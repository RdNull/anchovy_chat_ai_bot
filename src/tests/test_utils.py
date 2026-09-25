from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from telegram.constants import ChatAction

from src import settings
from src.characters.character import Character
from src.characters.registry import CHARACTERS, get_chat_character, set_chat_character
from src.chat_settings import repository as chat_settings_repository
from src.messages.models import (
    Message,
    MessageMedia,
    MessageMediaStatus,
    MessageMediaTypes,
    MessageReply,
    UserRole,
)
from src.messages.utils import ReplyToBotFilter, escape_markdown_v2, send_chat_action


def make_private_character(code='secret'):
    return Character(
        code=code,
        display_name='Secret',
        name='Secret',
        description='private one',
        style_prompt='...',
        public=False,
    )


def make_message(
    chat_id=1,
    telegram_id=None,
    role=UserRole.USER,
    text='hello',
    nickname='user1',
):
    return Message(
        chat_id=chat_id, telegram_id=telegram_id, role=role, text=text, nickname=nickname
    )


# --- escape_markdown_v2 ---


def test_escape_markdown_v2_special_chars():
    special = r'_*[]()~`>#+-=|{}.!'
    result = escape_markdown_v2(special)
    for char in special:
        assert f'\\{char}' in result


def test_escape_markdown_v2_plain_text():
    assert escape_markdown_v2('hello world') == 'hello world'


# --- ReplyToBotFilter ---


def _make_tg_message(is_bot=True, username='test_bot', has_reply=True):
    message = MagicMock()
    if has_reply:
        message.reply_to_message.from_user.is_bot = is_bot
        message.reply_to_message.from_user.username = username
    else:
        message.reply_to_message = None
    return message


def test_reply_to_bot_filter_matches():
    f = ReplyToBotFilter()
    assert f.filter(_make_tg_message(is_bot=True, username='test_bot')) is True


def test_reply_to_bot_filter_no_reply():
    f = ReplyToBotFilter()
    assert f.filter(_make_tg_message(has_reply=False)) is False


def test_reply_to_bot_filter_not_a_bot():
    f = ReplyToBotFilter()
    assert f.filter(_make_tg_message(is_bot=False, username='test_bot')) is False


def test_reply_to_bot_filter_wrong_username():
    f = ReplyToBotFilter()
    assert f.filter(_make_tg_message(is_bot=True, username='other_bot')) is False


# --- set_chat_character / get_chat_character ---


async def test_set_get_chat_character():
    code = settings.DEFAULT_CHARACTER
    chat_id = 12345
    await set_chat_character(chat_id, code)
    character = await get_chat_character(chat_id)
    assert character.code == code


async def test_get_chat_character_no_code_returns_the_default_and_saves_it():
    # AC1
    character = await get_chat_character(54321)

    assert character.code == settings.DEFAULT_CHARACTER
    assert await chat_settings_repository.get_character_code(54321) == settings.DEFAULT_CHARACTER


async def test_get_chat_character_never_picks_at_random():
    codes = {(await get_chat_character(chat_id)).code for chat_id in range(60000, 60020)}

    assert codes == {settings.DEFAULT_CHARACTER}


async def test_get_chat_character_unknown_code_falls_back_and_overwrites():
    # AC2
    await chat_settings_repository.set_character_code(54322, 'no-such-character')

    character = await get_chat_character(54322)

    assert character.code == settings.DEFAULT_CHARACTER
    assert await chat_settings_repository.get_character_code(54322) == settings.DEFAULT_CHARACTER


async def test_get_chat_character_unavailable_private_code_falls_back_and_overwrites(mocker):
    # AC2: a private character the chat is not (or no longer) allowed to use
    private = make_private_character()
    mocker.patch.dict(CHARACTERS, {private.code: private})
    await chat_settings_repository.set_character_code(54323, private.code)

    character = await get_chat_character(54323)

    assert character.code == settings.DEFAULT_CHARACTER
    assert await chat_settings_repository.get_character_code(54323) == settings.DEFAULT_CHARACTER


async def test_get_chat_character_keeps_an_allowed_private_character(mocker):
    private = make_private_character()
    mocker.patch.dict(CHARACTERS, {private.code: private})
    await chat_settings_repository.set_character_code(54324, private.code)
    await chat_settings_repository.toggle_allowed_character(54324, private.code)

    character = await get_chat_character(54324)

    assert character.code == private.code


# --- send_chat_action ---


