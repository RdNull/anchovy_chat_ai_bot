from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from src import mongo
from src.const import TIMEZONE_ALMATY
from src.memory.handlers import delete_old_memories
from src.memory.models import ChatState, DecayRecord, MemoryData, ParticipantInfo, StructuredMemory
from src.memory.repository import get_last_memory, save_memory
from src.messages.repository import get_messages, save_message
from src.models import Message, UserRole

TS = '26-05-01 10:00'
NOW = datetime(2026, 5, 1, 18, 0, tzinfo=TIMEZONE_ALMATY)


def make_memory_data(content: StructuredMemory, decay=None) -> MemoryData:
    return MemoryData(
        chat_id=1, created_at=NOW, content=content, decay=decay or {}
    )


async def test_save_and_get_last_memory():
    memory = StructuredMemory(
        participants={
            '@alice': ParticipantInfo(
                traits=['likes coffee', 'punctual'],
                recent=['talked about testing'],
            )
        },
        state=ChatState(
            active_topics=['testing', 'deployment'],
            open_questions=['when is the release?'],
            running_jokes=['the server is always down'],
        ),
    )
    decay = {'@alice': {'talked about testing': DecayRecord(born=TS, cycles=2, field='recent')}}
    await save_memory(1, memory, decay)

    result = await get_last_memory(1)
    assert result.chat_id == 1
    assert isinstance(result.created_at, datetime)
    assert result.content.participants['@alice'].traits == ['likes coffee', 'punctual']
    assert result.content.participants['@alice'].recent == ['talked about testing']
    assert result.decay['@alice']['talked about testing'].born == TS
    assert result.decay['@alice']['talked about testing'].cycles == 2
    assert result.content.state.active_topics == ['testing', 'deployment']
    assert result.content.state.open_questions == ['when is the release?']
    assert result.content.state.running_jokes == ['the server is always down']


async def test_get_last_memory_empty():
    result = await get_last_memory(1)
    assert result is None


async def test_get_last_memory_returns_newest():
    await save_memory(1, StructuredMemory(state=ChatState(active_topics=['first'])))
    await save_memory(1, StructuredMemory(state=ChatState(active_topics=['second'])))

    result = await get_last_memory(1)
    assert result.content.state.active_topics == ['second']


async def test_save_and_get_empty_memory():
    memory = StructuredMemory()
    await save_memory(1, memory)

    result = await get_last_memory(1)
    assert result.decay == {}
    assert result.content.participants == {}
    assert result.content.state.active_topics == []
    assert result.content.state.open_questions == []
    assert result.content.state.running_jokes == []


async def test_save_message_db_error(mocker):
    mock_mongo = mocker.patch('src.messages.repository.mongo')
    mock_mongo.messages.insert_one = AsyncMock(side_effect=Exception('DB error'))
    mocker.patch('src.messages.repository.logger')

    msg = Message(chat_id=123, nickname='n', role=UserRole.USER, text='t')
    with pytest.raises(Exception, match='DB error'):
        await save_message(msg)


async def test_get_messages_db_error(mocker):
    mock_mongo = mocker.patch('src.messages.repository.mongo')
    mock_mongo.messages.find.side_effect = Exception('DB find error')
    mocker.patch('src.messages.repository.logger')

    with pytest.raises(Exception, match='DB find error'):
        await get_messages(123)


# --- prompt_format unit tests (no DB) ---

def test_prompt_format_empty():
    assert make_memory_data(StructuredMemory()).prompt_format() == '=== ПАМЯТЬ ==='


def test_prompt_format_participants_traits_only():
    memory = make_memory_data(StructuredMemory(
        participants={'@bob': ParticipantInfo(traits=['сарказм', 'ночная сова'])}
    ))
    assert memory.prompt_format() == (
        '=== ПАМЯТЬ ===\n'
        'УЧАСТНИКИ:\n'
        '@bob\n'
        '  • сарказм\n'
        '  • ночная сова'
    )


def test_prompt_format_participants_with_recent():
    memory = make_memory_data(
        StructuredMemory(
            participants={
                '@alice': ParticipantInfo(traits=['любит кофе'], recent=['обсуждала деплой']),
            }
        ),
        decay={'@alice': {'обсуждала деплой': DecayRecord(born=TS, cycles=0, field='recent')}},
    )
    assert memory.prompt_format(now=NOW) == (
        '=== ПАМЯТЬ ===\n'
        'УЧАСТНИКИ:\n'
        '@alice\n'
        '  • любит кофе\n'
        '  recent:\n'
        '  - сегодня: обсуждала деплой'
    )


def test_prompt_format_recent_without_decay_record_renders_bare():
    """A record can be missing transiently; the reply path must not raise."""
    memory = make_memory_data(StructuredMemory(
        participants={'@alice': ParticipantInfo(recent=['обсуждала деплой'])}
    ))
    assert memory.prompt_format(now=NOW) == (
        '=== ПАМЯТЬ ===\n'
        'УЧАСТНИКИ:\n'
        '@alice\n'
        '  recent:\n'
        '  - обсуждала деплой'
    )


