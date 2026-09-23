from datetime import datetime, timedelta, UTC

from src import mongo
from src.initiative.repository import (
    count_replied_since,
    get_last_initiative_run,
    mark_initiative_replied,
    save_initiative_run,
)


async def test_get_last_initiative_run_returns_none_when_no_runs():
    result = await get_last_initiative_run(222)

    assert result is None


async def test_save_initiative_run_round_trips():
    last_message_time = datetime.now(UTC).replace(microsecond=0)

    await save_initiative_run(222, last_message_time=last_message_time)
    result = await get_last_initiative_run(222)

    assert result is not None
    assert result.chat_id == 222
    assert result.last_message_time == last_message_time
    assert result.created_at is not None


async def test_get_last_initiative_run_returns_the_most_recent():
    older = datetime.now(UTC) - timedelta(minutes=10)
    newer = datetime.now(UTC)
    await mongo.initiative_runs.insert_one({
        'chat_id': 222,
        'last_message_time': older.timestamp(),
        'created_at': older.timestamp(),
    })
    await mongo.initiative_runs.insert_one({
        'chat_id': 222,
        'last_message_time': newer.timestamp(),
        'created_at': newer.timestamp(),
    })

    result = await get_last_initiative_run(222)

    assert result.created_at == newer


async def test_get_last_initiative_run_is_scoped_to_chat():
    now = datetime.now(UTC)
    await mongo.initiative_runs.insert_one({
        'chat_id': 111,
        'last_message_time': now.timestamp(),
        'created_at': now.timestamp(),
    })

    result = await get_last_initiative_run(222)

    assert result is None


async def test_save_initiative_run_returns_a_usable_id():
    run_id = await save_initiative_run(222, last_message_time=datetime.now(UTC))

    await mark_initiative_replied(run_id)
    result = await get_last_initiative_run(222)

    assert result.id == run_id
    assert result.replied_at is not None


# --- mark_initiative_replied / count_replied_since ---


async def test_mark_initiative_replied_stamps_the_run():
    run_id = await save_initiative_run(222, last_message_time=datetime.now(UTC))

    await mark_initiative_replied(run_id)

    result = await get_last_initiative_run(222)
    assert result.replied_at is not None


async def test_count_replied_since_counts_only_replied_runs_for_this_chat_within_the_window():
    now = datetime.now(UTC)
    # Never sent — no replied_at.
    await mongo.initiative_runs.insert_one({
        'chat_id': 222,
        'last_message_time': now.timestamp(),
        'created_at': now.timestamp(),
    })
    # Sent, but before the window.
    stale = now - timedelta(hours=25)
    await mongo.initiative_runs.insert_one({
        'chat_id': 222,
        'last_message_time': stale.timestamp(),
        'created_at': stale.timestamp(),
        'replied_at': stale.timestamp(),
    })
    # Sent within the window, but a different chat.
    await mongo.initiative_runs.insert_one({
        'chat_id': 111,
        'last_message_time': now.timestamp(),
        'created_at': now.timestamp(),
        'replied_at': now.timestamp(),
    })
    # Sent within the window, this chat — the only one that should count.
    await mongo.initiative_runs.insert_one({
        'chat_id': 222,
        'last_message_time': now.timestamp(),
        'created_at': now.timestamp(),
        'replied_at': now.timestamp(),
    })

    assert await count_replied_since(222, timedelta(hours=24)) == 1


async def test_count_replied_since_respects_a_different_window():
    # The window is a parameter, not a baked-in constant — a run just outside a 1h
    # window must not count even though it would count against the 24h one above.
    now = datetime.now(UTC)
    two_hours_ago = now - timedelta(hours=2)
    await mongo.initiative_runs.insert_one({
        'chat_id': 222,
        'last_message_time': two_hours_ago.timestamp(),
        'created_at': two_hours_ago.timestamp(),
        'replied_at': two_hours_ago.timestamp(),
    })

    assert await count_replied_since(222, timedelta(hours=1)) == 0
    assert await count_replied_since(222, timedelta(hours=24)) == 1


async def test_count_replied_since_is_zero_with_no_runs():
    assert await count_replied_since(222, timedelta(hours=24)) == 0
