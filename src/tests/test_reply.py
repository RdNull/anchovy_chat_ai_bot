import logging
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from telegram import MessageEntity

from src import settings
from src.characters.reply import Replier, ReplyKind
from src.messages.repository import get_message_by_tg_id, get_messages, save_message
from src.messages.models import Message, UserRole

_NOT_SET = object()


def make_replier(make_bot, chat_id=222, text='hi', target=_NOT_SET):
    bot = make_bot()
    character = MagicMock()
    character.name = 'testbot'
    character.code = 'testbot'
    character.nickname = f'{settings.BOT_NICKNAME}[testbot]'
    if target is _NOT_SET:
        target = Message(chat_id=chat_id, role=UserRole.USER, text=text, nickname='questioner')
    return Replier(bot, character, chat_id, target)


async def test_reply_message_calls_telegram_api(make_bot):
    replier = make_replier(make_bot)

    await replier.reply_message('hello back')

    assert replier.bot.send_message.call_count == 1
    assert replier.bot.send_message.call_args == call(
        chat_id=222, reply_parameters=None, text='hello back'
    )


async def test_reply_message_uses_reply_parameters_when_target_has_telegram_id(make_bot):
    replier = make_replier(make_bot)
    replier.target_message.telegram_id = 42

    await replier.reply_message('hello back')

    reply_params = replier.bot.send_message.call_args.kwargs['reply_parameters']
    assert reply_params.message_id == 42


async def test_reply_message_without_target_sends_without_reply_params(make_bot):
    replier = make_replier(make_bot, target=None)

    await replier.reply_message('unsolicited')

    assert replier.bot.send_message.call_args == call(
        chat_id=222, reply_parameters=None, text='unsolicited'
    )
    history = await get_messages(222)
    assert history[0].reply is None


async def test_reply_message_saves_to_db(make_bot):
    replier = make_replier(make_bot)

    await replier.reply_message('hello back')

    history = await get_messages(222)
    assert len(history) == 1
    assert history[0].role == UserRole.AI
    assert history[0].text == 'hello back'


async def test_reply_message_saves_character_code_and_tagged_nickname(make_bot):
    # AC8
    replier = make_replier(make_bot)

    await replier.reply_message('hello back')

    history = await get_messages(222)
    assert history[0].character_code == 'testbot'
    assert history[0].nickname == f'{settings.BOT_NICKNAME}[testbot]'


async def test_reply_message_attaches_user_message_as_reply(make_bot):
    replier = make_replier(make_bot, text='original question')
    # save_message (repository) resolves reply_id via a DB lookup on telegram_id,
    # so the target must exist in the DB first.
    await save_message(replier.target_message)

    await replier.reply_message('my answer')

    history = await get_messages(222)
    ai_msg = next(m for m in history if m.role == UserRole.AI)
    assert ai_msg.reply is not None
    assert ai_msg.reply.text == 'original question'
    assert ai_msg.reply.nickname == 'questioner'


async def test_reply_sticker_saves_character_code_and_tagged_nickname(make_bot):
    # AC8
    replier = make_replier(make_bot)

    await replier.reply_sticker('SENDABLE_FILE_ID', 'sticker_uid')

    history = await get_messages(222)
    assert history[0].character_code == 'testbot'
    assert history[0].nickname == f'{settings.BOT_NICKNAME}[testbot]'


async def test_reply_sticker_calls_telegram_api(make_bot):
    replier = make_replier(make_bot)

    await replier.reply_sticker('SENDABLE_FILE_ID', 'sticker_uid')

    assert replier.bot.send_sticker.call_count == 1
    assert replier.bot.send_sticker.call_args == call(
        chat_id=222, reply_parameters=None, sticker='SENDABLE_FILE_ID'
    )


async def test_reply_sticker_persists_the_send(make_bot):
    # Persisting is not bookkeeping: it is what the recency exclusion in find_stickers
    # reads, and what puts the sticker into the character's own history.
    replier = make_replier(make_bot)

    await replier.reply_sticker('SENDABLE_FILE_ID', 'sticker_uid')

    history = await get_messages(222)
    assert len(history) == 1
    saved = history[0]
    assert saved.role == UserRole.AI
    assert saved.media is not None
    assert saved.media.media_id == 'SENDABLE_FILE_ID'
    assert saved.media.unique_id == 'sticker_uid'
    assert not saved.text


async def test_reply_sticker_history_renders_without_literal_none(make_bot):
    replier = make_replier(make_bot)

    message = await replier.reply_sticker('SENDABLE_FILE_ID', 'sticker_uid')

    assert 'None' not in message.embedding_text


async def test_reply_reaction_calls_set_reaction(make_bot):
    replier = make_replier(make_bot)
    replier.target_message.telegram_id = 1

    await replier.reply_reaction('🤡', is_big=True)

    assert replier.bot.set_message_reaction.call_count == 1
    assert replier.bot.set_message_reaction.call_args == call(
        chat_id=222, message_id=1, reaction='🤡', is_big=True
    )


async def test_reply_reaction_returns_false_and_does_not_raise_when_result_is_false(make_bot):
    replier = make_replier(make_bot)
    replier.target_message.telegram_id = 1
    await save_message(replier.target_message)
    replier.bot.set_message_reaction = AsyncMock(return_value=False)

    result = await replier.reply_reaction('🤡')

    assert result is False