def test_prompt_format_relative_age_buckets():
    entries = ['сегодня', 'вчера', 'позавчера', 'пять дней', 'неделя', 'давно']
    born = [
        '26-05-01 10:00', '26-04-30 10:00', '26-04-29 10:00',
        '26-04-26 10:00', '26-04-24 10:00', '26-04-23 10:00',
    ]
    memory = make_memory_data(
        StructuredMemory(participants={'@alice': ParticipantInfo(recent=entries)}),
        decay={
            '@alice': {
                entry: DecayRecord(born=stamp, cycles=0, field='recent')
                for entry, stamp in zip(entries, born)
            }
        },
    )
    rendered = memory.prompt_format(now=NOW).splitlines()[-6:]
    assert rendered == [
        '  - сегодня: сегодня',
        '  - вчера: вчера',
        '  - 2 дня назад: позавчера',
        '  - 5 дней назад: пять дней',
        '  - 7 дней назад: неделя',
        '  - больше недели назад: давно',
    ]


def test_prompt_format_age_is_calendar_days_not_elapsed_hours():
    """23:59 yesterday is «вчера», not «сегодня», even 5 minutes ago."""
    memory = make_memory_data(
        StructuredMemory(participants={'@alice': ParticipantInfo(recent=['поздний созвон'])}),
        decay={'@alice': {'поздний созвон': DecayRecord(
            born='26-04-30 23:59', cycles=0, field='recent'
        )}},
    )
    now = datetime(2026, 5, 1, 0, 4, tzinfo=TIMEZONE_ALMATY)
    assert memory.prompt_format(now=now).endswith('  - вчера: поздний созвон')


def test_prompt_format_participant_no_traits_no_recent():
    memory = make_memory_data(StructuredMemory(participants={'@ghost': ParticipantInfo()}))
    assert memory.prompt_format() == (
        '=== ПАМЯТЬ ===\n'
        'УЧАСТНИКИ:\n'
        '@ghost'
    )


def test_prompt_format_state_sections():
    memory = make_memory_data(StructuredMemory(
        state=ChatState(
            active_topics=['деплой', 'тесты'],
            open_questions=['когда релиз?'],
            running_jokes=['сервер снова лежит'],
        )
    ))
    assert memory.prompt_format() == (
        '=== ПАМЯТЬ ===\n'
        '\nОБСУЖДАЕТСЯ:\n'
        '- деплой\n'
        '- тесты\n'
        '\nТЕКУЩИЕ ВОПРОСЫ:\n'
        '- когда релиз?\n'
        '\nТЕКУЩИЕ ШУТКИ:\n'
        '- сервер снова лежит'
    )


def test_prompt_format_empty_state_sections_omitted():
    memory = make_memory_data(StructuredMemory(state=ChatState(active_topics=['x'])))
    assert memory.prompt_format() == (
        '=== ПАМЯТЬ ===\n'
        '\nОБСУЖДАЕТСЯ:\n'
        '- x'
    )


# --- delete_old_memories ---

async def test_delete_old_memories_removes_superseded_stale_record():
    ten_days_ago = (datetime.now(timezone.utc) - timedelta(days=10)).timestamp()
    nine_days_ago = (datetime.now(timezone.utc) - timedelta(days=9)).timestamp()
    await mongo.memory.insert_one({
        'chat_id': 1, 'content': StructuredMemory().model_dump(), 'created_at': ten_days_ago,
    })
    await mongo.memory.insert_one({
        'chat_id': 1, 'content': StructuredMemory().model_dump(), 'created_at': nine_days_ago,
    })

    await delete_old_memories(retention_days=7)

    remaining = await mongo.memory.find({'chat_id': 1}).to_list(length=10)
    assert len(remaining) == 1
    assert remaining[0]['created_at'] == nine_days_ago


async def test_delete_old_memories_keeps_recent_records():
    one_day_ago = (datetime.now(timezone.utc) - timedelta(days=1)).timestamp()
    await mongo.memory.insert_one({
        'chat_id': 2, 'content': StructuredMemory().model_dump(), 'created_at': one_day_ago,
    })

    await delete_old_memories(retention_days=7)

    remaining = await mongo.memory.find({'chat_id': 2}).to_list(length=10)
    assert len(remaining) == 1


async def test_delete_old_memories_preserves_only_record_even_if_stale():
    thirty_days_ago = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
    await mongo.memory.insert_one({
        'chat_id': 3, 'content': StructuredMemory().model_dump(), 'created_at': thirty_days_ago,
    })

    await delete_old_memories(retention_days=7)

    remaining = await mongo.memory.find({'chat_id': 3}).to_list(length=10)
    assert len(remaining) == 1


async def test_delete_old_memories_respects_custom_retention_days():
    three_days_ago = (datetime.now(timezone.utc) - timedelta(days=3)).timestamp()
    one_day_ago = (datetime.now(timezone.utc) - timedelta(days=1)).timestamp()
    await mongo.memory.insert_one({
        'chat_id': 4, 'content': StructuredMemory().model_dump(), 'created_at': three_days_ago,
    })
    await mongo.memory.insert_one({
        'chat_id': 4, 'content': StructuredMemory().model_dump(), 'created_at': one_day_ago,
    })

    await delete_old_memories(retention_days=1)

    remaining = await mongo.memory.find({'chat_id': 4}).to_list(length=10)
    assert len(remaining) == 1
    assert remaining[0]['created_at'] == one_day_ago
