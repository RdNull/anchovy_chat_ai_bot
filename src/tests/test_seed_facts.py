import io
import json
from unittest.mock import AsyncMock

import pytest

from src import mongo
from src.facts.models import FactStatus
from src.facts.repository import get_facts
from src.scripts.seed_facts import describe, read_seed, seed_facts

SEED = [
    {'nickname': 'alice', 'kind': 'bio', 'text': 'живёт в Алматы'},
    {'nickname': '@alice', 'kind': 'habit', 'text': 'играет в CS'},
    {'nickname': 'bob', 'kind': 'joke', 'text': 'вечно опаздывает'},
]


@pytest.fixture
def embeddings(mocker):
    client = mocker.patch('src.scripts.seed_facts.facts_embedding_client')
    client.save_fact = AsyncMock()
    client.qdrant_client.collection_exists = AsyncMock(return_value=True)
    client.qdrant_client.delete_collection = AsyncMock()
    client.collection_name = 'facts'
    return client


def test_read_seed_from_stdin_strips_at_and_describes(mocker):
    mocker.patch('sys.stdin', io.StringIO(json.dumps(SEED)))

    seed = read_seed('-')

    assert [f.nickname for f in seed] == ['alice', 'alice', 'bob']
    assert (
        describe(seed) == "3 facts across 2 nicknames, by kind: {'bio': 1, 'habit': 1, 'joke': 1}"
    )


def test_read_seed_rejects_unknown_kind(mocker):
    bad = [{'nickname': 'a', 'kind': 'trait', 'text': 'x'}]
    mocker.patch('sys.stdin', io.StringIO(json.dumps(bad)))

    with pytest.raises(ValueError, match='kind'):
        read_seed('-')


async def test_dry_run_writes_nothing(mocker, embeddings, capsys):
    mocker.patch('sys.stdin', io.StringIO(json.dumps(SEED)))
    await mongo.facts.insert_one({'nickname': 'keep', 'text': 'me'})

    await seed_facts(read_seed('-'), dry_run=True)

    assert await mongo.facts.count_documents({}) == 1
    assert embeddings.save_fact.call_count == 0
    assert embeddings.qdrant_client.delete_collection.call_count == 0
    assert '3 facts across 2 nicknames' in capsys.readouterr().out


async def test_real_run_replaces_store_with_confirmed_facts(mocker, embeddings):
    mocker.patch('sys.stdin', io.StringIO(json.dumps(SEED)))
    await mongo.facts.insert_one({'nickname': 'old', 'text': 'legacy', 'confidence': 0.9})

    await seed_facts(read_seed('-'), dry_run=False)

    assert await mongo.facts.count_documents({}) == 3
    alice = await get_facts('alice')
    assert {f.status for f in alice} == {FactStatus.CONFIRMED}
    assert all(len(f.sightings) == 1 for f in alice)
    assert await get_facts('old') == []
    assert embeddings.save_fact.call_count == 3
    assert embeddings.qdrant_client.delete_collection.call_args.args == ('facts',)
