from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, call

from bson import Decimal128, ObjectId

from src import mongo
from src.embeddings.facts import FactsSearchResult
from src.facts.handlers import decay_all_facts, upsert_fact
from src.facts.processors import extract_facts
from src.facts.repository import create_fact, get_fact_by_id, get_facts, update_fact
from src.models import ExtractedFact, ExtractedFacts, UserFact
from src.tests.test_utils import make_message


def mock_facts_llm(mocker, return_value=None):
    result = return_value if return_value is not None else ExtractedFacts(facts=[])
    mock_llm = MagicMock()
    mock_ainvoke = AsyncMock(return_value=result)
    mock_llm.__or__.return_value.with_retry.return_value.ainvoke = mock_ainvoke
    mocker.patch('src.facts.processors.ai.get_facts_model', return_value=mock_llm)
    mocker.patch('src.facts.processors.prompt_manager.get_prompt', return_value='p')
    return mock_ainvoke


# --- save_fact ---

async def test_save_fact_persists_fields():
    fact = await create_fact(nickname='alice', text='likes coffee', confidence=0.9)

    assert fact.id is not None
    assert fact.nickname == 'alice'
    assert fact.text == 'likes coffee'
    assert fact.confidence == 0.9
    assert fact.created_at is not None
    assert fact.updated_at is not None
    assert isinstance(fact, UserFact)


async def test_save_fact_sets_updated_at():
    fact = await create_fact(nickname='alice', text='likes coffee', confidence=0.9)

    stored = await mongo.facts.find_one({'_id': ObjectId(fact.id)})
    assert stored['updated_at'] is not None
    assert stored['updated_at'] == stored['created_at']


# --- get_facts ---

async def test_get_facts_returns_saved():
    await create_fact('alice', 'likes coffee', 0.9)

    facts = await get_facts('alice')
    assert len(facts) == 1
    assert facts[0].nickname == 'alice'
    assert facts[0].text == 'likes coffee'
    assert facts[0].confidence == 0.9


async def test_get_facts_sorted_by_confidence():
    await create_fact('alice', 'low confidence fact', 0.3)
    await create_fact('alice', 'high confidence fact', 0.9)
    await create_fact('alice', 'mid confidence fact', 0.6)

    facts = await get_facts('alice')
    assert facts[0].confidence == 0.9
    assert facts[1].confidence == 0.6
    assert facts[2].confidence == 0.3


async def test_get_facts_respects_limit():
    for i in range(5):
        await create_fact('alice', f'fact {i}', float(i) / 10)

    facts = await get_facts('alice', limit=3)
    assert len(facts) == 3


async def test_get_facts_isolates_by_nickname():
    await create_fact('alice', 'alice fact', 0.9)
    await create_fact('bob', 'bob fact', 0.8)

    alice_facts = await get_facts('alice')
    assert len(alice_facts) == 1
    assert alice_facts[0].nickname == 'alice'


async def test_get_facts_unknown_user_returns_empty():
    result = await get_facts('nobody')
    assert result == []


# --- get_fact_by_id ---

async def test_get_fact_by_id_returns_fact():
    fact = await create_fact('alice', 'likes coffee', 0.9)

    result = await get_fact_by_id(fact.id)

    assert result is not None
    assert result.id == fact.id
    assert result.nickname == 'alice'
    assert result.text == 'likes coffee'
    assert result.confidence == 0.9


async def test_get_fact_by_id_returns_none_for_unknown_id():
    result = await get_fact_by_id(str(ObjectId()))

    assert result is None


# --- update_fact ---

async def test_update_fact_refreshes_updated_at():
    fact = await create_fact('alice', 'likes coffee', 0.8)
    stored_before = await mongo.facts.find_one({'_id': ObjectId(fact.id)})
    old_updated_at = stored_before['updated_at']

    await update_fact(fact.id, confidence=0.9)

    stored_after = await mongo.facts.find_one({'_id': ObjectId(fact.id)})
    assert stored_after
    assert stored_after['updated_at'] >= old_updated_at
    assert stored_after['created_at'] == stored_before['created_at']
    assert stored_after['confidence'] == 0.9