async def test_send_chat_action_calls_bot(mocker):
    bot = MagicMock()
    bot.send_chat_action = AsyncMock()
    mocker.patch('src.messages.utils.get_bot', return_value=bot)

    await send_chat_action(222, ChatAction.TYPING)

    assert bot.send_chat_action.call_count == 1
    assert bot.send_chat_action.call_args == call(chat_id=222, action=ChatAction.TYPING)


# --- MessageMedia.ai_format ---


def test_media_ai_format_processing():
    media = MessageMedia(status=MessageMediaStatus.PENDING)
    assert media.ai_format == 'PROCESSING'


def test_media_ai_format_ready_with_type():
    media = MessageMedia(
        status=MessageMediaStatus.READY,
        type=MessageMediaTypes.IMAGE,
        description='a cat',
        ocr_text='meow',
    )
    assert media.ai_format == 'image: a cat | текст: meow'


def test_media_ai_format_ready_no_type():
    media = MessageMedia(
        status=MessageMediaStatus.READY,
        type=None,
        description='a cat',
        ocr_text='meow',
    )
    assert media.ai_format == 'a cat | текст: meow'


def test_media_ai_format_ready_no_ocr():
    media = MessageMedia(
        status=MessageMediaStatus.READY,
        type=MessageMediaTypes.IMAGE,
        description='a cat',
        ocr_text=None,
    )
    assert media.ai_format == 'image: a cat | текст: '


# --- Message.embedding_text ---


def test_message_embedding_text_plain():
    msg = Message(chat_id=1, nickname='nick', role=UserRole.USER, text='hello')
    assert msg.embedding_text == 'nick: hello'


def test_message_embedding_text_with_reply_text():
    reply = MessageReply(text='quoted', nickname='other')
    msg = Message(chat_id=1, nickname='nick', role=UserRole.USER, text='hello', reply=reply)
    assert msg.embedding_text == 'nick (reply: "other| quoted"): hello'


def test_message_embedding_text_reply_truncates():
    long_text = 'x' * 60
    reply = MessageReply(text=long_text, nickname='other')
    msg = Message(chat_id=1, nickname='nick', role=UserRole.USER, text='hello', reply=reply)
    assert 'x' * 50 in msg.embedding_text
    assert 'x' * 51 not in msg.embedding_text


def test_message_embedding_text_with_media_processing():
    media = MessageMedia(status=MessageMediaStatus.PENDING)
    msg = Message(chat_id=1, nickname='nick', role=UserRole.USER, text='hello', media=media)
    assert msg.embedding_text == 'nick: hello [PROCESSING]'


def test_message_embedding_text_with_media_ready():
    media = MessageMedia(
        status=MessageMediaStatus.READY,
        type=MessageMediaTypes.IMAGE,
        description='a cat',
        ocr_text='meow',
    )
    msg = Message(chat_id=1, nickname='nick', role=UserRole.USER, text='hello', media=media)
    assert msg.embedding_text == 'nick: hello [image: a cat | текст: meow]'


def test_message_embedding_text_reply_with_media():
    media = MessageMedia(
        status=MessageMediaStatus.READY,
        type=MessageMediaTypes.IMAGE,
        description='a dog',
        ocr_text=None,
    )
    reply = MessageReply(text='look', nickname='other', media=media)
    msg = Message(chat_id=1, nickname='nick', role=UserRole.USER, text='nice', reply=reply)
    assert 'image: a dog' in msg.embedding_text
    assert 'other|' in msg.embedding_text


def test_message_embedding_text_caption_less_media_has_no_literal_none():
    # A bare sticker or photo carries no caption, and `None` used to reach both the
    # prompt and the embeddings as the literal string "None [image: ...]".
    media = MessageMedia(
        status=MessageMediaStatus.READY,
        type=MessageMediaTypes.IMAGE,
        description='a cat',
        ocr_text=None,
    )
    msg = Message(chat_id=1, nickname='nick', role=UserRole.USER, text=None, media=media)

    assert 'None' not in msg.embedding_text
    assert msg.embedding_text == 'nick:  [image: a cat | текст: ]'


def test_message_embedding_text_with_timestamp():
    # 2026-04-19 10:00 UTC = 2026-04-19 15:00 Almaty (UTC+5)
    created_at = datetime(2026, 4, 19, 10, 0, 0, tzinfo=UTC)
    msg = Message(
        chat_id=1, nickname='nick', role=UserRole.USER, text='hello', created_at=created_at
    )
    assert msg.embedding_text == '[2026-04-19 15:00] nick: hello'