async def test_reply_reaction_rejected_by_telegram_does_not_log_reply_sent(make_bot, caplog):
    # A Telegram rejection must not count as sent in the REPLY_SENT Axiom metric.
    replier = make_replier(make_bot)
    replier.target_message.telegram_id = 1
    await save_message(replier.target_message)
    replier.bot.set_message_reaction = AsyncMock(return_value=False)

    with caplog.at_level(logging.INFO, logger='bot'):
        await replier.reply_reaction('🤡')

    reply_sent_records = [r for r in caplog.records if getattr(r, 'event', None) == 'REPLY_SENT']
    assert reply_sent_records == []
    updated = await get_message_by_tg_id(222, 1)
    assert updated.reactions == {}


async def test_reply_reaction_success_logs_reply_sent(make_bot, caplog):
    replier = make_replier(make_bot)
    replier.target_message.telegram_id = 1
    await save_message(replier.target_message)

    with caplog.at_level(logging.INFO, logger='bot'):
        result = await replier.reply_reaction('🤡')

    assert result is True
    record = next(r for r in caplog.records if getattr(r, 'event', None) == 'REPLY_SENT')
    assert record.kind == 'reaction'
    assert record.emoji == '🤡'


async def test_reply_reaction_raises_when_target_has_no_telegram_id(make_bot):
    # target_message exists but was never sent through Telegram (no telegram_id) —
    # there is nothing to attach a Bot API reaction to.
    replier = make_replier(make_bot)

    with pytest.raises(ValueError, match='Target message is not set'):
        await replier.reply_reaction('🤡')

    assert replier.bot.set_message_reaction.call_count == 0


async def test_reply_reaction_raises_when_no_target(make_bot):
    replier = make_replier(make_bot, target=None)

    with pytest.raises(ValueError, match='Target message is not set'):
        await replier.reply_reaction('🤡')


async def test_reply_reaction_writes_bot_reaction_to_message(make_bot):
    replier = make_replier(make_bot)
    replier.target_message.telegram_id = 1
    await save_message(replier.target_message)

    await replier.reply_reaction('🤡')

    updated = await get_message_by_tg_id(222, 1)
    assert updated is not None
    assert '🤡' in updated.reactions
    assert updated.reactions['🤡'] == [f'{settings.BOT_NICKNAME}[testbot]']

    history = await get_messages(222)
    assert all(m.role == UserRole.USER for m in history)


async def test_delivered_records_each_kind_after_telegram_accepts(make_bot):
    replier = make_replier(make_bot)
    replier.target_message.telegram_id = 42

    await replier.reply_message('hi')
    await replier.reply_sticker('file', 'uniq')
    await replier.reply_reaction('👍')

    assert replier.delivered == [ReplyKind.TEXT, ReplyKind.STICKER, ReplyKind.REACTION]


async def test_delivered_skips_a_reaction_telegram_refused(make_bot):
    replier = make_replier(make_bot)
    replier.target_message.telegram_id = 42
    replier.bot.set_message_reaction = AsyncMock(return_value=False)

    await replier.reply_reaction('👍')

    assert replier.delivered == []


# --- blockquote ---

QUOTE_TEXT = 'ну да\n> я больше не пью 🍺\n— imdemur, 12.03.2026'


def reply_sent_records(caplog):
    return [r for r in caplog.records if getattr(r, 'event', None) == 'REPLY_SENT']


async def test_quote_run_is_sent_as_one_blockquote_entity(make_bot, caplog):
    replier = make_replier(make_bot)

    with caplog.at_level(logging.INFO, logger='bot'):
        await replier.reply_message(QUOTE_TEXT)

    kwargs = replier.bot.send_message.call_args.kwargs
    sent = kwargs['text']
    assert sent == 'ну да\nя больше не пью 🍺\n— imdemur, 12.03.2026'
    assert len(kwargs['entities']) == 1
    entity = kwargs['entities'][0]
    assert entity.type == MessageEntity.BLOCKQUOTE
    utf16 = sent.encode('utf-16-le')
    quoted = utf16[entity.offset * 2 : (entity.offset + entity.length) * 2].decode('utf-16-le')
    assert quoted == 'я больше не пью 🍺'
    assert reply_sent_records(caplog)[0].quote_like is True


async def test_two_line_quote_run_is_one_entity(make_bot):
    replier = make_replier(make_bot)

    await replier.reply_message('> первая\n> вторая\n— ник, дата')

    kwargs = replier.bot.send_message.call_args.kwargs
    assert kwargs['text'] == 'первая\nвторая\n— ник, дата'
    assert len(kwargs['entities']) == 1
    assert kwargs['entities'][0].offset == 0
    assert kwargs['entities'][0].length == len('первая\nвторая')


@pytest.mark.parametrize('text', ['просто текст', '>без пробела', 'a > b'])
async def test_text_without_quote_run_goes_out_unchanged(make_bot, caplog, text):
    replier = make_replier(make_bot)

    with caplog.at_level(logging.INFO, logger='bot'):
        await replier.reply_message(text)

    assert replier.bot.send_message.call_args == call(chat_id=222, reply_parameters=None, text=text)
    assert reply_sent_records(caplog)[0].quote_like is False


async def test_only_first_quote_run_becomes_a_blockquote(make_bot):
    replier = make_replier(make_bot)

    await replier.reply_message('> раз\nмежду\n> два')

    kwargs = replier.bot.send_message.call_args.kwargs
    assert kwargs['text'] == 'раз\nмежду\n> два'
    assert len(kwargs['entities']) == 1


async def test_saved_message_text_is_the_sent_text(make_bot):
    replier = make_replier(make_bot)

    await replier.reply_message(QUOTE_TEXT)

    history = await get_messages(222)
    assert history[0].text == replier.bot.send_message.call_args.kwargs['text']
