from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, call

from src import mongo
from src.characters.repository import CHARACTERS
from src.messages import handlers
from src.messages.handlers import _get_message_medium
from src.messages.history import get_history, push_history
from src.models import Message, UserRole


# --- /start ---

async def test_start_replies(make_update, make_context):
    update = make_update()
    await handlers.start(update, make_context)
    assert update.message.reply_text.call_count == 1


# --- /info ---

async def test_info_replies_with_character_name(make_update, make_context):
    code = next(iter(CHARACTERS))
    update = make_update()
    ctx = make_context
    ctx.chat_data['character_code'] = code

    await handlers.info(update, ctx)

    text = update.message.reply_text.call_args[0][0]
    display = CHARACTERS[code].display_name
    assert display.replace(' ', '\\ ') in text or display in text


# --- /list ---

async def test_list_characters_sends_keyboard(make_update, make_context):
    from telegram import InlineKeyboardMarkup
    update = make_update()

    await handlers.list_characters(update, make_context)

    call_kwargs = update.message.reply_text.call_args[1]
    assert isinstance(call_kwargs['reply_markup'], InlineKeyboardMarkup)


# --- /random ---

async def test_random_character_sets_valid_code(make_update, make_context):
    update = make_update()
    ctx = make_context

    await handlers.random_character(update, ctx)

    assert ctx.chat_data['character_code'] in CHARACTERS
    assert update.message.reply_text.call_count == 1


# --- select_character callback ---

async def test_select_character_updates_context(make_update, make_context):
    code = next(iter(CHARACTERS))
    update = make_update()
    ctx = make_context
    update.callback_query.data = f'select_char:{code}'
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()

    await handlers.select_character(update, ctx)

    assert ctx.chat_data['character_code'] == code
    assert update.callback_query.answer.call_count == 1
    assert update.callback_query.edit_message_text.call_count == 1


# --- @restricted access control ---

async def test_restricted_blocks_unauthorized_user(make_update, make_context):
    update = make_update(user_id=999, chat_id=999)

    await handlers.info(update, make_context)

    assert update.effective_message.reply_text.call_count == 1
    text = update.effective_message.reply_text.call_args[0][0]
    assert '999' in text
    assert update.message.reply_text.call_count == 0


# --- _parse_user_message ---

async def test_parse_user_message_text(make_update, make_context):
    update = make_update(text='hi there', username='alice', chat_id=222)

    msg = await handlers._parse_user_message(update)

    assert msg is not None
    assert msg.text == 'hi there'
    assert msg.nickname == 'alice'
    assert msg.chat_id == 222
    assert msg.role == UserRole.USER


async def test_parse_user_message_no_message_returns_none():
    update = MagicMock()
    update.message = None

    result = await handlers._parse_user_message(update)

    assert result is None


async def test_parse_user_message_with_reply(make_update):
    reply_msg = MagicMock()
    reply_msg.text = 'original'
    reply_msg.caption = None
    reply_msg.from_user.username = 'bob'
    reply_msg.from_user.first_name = 'bob'
    reply_msg.sticker = None
    reply_msg.photo = None
    reply_msg.animation = None
    update = make_update(reply_to_message=reply_msg)

    msg = await handlers._parse_user_message(update)

    assert msg.reply is not None
    assert msg.reply.text == 'original'
    assert msg.reply.nickname == 'bob'


async def test_parse_user_message_with_photo(make_update):
    photo = MagicMock()
    photo.file_id = 'file123'
    photo.file_unique_id = 'unique123'
    photo.width = 400
    photo.height = 400
    update = make_update(photo=[photo])

    msg = await handlers._parse_user_message(update)

    assert msg.media is not None
    assert msg.media.media_id == 'file123'
    assert msg.media.unique_id == 'unique123'


# --- handle_conversation ---

async def test_handle_conversation_pushes_to_history(make_update, make_context, mocker):
    mocker.patch('src.messages.handlers.random.random', return_value=1.0)
    mocker.patch(
        'src.messages.handlers.run_context_checks', new_callable=AsyncMock
    )
    update = make_update(chat_id=222)

    await handlers.handle_conversation(update, make_context)

    history = await get_history(222)
    assert len(history) == 1
    assert history[0].text == 'hello'