def test_message_embedding_text_reply_with_timestamp():
    created_at = datetime(2026, 4, 19, 10, 0, 0, tzinfo=UTC)
    reply = MessageReply(text='quoted', nickname='other')
    msg = Message(
        chat_id=1,
        nickname='nick',
        role=UserRole.USER,
        text='hello',
        reply=reply,
        created_at=created_at,
    )
    assert msg.embedding_text == '[2026-04-19 15:00] nick (reply: "other| quoted"): hello'


# --- Message.response_format ---


def test_message_response_format_plain_text_unchanged():
    msg = Message(chat_id=1, nickname='nick', role=UserRole.AI, text='ответил')
    assert msg.response_format == 'ответил'


def test_message_response_format_with_media_mirrors_embedding_text():
    # A bot sticker reply used to render as a blank assistant turn: response_format had
    # no media branch, unlike embedding_text. It now mirrors embedding_text's, so a bot
    # turn is never silently dropped from the prompt history.
    media = MessageMedia(
        status=MessageMediaStatus.READY,
        type=MessageMediaTypes.STICKER,
        description='жаба на руках',
        ocr_text=None,
    )
    msg = Message(chat_id=1, nickname='nick', role=UserRole.AI, text=None, media=media)

    assert 'None' not in msg.response_format
    assert msg.response_format == ' [sticker: жаба на руках | текст: ]'


def test_message_response_format_with_media_and_reactions():
    media = MessageMedia(
        status=MessageMediaStatus.READY,
        type=MessageMediaTypes.STICKER,
        description='жаба на руках',
        ocr_text=None,
    )
    msg = _msg({'🤡': ['dima']})
    msg.text = None
    msg.media = media

    assert msg.response_format == ' [sticker: жаба на руках | текст: ]\n⤷ 🤡 dima'


# --- Message._render_reactions ---

BOT = settings.BOT_NICKNAME


def _msg(reactions):
    return Message(chat_id=1, nickname='nick', role=UserRole.USER, text='hi', reactions=reactions)


@pytest.mark.parametrize(
    ('reactions', 'expected'),
    [
        ({}, None),
        ({'🖕': [BOT]}, f'⤷ 🖕 {BOT}'),
        ({'🤡': ['dima', 'sasha']}, '⤷ 🤡 dima, sasha'),
        ({'👍': [BOT, 'misha']}, f'⤷ 👍 {BOT}, misha'),
        ({'🤡': ['a', 'b', 'c']}, '⤷ 🤡 a, b, c'),
        ({'👍': ['a', 'b', 'c', 'd']}, '⤷ 👍 ×4'),
        ({'👍': [BOT, 'a', 'b', 'c', 'd']}, f'⤷ 👍 {BOT} +4'),
        ({'🤡': ['dima', 'sasha'], '👍': [BOT, 'misha']}, f'⤷ 🤡 dima, sasha · 👍 {BOT}, misha'),
        ({'🤡': ['a', 'b', 'c', 'd'], '🖕': [BOT]}, f'⤷ 🤡 ×4 · 🖕 {BOT}'),
    ],
)
def test_render_reactions(reactions: dict, expected: str | None):
    assert _msg(reactions)._render_reactions(own_nickname=BOT) == expected


def test_render_reactions_names_only_the_answering_characters_own_reaction():
    # AC10: another character's tagged nickname is just a user, so it counts toward the
    # named-reactor threshold instead of jumping the queue.
    own = f'{BOT}[a]'
    other = f'{BOT}[b]'
    reactions = {'👍': ['x', other, own]}

    assert _msg(reactions)._render_reactions(own_nickname=own) == f'⤷ 👍 {own}, x, {other}'
    assert _msg(reactions)._render_reactions(own_nickname=other) == f'⤷ 👍 {other}, x, {own}'


def test_render_reactions_without_own_nickname_treats_everyone_alike():
    reactions = {'👍': ['a', 'b', 'c', 'd']}

    assert _msg(reactions)._render_reactions() == '⤷ 👍 ×4'


def test_render_reactions_appears_in_ai_format():
    assert _msg({'🤡': ['dima']}).ai_format == 'nick: hi\n⤷ 🤡 dima'


def test_render_reactions_not_in_embedding_text():
    assert _msg({'🤡': ['dima']}).embedding_text == 'nick: hi'
