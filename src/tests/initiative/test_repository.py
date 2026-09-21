from datetime import datetime, timedelta, timezone

from src import mongo
from src.initiative.repository import (
    count_replied_last_24h, get_last_initiative_run, mark_initiative_replied,
    save_initiative_run,
)


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


async def test_save_initiative_run_returns_a_usable_id():
    run_id = await save_initiative_run(222, last_message_time=datetime.now(timezone.utc))

    await mark_initiative_replied(run_id)
    result = await get_last_initiative_run(222)

    assert result.id == run_id
    assert result.replied_at is not None


# --- mark_initiative_replied / count_replied_last_24h ---

async def test_mark_initiative_replied_stamps_the_run():
    run_id = await save_initiative_run(222, last_message_time=datetime.now(timezone.utc))

    await mark_initiative_replied(run_id)

    result = await get_last_initiative_run(222)
    assert result.replied_at is not None


async def test_count_replied_last_24h_counts_only_replied_runs_for_this_chat():
    now = datetime.now(timezone.utc)
    # Never sent — no replied_at.
    await mongo.initiative_runs.insert_one({
        'chat_id': 222, 'last_message_time': now.timestamp(), 'created_at': now.timestamp(),
    })
    # Sent, but more than 24h ago.
    stale = now - timedelta(hours=25)
    await mongo.initiative_runs.insert_one({
        'chat_id': 222, 'last_message_time': stale.timestamp(), 'created_at': stale.timestamp(),
        'replied_at': stale.timestamp(),
    })
    # Sent within the window, but a different chat.
    await mongo.initiative_runs.insert_one({
        'chat_id': 111, 'last_message_time': now.timestamp(), 'created_at': now.timestamp(),
        'replied_at': now.timestamp(),
    })
    # Sent within the window, this chat — the only one that should count.
    await mongo.initiative_runs.insert_one({
        'chat_id': 222, 'last_message_time': now.timestamp(), 'created_at': now.timestamp(),
        'replied_at': now.timestamp(),
    })

    assert await count_replied_last_24h(222) == 1


async def test_count_replied_last_24h_is_zero_with_no_runs():
    assert await count_replied_last_24h(222) == 0
