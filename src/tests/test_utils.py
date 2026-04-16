from unittest.mock import MagicMock

from src.characters.repository import CHARACTERS
from src.messages.utils import (
    ReplyToBotFilter,
    escape_markdown_v2,
    get_chat_character,
    set_chat_character,
)
from src.models import (
    Message,
    MessageMedia,
    MessageMediaStatus,
    MessageMediaTypes,
    MessageReply,
    UserRole,
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

def _make_context():
    ctx = MagicMock()
    ctx.chat_data = {}
    return ctx


def test_set_get_chat_character():
    code = next(iter(CHARACTERS))
    ctx = _make_context()
    set_chat_character(code, ctx)
    character = get_chat_character(ctx)
    assert character.code == code


def test_get_chat_character_no_code_returns_valid():
    ctx = _make_context()
    character = get_chat_character(ctx)
    assert character.code in CHARACTERS


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


# --- Message.ai_format ---

def test_message_ai_format_plain():
    msg = Message(chat_id=1, nickname='nick', role=UserRole.USER, text='hello')
    assert msg.ai_format == 'nick: hello'


def test_message_ai_format_with_reply_text():
    reply = MessageReply(text='quoted', nickname='other')
    msg = Message(chat_id=1, nickname='nick', role=UserRole.USER, text='hello', reply=reply)
    assert msg.ai_format == 'nick (reply: "other| quoted"): hello'


def test_message_ai_format_reply_truncates():
    long_text = 'x' * 60
    reply = MessageReply(text=long_text, nickname='other')
    msg = Message(chat_id=1, nickname='nick', role=UserRole.USER, text='hello', reply=reply)
    # reply.ai_format truncates text to 50 chars
    assert 'x' * 50 in msg.ai_format
    assert 'x' * 51 not in msg.ai_format


def test_message_ai_format_with_media_processing():
    media = MessageMedia(status=MessageMediaStatus.PENDING)
    msg = Message(chat_id=1, nickname='nick', role=UserRole.USER, text='hello', media=media)
    assert msg.ai_format == 'nick: hello [PROCESSING]'


def test_message_ai_format_with_media_ready():
    media = MessageMedia(
        status=MessageMediaStatus.READY,
        type=MessageMediaTypes.IMAGE,
        description='a cat',
        ocr_text='meow',
    )
    msg = Message(chat_id=1, nickname='nick', role=UserRole.USER, text='hello', media=media)
    assert msg.ai_format == 'nick: hello [image: a cat | текст: meow]'


def test_message_ai_format_reply_with_media():
    media = MessageMedia(
        status=MessageMediaStatus.READY,
        type=MessageMediaTypes.IMAGE,
        description='a dog',
        ocr_text=None,
    )
    reply = MessageReply(text='look', nickname='other', media=media)
    msg = Message(chat_id=1, nickname='nick', role=UserRole.USER, text='nice', reply=reply)
    assert 'image: a dog' in msg.ai_format
    assert 'other|' in msg.ai_format
