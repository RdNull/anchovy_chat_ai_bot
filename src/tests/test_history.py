from datetime import datetime, timezone

from src.messages.repository import (
    get_active_chats, get_history, get_last_message, get_messages_count, get_messages_count_since,
    push_history, register_chat,
)
from src.models import (
    Message, MessageReply, UserRole,
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