async def test_handle_conversation_skips_random_reply_if_last_was_ai(
    make_update, make_context, mocker
):
    mocker.patch('src.messages.handlers.random.random', return_value=0.0)
    mocker.patch(
        'src.messages.handlers.run_context_checks', new_callable=AsyncMock
    )
    mock_generate = mocker.patch(
        'src.messages.handlers._generate_answer', new_callable=AsyncMock
    )
    # Seed an AI message as the last in history
    await push_history(
        Message(chat_id=222, role=UserRole.AI, text='bot said', nickname='bot')
    )

    await handlers.handle_conversation(make_update(chat_id=222), make_context)

    assert mock_generate.call_count == 0


# --- _generate_answer ---

async def test_generate_answer_full_flow(make_update, make_context, mock_llm, mocker):
    mocker.patch(
        'src.messages.handlers.search_related_messages',
        new_callable=AsyncMock,
        return_value=[],
    )
    mocker.patch(
        'src.messages.handlers.run_context_checks', new_callable=AsyncMock
    )
    update = make_update(text='question', chat_id=222)

    await handlers._generate_answer(update, make_context)

    # User message + AI response both saved
    history = await get_history(222)
    assert len(history) == 2
    assert history[0].role == UserRole.USER
    assert history[0].text == 'question'
    assert history[1].role == UserRole.AI
    assert history[1].text == 'мок ответ'
    # Telegram reply was sent
    assert update.message.reply_text.call_count == 1
    assert update.message.reply_text.call_args == call('мок ответ')


async def test_error_handler(mocker):
    update = MagicMock()
    context = MagicMock()
    context.error = ValueError("Something went wrong")

    mock_logger = mocker.patch("src.messages.handlers.logger")

    await handlers.error_handler(update, context)

    assert mock_logger.error.call_count == 1
    assert "Exception while handling an update" in mock_logger.error.call_args[0][0]


async def test_handle_mention(make_update, make_context, mocker):
    update = make_update()
    mock_gen = mocker.patch("src.messages.handlers._generate_answer", AsyncMock())

    await handlers.handle_mention(update, make_context)

    assert mock_gen.call_count == 1


async def test_handle_media(make_update, make_context, mocker):
    update = make_update()
    # Mocking photo list as handle_media checks for photo/sticker/animation
    mock_photo = MagicMock()
    mock_photo.width = 100
    mock_photo.height = 100
    mock_photo.file_id = "f1"
    mock_photo.file_unique_id = "fu1"
    update.message.photo = [mock_photo]

    # Mock bot.get_file to be an AsyncMock
    make_context.bot.get_file = AsyncMock()

    mock_gen = mocker.patch("src.messages.handlers._generate_answer", AsyncMock())

    await handlers.handle_media(update, make_context)

    assert mock_gen.call_count == 1


async def test_handle_media_no_message_returns_early(make_context, mocker):
    update = MagicMock()
    update.message = None
    update.effective_user.id = 111
    update.effective_chat.id = 222
    update.effective_message.reply_text = AsyncMock()
    mock_gen = mocker.patch('src.messages.handlers._generate_answer', new_callable=AsyncMock)

    await handlers.handle_media(update, make_context)

    assert mock_gen.call_count == 0


# --- handle_conversation early return ---

async def test_handle_conversation_no_message_returns_early(make_context, mocker):
    update = MagicMock()
    update.message = None
    update.effective_user.id = 111
    update.effective_chat.id = 222
    update.effective_message.reply_text = AsyncMock()
    mocker.patch('src.messages.handlers.random.random', return_value=1.0)
    mock_push = mocker.patch('src.messages.handlers.push_history', new_callable=AsyncMock)

    await handlers.handle_conversation(update, make_context)

    assert mock_push.call_count == 0


# --- handle_conversation with pending media ---

async def test_handle_conversation_creates_media_task(make_update, make_context, mocker):
    mocker.patch('src.messages.handlers.random.random', return_value=1.0)
    mocker.patch('src.messages.handlers.run_context_checks', new_callable=AsyncMock)
    mock_handle_media = mocker.patch(
        'src.messages.handlers.handle_media_message', new_callable=AsyncMock
    )

    photo = MagicMock()
    photo.file_id = 'fid'
    photo.file_unique_id = 'uid'
    photo.width = 100
    photo.height = 100
    update = make_update(photo=[photo], chat_id=222)

    await handlers.handle_conversation(update, make_context)

    # Allow the created task to run
    import asyncio
    await asyncio.sleep(0)

    assert mock_handle_media.call_count == 1