# --- upsert_fact ---

async def test_upsert_fact_creates_new_when_no_similar(mocker):
    mocker.patch('src.embeddings.facts.facts_embedding_client.search_facts', return_value=[])
    mock_save = mocker.patch('src.embeddings.facts.facts_embedding_client.save_fact')

    await upsert_fact('alice', 'likes coffee', 0.8)
    facts = await get_facts('alice')

    assert len(facts) == 1
    assert facts[0].nickname == 'alice'
    assert facts[0].text == 'likes coffee'
    assert facts[0].confidence == 0.8
    assert mock_save.call_count == 1
    assert mock_save.call_args == call(facts[0])


async def test_upsert_fact_skips_low_confidence(mocker):
    mock_search = mocker.patch(
        'src.embeddings.facts.facts_embedding_client.search_facts',
        return_value=[]
    )
    mock_save = mocker.patch('src.embeddings.facts.facts_embedding_client.save_fact')

    await upsert_fact('alice', 'maybe likes coffee', 0.4)

    assert mock_search.call_count == 0
    assert mock_save.call_count == 0
    facts = await get_facts('alice')
    assert len(facts) == 0


async def test_upsert_fact_strips_at_prefix(mocker):
    mocker.patch(
        'src.embeddings.facts.facts_embedding_client.search_facts', return_value=[]
    )
    mock_save = mocker.patch('src.embeddings.facts.facts_embedding_client.save_fact')

    await upsert_fact('@alice', 'likes coffee', 0.8)

    facts = await get_facts('alice')

    assert len(facts) == 1
    assert facts[0].nickname == 'alice'
    assert facts[0].text == 'likes coffee'
    assert facts[0].confidence == 0.8
    assert mock_save.call_count == 1
    assert mock_save.call_args == call(facts[0])


async def test_upsert_fact_reinforces_existing_higher_confidence(mocker):
    existing = await create_fact('alice', 'likes coffee', 0.9)
    similar = FactsSearchResult(fact=existing, score=0.85)

    mocker.patch(
        'src.embeddings.facts.facts_embedding_client.search_facts', return_value=[similar]
    )
    mock_save = mocker.patch('src.embeddings.facts.facts_embedding_client.save_fact')

    await upsert_fact('alice', 'loves coffee', 0.7)

    stored = await mongo.facts.find_one({'_id': ObjectId(existing.id)})

    assert stored
    assert stored['confidence'] == min(0.9 + 0.1, 1)
    assert stored['text'] == 'likes coffee'
    assert mock_save.call_count == 0


async def test_upsert_fact_updates_existing_lower_confidence(mocker):
    existing = await create_fact('alice', 'likes coffee', 0.6)
    similar = FactsSearchResult(fact=existing, score=0.85)

    mocker.patch(
        'src.embeddings.facts.facts_embedding_client.search_facts', return_value=[similar]
    )
    mock_save = mocker.patch('src.embeddings.facts.facts_embedding_client.save_fact')

    await upsert_fact('alice', 'loves coffee', 0.9)

    stored = await mongo.facts.find_one({'_id': ObjectId(existing.id)})
    assert stored['confidence'] == 0.9
    assert stored['text'] == 'loves coffee'
    assert mock_save.call_count == 0


# --- decay_all_facts ---

async def test_decay_reduces_confidence_for_stale_facts():
    old_ts = (datetime.now(timezone.utc) - timedelta(weeks=2)).timestamp()
    await mongo.facts.insert_one({
        'nickname': 'alice',
        'text': 'likes coffee',
        'confidence': 0.8,
        'created_at': old_ts,
        'updated_at': old_ts,
    })

    await decay_all_facts()

    facts = await get_facts('alice')
    assert len(facts) == 1
    assert round(facts[0].confidence, 2) == 0.7


