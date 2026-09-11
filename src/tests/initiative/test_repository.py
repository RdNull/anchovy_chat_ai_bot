from datetime import datetime, timedelta, timezone

from src import mongo
from src.initiative.repository import get_last_initiative_run, save_initiative_run


async def test_get_last_initiative_run_returns_none_when_no_runs():
    result = await get_last_initiative_run(222)

    assert result is None


async def test_save_initiative_run_round_trips():
    last_message_time = datetime.now(timezone.utc).replace(microsecond=0)

    await save_initiative_run(222, last_message_time=last_message_time)
    result = await get_last_initiative_run(222)

    assert result is not None
    assert result.chat_id == 222
    assert result.last_message_time == last_message_time
    assert result.created_at is not None


async def test_get_last_initiative_run_returns_the_most_recent():
    older = datetime.now(timezone.utc) - timedelta(minutes=10)
    newer = datetime.now(timezone.utc)
    await mongo.initiative_runs.insert_one({
        'chat_id': 222, 'last_message_time': older.timestamp(), 'created_at': older.timestamp(),
    })
    await mongo.initiative_runs.insert_one({
        'chat_id': 222, 'last_message_time': newer.timestamp(), 'created_at': newer.timestamp(),
    })

    result = await get_last_initiative_run(222)

    assert result.created_at == newer


async def test_get_last_initiative_run_is_scoped_to_chat():
    now = datetime.now(timezone.utc)
    await mongo.initiative_runs.insert_one({
        'chat_id': 111, 'last_message_time': now.timestamp(), 'created_at': now.timestamp(),
    })

    result = await get_last_initiative_run(222)

    assert result is None
