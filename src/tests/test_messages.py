from datetime import datetime, timezone

from src import mongo
from src.messages.repository import (
    get_last_message, get_message_by_tg_id, get_messages, get_messages_count,
    get_messages_count_since, save_message, update_message,
)
from src.models import (
    Message, MessageReply, UpdateMessage, UserRole,
)
from src.tests.test_utils import make_message


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


async def test_get_messages_default_sort_order_keeps_the_newest():
    """Regression guard for the three callers that rely on the default."""
    for i in range(4):
        await save_message(make_message(text=f'msg{i}'))

    history = await get_messages(1, size=2, sort_order=-1)

    assert [m.text for m in history] == ['msg2', 'msg3']


async def test_get_messages_ascending_sort_order_keeps_the_oldest():
    """`sort_order` picks which end `size` takes — here, the front of the backlog."""
    for i in range(4):
        await save_message(make_message(text=f'msg{i}'))

    history = await get_messages(1, size=2, sort_order=1)

    assert [m.text for m in history] == ['msg0', 'msg1']


async def test_get_messages_is_chronological_under_both_sort_orders():
    """`sort_order` selects an end, never an order.

    The unconditional `reversed()` used to conflate the two, so asking for the
    oldest end silently returned them newest-first — which would have inverted the
    prompt's message order and `response.py`'s `[:-1]` trim.
    """
    for i in range(4):
        await save_message(make_message(text=f'msg{i}'))

    newest_end = await get_messages(1, size=4, sort_order=-1)
    oldest_end = await get_messages(1, size=4, sort_order=1)

    expected = ['msg0', 'msg1', 'msg2', 'msg3']
    assert [m.text for m in newest_end] == expected
    assert [m.text for m in oldest_end] == expected


async def test_get_messages_ascending_respects_from_date():
    """The memory pass combines both: everything after the watermark, oldest first."""
    await save_message(make_message(text='before'))
    cutoff = datetime.now(timezone.utc)
    for i in range(3):
        await save_message(make_message(text=f'after{i}'))

    history = await get_messages(1, size=2, from_date=cutoff, sort_order=1)

    assert [m.text for m in history] == ['after0', 'after1']


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