async def test_decay_deletes_fact_when_confidence_reaches_zero():
    old_ts = (datetime.now(timezone.utc) - timedelta(weeks=2)).timestamp()
    await mongo.facts.insert_one({
        'nickname': 'alice',
        'text': 'old fact',
        'confidence': 0.1,
        'created_at': old_ts,
        'updated_at': old_ts,
    })

    await decay_all_facts()

    facts = await get_facts('alice')
    assert len(facts) == 0


async def test_decay_skips_recently_updated_facts():
    recent_ts = datetime.now(timezone.utc).timestamp()
    await mongo.facts.insert_one({
        'nickname': 'alice',
        'text': 'fresh fact',
        'confidence': 0.8,
        'created_at': recent_ts,
        'updated_at': recent_ts,
    })

    await decay_all_facts()

    facts = await get_facts('alice')
    assert len(facts) == 1
    assert facts[0].confidence == 0.8


async def test_decay_falls_back_to_created_at_when_no_updated_at():
    old_ts = (datetime.now(timezone.utc) - timedelta(weeks=2)).timestamp()
    await mongo.facts.insert_one({
        'nickname': 'alice',
        'text': 'legacy fact',
        'confidence': 0.8,
        'created_at': old_ts,
    })

    await decay_all_facts()

    facts = await get_facts('alice')
    assert len(facts) == 1
    assert round(facts[0].confidence, 2) == 0.7


# --- UserFact model validation ---

def test_user_fact_model_validate_decimal_confidence():
    data = {
        '_id': ObjectId(),
        'nickname': 'alice',
        'text': 'likes coffee',
        'confidence': Decimal('0.9'),
        'created_at': None,
    }
    fact = UserFact.model_validate(data)
    assert isinstance(fact.confidence, float)
    assert fact.confidence == 0.9


async def test_get_facts_handles_decimal128_stored_in_mongo():
    await mongo.facts.insert_one({
        'nickname': 'alice',
        'text': 'likes coffee',
        'confidence': Decimal128('0.9'),
        'created_at': datetime.now(timezone.utc).timestamp(),
    })
    facts = await get_facts('alice')
    assert len(facts) == 1
    assert isinstance(facts[0].confidence, float)
    assert facts[0].confidence == 0.9


# --- extract_facts (processor) ---

async def test_extract_facts_upserts_each_fact(mocker):
    facts = ExtractedFacts(facts=[
        ExtractedFact(nickname='alice', text='likes coffee', confidence=0.9),
        ExtractedFact(nickname='bob', text='hates mornings', confidence=0.7),
    ])
    mock_facts_llm(mocker, return_value=facts)
    mock_upsert = mocker.patch('src.facts.processors.upsert_fact')

    await extract_facts([make_message()])

    assert mock_upsert.call_count == 2
    assert mock_upsert.call_args_list[0] == call('alice', 'likes coffee', 0.9)
    assert mock_upsert.call_args_list[1] == call('bob', 'hates mornings', 0.7)


async def test_extract_facts_empty_result_skips_upsert(mocker):
    mock_facts_llm(mocker, return_value=ExtractedFacts(facts=[]))
    mock_upsert = mocker.patch('src.facts.processors.upsert_fact')

    await extract_facts([make_message()])

    assert mock_upsert.call_count == 0


async def test_extract_facts_logs_error_on_exception(mocker):
    mock_llm = MagicMock()
    mock_llm.__or__.return_value.with_retry.return_value.ainvoke = AsyncMock(
        side_effect=Exception('LLM failure')
    )
    mocker.patch('src.facts.processors.ai.get_facts_model', return_value=mock_llm)
    mocker.patch('src.facts.processors.prompt_manager.get_prompt', return_value='p')
    mock_logger = mocker.patch('src.facts.processors.logger')

    await extract_facts([make_message()])

    assert mock_logger.error.call_count == 1
    assert 'Error extracting facts from messages' in mock_logger.error.call_args[0][0]
