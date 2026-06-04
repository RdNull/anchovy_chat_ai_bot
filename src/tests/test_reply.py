from unittest.mock import AsyncMock, MagicMock

import pytest

from src import settings
from src.characters.reply import Replier
from src.messages.repository import get_message_by_tg_id, get_messages, save_message
from src.models import Message, UserRole


def make_replier(make_update, chat_id=222, text='hi'):
    update = make_update(chat_id=chat_id, text=text)
    character = MagicMock()
    character.name = 'testbot'
    user_msg = Message(chat_id=chat_id, role=UserRole.USER, text=text, nickname='questioner')
    return Replier(character, update, user_msg)


async def test_reply_message_calls_telegram_api(make_update):
    replier = make_replier(make_update)

    await replier.reply_message('hello back')

    assert replier.update.message.reply_text.call_count == 1
    assert replier.update.message.reply_text.call_args[0][0] == 'hello back'


async def test_reply_message_saves_to_db(make_update):
    replier = make_replier(make_update)

    await replier.reply_message('hello back')

    history = await get_messages(222)
    assert len(history) == 1
    assert history[0].role == UserRole.AI
    assert history[0].text == 'hello back'


async def test_reply_message_attaches_user_message_as_reply(make_update):
    replier = make_replier(make_update, text='original question')
    await save_message(replier.user_message)  # must exist in DB so _save_message can find it by telegram_id

    await replier.reply_message('my answer')

    history = await get_messages(222)
    ai_msg = next(m for m in history if m.role == UserRole.AI)
    assert ai_msg.reply is not None
    assert ai_msg.reply.text == 'original question'
    assert ai_msg.reply.nickname == 'questioner'


async def test_reply_reaction_calls_set_reaction(make_update):
    replier = make_replier(make_update)

    await replier.reply_reaction('🤡', is_big=True)

    assert replier.update.message.set_reaction.call_count == 1
    assert replier.update.message.set_reaction.call_args[0][0] == '🤡'
    assert replier.update.message.set_reaction.call_args[1]['is_big'] is True


async def test_reply_reaction_raises_when_result_is_false(make_update):
    replier = make_replier(make_update)
    replier.update.message.set_reaction = AsyncMock(return_value=False)

    with pytest.raises(ValueError, match='Failed to set reaction'):
        await replier.reply_reaction('🤡')


async def test_reply_reaction_writes_bot_reaction_to_message(make_update):
    replier = make_replier(make_update)
    replier.user_message.telegram_id = 1
    await save_message(replier.user_message)

    await replier.reply_reaction('🤡')

    updated = await get_message_by_tg_id(222, 1)
    assert updated is not None
    assert '🤡' in updated.reactions
    assert settings.BOT_NICKNAME in updated.reactions['🤡']

    history = await get_messages(222)
    assert all(m.role == UserRole.USER for m in history)


async def test_reply_reaction_skips_when_no_telegram_id(make_update):
    replier = make_replier(make_update)

    await replier.reply_reaction('🤡')

    history = await get_messages(222)
    assert len(history) == 0
