from datetime import datetime, timezone

from src import mongo
from src.messages.repository import (
    get_active_chats, get_last_message, get_message_by_tg_id, get_messages, get_messages_count,
    get_messages_count_since,
    register_chat, save_message, update_message,
)
from src.models import (
    Message, MessageReply, UpdateMessage, UserRole,
)


def make_message(chat_id=1, telegram_id=404, role=UserRole.USER, text='hello', nickname='user1'):
    return Message(
        chat_id=chat_id,
        telegram_id=telegram_id,
        role=role,
        text=text,
        nickname=nickname
    )


# --- save_message ---

async def test_save_message_persists_fields():
    replied_msg = Message(
        chat_id=42,
        telegram_id=405,
        role=UserRole.USER,
        text='quoted text',
        nickname='other_user',
    )
    await save_message(replied_msg)

    msg = Message(
        chat_id=42,
        telegram_id=404,
        role=UserRole.USER,
        text='test message',
        nickname='tester',
        reply=MessageReply(
            telegram_id=405,
            text='quoted text',
            nickname='other_user',
        )
    )
    assert msg.id is None
    await save_message(msg)

    fetched = await get_last_message(42)
    assert fetched.id is not None
    assert fetched.chat_id == 42
    assert fetched.telegram_id == 404
    assert fetched.nickname == 'tester'
    assert fetched.role == UserRole.USER
    assert fetched.text == 'test message'
    assert isinstance(fetched.created_at, datetime)
    assert fetched.reply is not None
    assert fetched.reply.text == 'quoted text'
    assert fetched.reply.nickname == 'other_user'
    assert fetched.reply.telegram_id == 405


async def test_save_message_reply_skipped_when_original_not_in_db():
    msg = Message(
        chat_id=42,
        telegram_id=404,
        role=UserRole.USER,
        text='test message',
        nickname='tester',
        reply=MessageReply(
            telegram_id=999,
            text='original text not in db',
            nickname='ghost_user',
        )
    )
    await save_message(msg)

    fetched = await get_last_message(42)
    assert fetched.reply is None


async def test_parse_old_format_reply():
    old_doc = {
        'chat_id': 42,
        'telegram_id': 404,
        'role': 'user',
        'text': 'test message',
        'nickname': 'tester',
        'reply_telegram_id': 405,
        'reply_text': 'old format reply',
        'reply_nickname': 'old_user',
        'reply_media_id': None,
        'reply_media_unique_id': None,
        'created_at': datetime.now(timezone.utc).timestamp(),
    }
    await mongo.messages.insert_one(old_doc)

    fetched = await get_last_message(42)
    assert fetched.reply is not None
    assert fetched.reply.text == 'old format reply'
    assert fetched.reply.nickname == 'old_user'
    assert fetched.reply.telegram_id == 405


# --- get_messages ---

async def test_get_messages_order():
    await save_message(make_message(text='first'))
    await save_message(make_message(text='second'))
    history = await get_messages(1)
    assert len(history) == 2
    assert history[0].text == 'first'
    assert history[1].text == 'second'


async def test_get_messages_from_date():
    await save_message(make_message(text='old'))
    cutoff = datetime.now(timezone.utc)
    await save_message(make_message(text='new'))
    history = await get_messages(1, from_date=cutoff)
    assert len(history) == 1
    assert history[0].text == 'new'


async def test_get_messages_size_limit():
    for i in range(3):
        await save_message(make_message(text=f'msg{i}'))

    history = await get_messages(1, size=2)
    assert len(history) == 2


# --- get_last_message ---

async def test_get_last_message():
    await save_message(make_message(role=UserRole.USER, text='user msg'))
    await save_message(make_message(role=UserRole.AI, text='ai msg'))
    msg = await get_last_message(1)
    assert msg.text == 'ai msg'
    assert msg.role == UserRole.AI


async def test_get_last_message_role_filter():
    await save_message(make_message(role=UserRole.USER, text='user msg'))
    await save_message(make_message(role=UserRole.AI, text='ai msg'))
    msg = await get_last_message(1, role=UserRole.USER)
    assert msg.text == 'user msg'
    assert msg.role == UserRole.USER


async def test_get_last_message_empty():
    result = await get_last_message(1)
    assert result is None


async def test_get_message_by_telegram_id():
    message = make_message(chat_id=101, telegram_id=8008)
    await save_message(message)

    fetched_message = await get_message_by_tg_id(message.chat_id, telegram_id=message.telegram_id)

    assert fetched_message
    assert fetched_message.chat_id == message.chat_id
    assert fetched_message.telegram_id == message.telegram_id


# --- get_messages_count / get_messages_count_since ---

async def test_get_messages_count():
    for _ in range(3):
        await save_message(make_message())

    assert await get_messages_count(1) == 3


async def test_get_messages_count_since():
    await save_message(make_message(text='old1'))
    await save_message(make_message(text='old2'))
    cutoff = datetime.now(timezone.utc)
    await save_message(make_message(text='recent'))

    assert await get_messages_count_since(1, cutoff.timestamp()) == 1


async def test_message_update():
    old_message = make_message()
    await save_message(old_message)

    update_data = UpdateMessage(
        id=old_message.id,
        text='updated text',
    )
    await update_message(update_data)

    assert await get_messages_count(old_message.chat_id) == 1

    fetched_message = await get_last_message(old_message.chat_id)
    assert fetched_message
    assert fetched_message.text == 'updated text'


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
