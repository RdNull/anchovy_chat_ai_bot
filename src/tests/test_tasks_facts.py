from datetime import datetime, timedelta, UTC
from unittest.mock import AsyncMock

import pytest

from src import mongo, settings
from src.facts.repository import get_fact_by_id
from src.tasks.facts import expire_candidates, run_candidate_expiry


@pytest.fixture(autouse=True)
def embeddings(mocker):
    client = mocker.patch('src.tasks.facts.facts_embedding_client')
    client.delete_fact = AsyncMock()
    return client


async def insert_fact(text, status, days_ago):
    seen = datetime.now(UTC) - timedelta(days=days_ago)
    result = await mongo.facts.insert_one({
        'nickname': 'alice',
        'kind': 'habit',
        'status': status,
        'text': text,
        'sightings': ['2026-09-01'],
        'created_at': seen,
        'last_seen_at': seen,
    })
    return str(result.inserted_id)


async def test_expiry_deletes_only_stale_candidates(embeddings):
    stale = settings.FACTS_CANDIDATE_TTL_DAYS + 5
    stale_candidate = await insert_fact('stale candidate', 'candidate', stale)
    stale_confirmed = await insert_fact('stale confirmed', 'confirmed', stale * 10)
    fresh_candidate = await insert_fact('fresh candidate', 'candidate', 1)

    count = await expire_candidates()

    assert count == 1
    assert await get_fact_by_id(stale_candidate) is None
    assert await get_fact_by_id(stale_confirmed) is not None
    assert await get_fact_by_id(fresh_candidate) is not None
    assert [c.args[0] for c in embeddings.delete_fact.call_args_list] == [stale_candidate]


async def test_run_candidate_expiry_calls_expiry(mocker):
    mock_expire = mocker.patch('src.tasks.facts.expire_candidates', AsyncMock(return_value=0))

    await run_candidate_expiry()

    assert mock_expire.call_count == 1


async def test_run_candidate_expiry_handles_errors(mocker):
    mocker.patch(
        'src.tasks.facts.expire_candidates', AsyncMock(side_effect=RuntimeError('db error'))
    )
    mock_logger = mocker.patch('src.tasks.facts.logger')

    await run_candidate_expiry()

    assert mock_logger.error.call_count == 1
    assert mock_logger.error.call_args.kwargs['extra'] == {'event': 'TASK_FAILED'}
    assert mock_logger.error.call_args.kwargs['exc_info'] is True