# --- _generate_answer early return ---

async def test_generate_answer_no_message_returns_early(make_context, mocker):
    update = MagicMock()
    update.message = None
    mock_push = mocker.patch('src.messages.handlers.push_history', new_callable=AsyncMock)

    await handlers._generate_answer(update, make_context)

    assert mock_push.call_count == 0


# --- random reply cooldown ---

async def test_handle_conversation_random_reply_skipped_within_cooldown(
    make_update, make_context, mocker
):
    mocker.patch('src.messages.handlers.random.random', return_value=0.0)
    mocker.patch('src.messages.handlers.run_context_checks', new_callable=AsyncMock)
    mock_gen = mocker.patch('src.messages.handlers._generate_answer', new_callable=AsyncMock)

    # Insert a recent AI message (1 min ago, well within the 30-min cooldown) directly
    # so push_history doesn't overwrite created_at.
    recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp()
    await mongo.messages.insert_one({
        'chat_id': 222,
        'role': UserRole.AI.value,
        'text': 'bot said',
        'nickname': 'bot',
        'created_at': recent_ts,
        'media_id': None,
        'media_unique_id': None,
    })
    # Push a user message after so the last-any-message check sees a USER, not AI.
    await push_history(
        Message(chat_id=222, role=UserRole.USER, text='user msg', nickname='user')
    )

    await handlers.handle_conversation(make_update(chat_id=222), make_context)

    assert mock_gen.call_count == 0


async def test_handle_conversation_random_reply_fires_after_cooldown(
    make_update, make_context, mocker
):
    mocker.patch('src.messages.handlers.random.random', return_value=0.0)
    mocker.patch('src.messages.handlers.run_context_checks', new_callable=AsyncMock)
    mock_gen = mocker.patch('src.messages.handlers._generate_answer', new_callable=AsyncMock)

    # Insert an AI message with an old timestamp (past cooldown) directly so push_history
    # doesn't overwrite created_at with datetime.now().
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()
    await mongo.messages.insert_one({
        'chat_id': 222,
        'role': UserRole.AI.value,
        'text': 'old bot said',
        'nickname': 'bot',
        'created_at': old_ts,
        'media_id': None,
        'media_unique_id': None,
    })
    # Push a user message so that get_last_message(chat_id) returns a USER, not AI.
    await push_history(
        Message(chat_id=222, role=UserRole.USER, text='user msg', nickname='user')
    )

    await handlers.handle_conversation(make_update(chat_id=222), make_context)

    assert mock_gen.call_count == 1


# --- _parse_user_message reply with medium ---

async def test_parse_user_message_reply_with_sticker(make_update):
    sticker = MagicMock()
    sticker.file_id = 'sticker_fid'
    sticker.file_unique_id = 'sticker_uid'

    reply_msg = MagicMock()
    reply_msg.text = 'sticker reply'
    reply_msg.caption = None
    reply_msg.from_user.username = 'bob'
    reply_msg.from_user.first_name = 'bob'
    reply_msg.sticker = sticker
    reply_msg.photo = None
    reply_msg.animation = None

    update = make_update(reply_to_message=reply_msg)

    msg = await handlers._parse_user_message(update)

    assert msg.reply is not None
    assert msg.reply.media is not None
    assert msg.reply.media.media_id == 'sticker_fid'


# --- _get_message_medium ---

def test_get_message_medium_sticker():
    tg_msg = MagicMock()
    sticker = MagicMock()
    tg_msg.sticker = sticker
    tg_msg.photo = None
    tg_msg.animation = None

    result = _get_message_medium(tg_msg)

    assert result is sticker


def test_get_message_medium_animation_short():
    tg_msg = MagicMock()
    tg_msg.sticker = None
    tg_msg.photo = None
    animation = MagicMock()
    animation.duration = 5
    tg_msg.animation = animation

    result = _get_message_medium(tg_msg)

    assert result is animation


def test_get_message_medium_animation_too_long():
    tg_msg = MagicMock()
    tg_msg.sticker = None
    tg_msg.photo = None
    animation = MagicMock()
    animation.duration = 60
    tg_msg.animation = animation

    result = _get_message_medium(tg_msg)

    assert result is None
