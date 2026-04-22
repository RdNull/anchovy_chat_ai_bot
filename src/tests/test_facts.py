from datetime import datetime, timezone
from decimal import Decimal

from bson import Decimal128, ObjectId

from src import mongo
from src.models import UserFact
from src.processors.context.facts import get_facts, save_fact


# --- save_fact ---

async def test_save_fact_persists_fields():
    fact = await save_fact(nickname='alice', text='likes coffee', confidence=0.9)

    assert fact.id is not None
    assert fact.nickname == 'alice'
    assert fact.text == 'likes coffee'
    assert fact.confidence == 0.9
    assert isinstance(fact, UserFact)


# --- get_facts ---

async def test_get_facts_returns_saved():
    await save_fact('alice', 'likes coffee', 0.9)

    facts = await get_facts('alice')
    assert len(facts) == 1
    assert facts[0].nickname == 'alice'
    assert facts[0].text == 'likes coffee'
    assert facts[0].confidence == 0.9


async def test_get_facts_sorted_by_confidence():
    await save_fact('alice', 'low confidence fact', 0.3)
    await save_fact('alice', 'high confidence fact', 0.9)
    await save_fact('alice', 'mid confidence fact', 0.6)

    facts = await get_facts('alice')
    assert facts[0].confidence == 0.9
    assert facts[1].confidence == 0.6
    assert facts[2].confidence == 0.3


async def test_get_facts_respects_limit():
    for i in range(5):
        await save_fact('alice', f'fact {i}', float(i) / 10)

    facts = await get_facts('alice', limit=3)
    assert len(facts) == 3


async def test_get_facts_isolates_by_nickname():
    await save_fact('alice', 'alice fact', 0.9)
    await save_fact('bob', 'bob fact', 0.8)

    alice_facts = await get_facts('alice')
    assert len(alice_facts) == 1
    assert alice_facts[0].nickname == 'alice'


async def test_get_facts_unknown_user_returns_empty():
    result = await get_facts('nobody')
    assert result == []


# --- UserFact model validation ---

def test_user_fact_model_validate_decimal_confidence():
    # Decimal128 is converted to Decimal by the mongo codec; pydantic coerces Decimal -> float
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
