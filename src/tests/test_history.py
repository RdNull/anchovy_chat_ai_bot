from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.messages.history import (
    get_active_chats, get_history, get_last_memory, get_last_message,
    get_messages_count, get_messages_count_since, push_history, register_chat, save_memory,
)
from src.models import (
    Decision, Fact, Message, MessageReply, OpenLoop, ParticipantInfo,
    StructuredMemory, Topic, UserRole,
)


def make_message(chat_id=1, role=UserRole.USER, text='hello', nickname='user1'):
    return Message(chat_id=chat_id, role=role, text=text, nickname=nickname)


# --- push_history ---

async def test_push_history_persists_fields():
    reply = MessageReply(text='quoted text', nickname='other_user')
    msg = Message(
        chat_id=42,
        role=UserRole.USER,
        text='test message',
        nickname='tester',
        reply=reply
    )
    assert msg.id is None
    await push_history(msg)

    fetched = await get_last_message(42)
    assert fetched.id is not None
    assert fetched.chat_id == 42
    assert fetched.nickname == 'tester'
    assert fetched.role == UserRole.USER
    assert fetched.text == 'test message'
    assert isinstance(fetched.created_at, datetime)
    assert fetched.reply is not None
    assert fetched.reply.text == 'quoted text'
    assert fetched.reply.nickname == 'other_user'


# --- get_history ---

async def test_get_history_order():
    await push_history(make_message(text='first'))
    await push_history(make_message(text='second'))
    history = await get_history(1)
    assert len(history) == 2
    assert history[0].text == 'first'
    assert history[1].text == 'second'


async def test_get_history_from_date():
    await push_history(make_message(text='old'))
    cutoff = datetime.now(timezone.utc)
    await push_history(make_message(text='new'))
    history = await get_history(1, from_date=cutoff)
    assert len(history) == 1
    assert history[0].text == 'new'


async def test_get_history_size_limit():
    for i in range(3):
        await push_history(make_message(text=f'msg{i}'))

    history = await get_history(1, size=2)
    assert len(history) == 2


# --- get_last_message ---

async def test_get_last_message():
    await push_history(make_message(role=UserRole.USER, text='user msg'))
    await push_history(make_message(role=UserRole.AI, text='ai msg'))
    msg = await get_last_message(1)
    assert msg.text == 'ai msg'
    assert msg.role == UserRole.AI


async def test_get_last_message_role_filter():
    await push_history(make_message(role=UserRole.USER, text='user msg'))
    await push_history(make_message(role=UserRole.AI, text='ai msg'))
    msg = await get_last_message(1, role=UserRole.USER)
    assert msg.text == 'user msg'
    assert msg.role == UserRole.USER


async def test_get_last_message_empty():
    result = await get_last_message(1)
    assert result is None


# --- get_messages_count / get_messages_count_since ---

async def test_get_messages_count():
    for _ in range(3):
        await push_history(make_message())

    assert await get_messages_count(1) == 3


async def test_get_messages_count_since():
    await push_history(make_message(text='old1'))
    await push_history(make_message(text='old2'))
    cutoff = datetime.now(timezone.utc)
    await push_history(make_message(text='recent'))

    assert await get_messages_count_since(1, cutoff.timestamp()) == 1


# --- register_chat / get_active_chats ---

async def test_register_chat_upsert():
    await register_chat(42)
    await register_chat(42)

    chats = await get_active_chats()
    assert chats.count(42) == 1


async def test_get_active_chats():
    await register_chat(1)
    await register_chat(2)

    chats = await get_active_chats()
    assert 1 in chats
    assert 2 in chats


# --- save_memory / get_last_memory ---

async def test_save_and_get_last_memory():
    memory = StructuredMemory(
        facts=[Fact(text='the sky is blue', confidence=0.9)],
        decisions=[Decision(text='use Python', status='done')],
        topics=[Topic(name='testing', status='active')],
        open_loops=[OpenLoop(text='add more tests', priority='high')],
        participants={'alice': ParticipantInfo(facts=['likes coffee'])},
        constraints=['no mocking DB'],
        preferences=['fast tests'],
    )
    await save_memory(1, memory)

    result = await get_last_memory(1)
    assert result.chat_id == 1
    assert isinstance(result.created_at, datetime)
    assert result.content.facts[0].text == 'the sky is blue'
    assert result.content.facts[0].confidence == 0.9
    assert result.content.decisions[0].text == 'use Python'
    assert result.content.decisions[0].status == 'done'
    assert result.content.topics[0].name == 'testing'
    assert result.content.open_loops[0].text == 'add more tests'
    assert result.content.open_loops[0].priority == 'high'
    assert result.content.participants['alice'].facts == ['likes coffee']
    assert result.content.constraints == ['no mocking DB']
    assert result.content.preferences == ['fast tests']


async def test_get_last_memory_empty():
    result = await get_last_memory(1)
    assert result is None


async def test_get_last_memory_returns_newest():
    await save_memory(1, StructuredMemory(constraints=['first']))
    await save_memory(1, StructuredMemory(constraints=['second']))

    result = await get_last_memory(1)
    assert result.content.constraints == ['second']


async def test_push_history_db_error(mocker):
    # Mocking mongo.messages in src.messages.history
    mock_mongo = mocker.patch("src.messages.history.mongo")
    mock_mongo.messages.insert_one = AsyncMock(side_effect=Exception("DB error"))
    mock_logger = mocker.patch("src.messages.history.logger")

    msg = Message(chat_id=123, nickname="n", role=UserRole.USER, text="t")
    # history.py doesn't have a try-except for insert_one, so it will raise
    with pytest.raises(Exception, match="DB error"):
        await push_history(msg)


async def test_get_history_db_error(mocker):
    mock_mongo = mocker.patch("src.messages.history.mongo")
    mock_mongo.messages.find.side_effect = Exception("DB find error")
    mock_logger = mocker.patch("src.messages.history.logger")

    with pytest.raises(Exception, match="DB find error"):
        await get_history(123)


async def test_register_chat_db_error(mocker):
    mock_mongo = mocker.patch("src.messages.history.mongo")
    mock_mongo.chats.update_one = AsyncMock(side_effect=Exception("DB update error"))
    mock_logger = mocker.patch("src.messages.history.logger")

    with pytest.raises(Exception, match="DB update error"):
        await register_chat(123)
