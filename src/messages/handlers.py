import asyncio
import random
import re
from datetime import datetime, timedelta, timezone

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, Message as TgMessage, PhotoSize, Update,
)
from telegram.constants import ChatAction
from telegram.ext import CallbackContext, ContextTypes

from src import ai as llm_module, settings
from src.characters.repository import CHARACTERS
from src.logs import logger
from src.models import (
    Message, MessageMediaStatus, MessageMediaTypes, MessageReply,
    RecapType, UserRole,
)
from src.processors.recap import generate_and_save_recap
from .history import (
    get_history, get_last_message, get_last_recap, get_last_recap_timestamp,
    get_message_media_data, get_messages_count, get_messages_count_since, push_history,
    register_chat,
)
from .media import handle_media_message
from .utils import (
    escape_markdown_v2, get_chat_character, get_chat_model, restricted, send_action,
    set_chat_character, set_chat_model,
)


async def start(update: Update, context: CallbackContext):
    logger.info('started')
    await update.message.reply_text('Дарова, чорт!')


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)


@restricted
async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(f"Info requested in chat {chat_id}")
    character = get_chat_character(context)
    name = escape_markdown_v2(character.display_name)
    description = escape_markdown_v2(character.description)
    model_code = get_chat_model(context)
    await update.message.reply_text(
        f"*Персонаж:* {name}\n"
        f"*Описание:* {description}\n"
        f"*Модель:* {escape_markdown_v2(model_code)}",
        parse_mode="MarkdownV2"
    )


@restricted
async def list_characters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(f"Characters list requested in chat {chat_id}")
    keyboard = [
        [InlineKeyboardButton(character.display_name, callback_data=f"select_char:{code}")]
        for code, character in CHARACTERS.items()
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите персонажа:", reply_markup=reply_markup)


@restricted
async def select_character(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = update.effective_chat.id
    await query.answer()

    character_code = query.data.split(":")[1]
    logger.info(f"Character {character_code} selected in chat {chat_id}")
    set_chat_character(character_code, context)
    character = CHARACTERS[character_code]

    await query.edit_message_text(f"Персонаж изменён на: {character.display_name}")


@restricted
@send_action(ChatAction.TYPING)
async def send_recap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send_recap_by_type(update, context, RecapType.PERIODIC)


@restricted
@send_action(ChatAction.TYPING)
async def send_recap_hour(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send_recap_by_type(update, context, RecapType.HOURLY)


@restricted
@send_action(ChatAction.TYPING)
async def send_recap_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send_recap_by_type(update, context, RecapType.DAILY)


async def _send_recap_by_type(update: Update, context: ContextTypes.DEFAULT_TYPE, recap_type: RecapType):
    chat_id = update.effective_chat.id
    logger.info(f"Recap {recap_type} requested in chat {chat_id}")

    recap = await get_last_recap(chat_id, recap_type=recap_type)
    if not recap:
        await update.message.reply_text("Сводки пока нет.")
        return

    recap_text = recap.text.strip()
    if recap_text:
        sentences = [s.strip() for s in re.split(r'\.\s*', recap_text) if s.strip()]
        recap_text = "\n".join([f"- {s}." for s in sentences])

    title = {
        RecapType.PERIODIC: "Последняя сводка",
        RecapType.HOURLY: "Сводка за час",
        RecapType.DAILY: "Сводка за день",
    }[recap_type]

    await update.message.reply_text(
        f"*{title}:*\n{escape_markdown_v2(recap_text)}",
        parse_mode="MarkdownV2"
    )


@restricted
async def list_models(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(f"Models list requested in chat {chat_id}")
    keyboard = [
        [InlineKeyboardButton(model_code, callback_data=f"select_model:{model_code}")]
        for model_code in settings.AI_MODELS.keys()
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите модель:", reply_markup=reply_markup)


@restricted
async def select_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = update.effective_chat.id
    await query.answer()

    model_code = query.data.split(":")[1]
    logger.info(f"Model {model_code} selected in chat {chat_id}")
    set_chat_model(model_code, context)

    await query.edit_message_text(f"Модель изменена на: {model_code}")


@restricted
async def random_character(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    character_code = random.choice(list(CHARACTERS.keys()))
    logger.info(f"Random character {character_code} chosen for chat {chat_id}")
    set_chat_character(character_code, context)
    character = CHARACTERS[character_code]

    await update.message.reply_text(f"Выпал персонаж: {character.display_name}")


@restricted
async def handle_mention(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(f"Bot mentioned or replied to in chat {chat_id}")
    await _generate_answer(update, context)


@restricted
async def handle_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_message = await _parse_user_message(update)
    if not user_message:
        return

    logger.debug(f"Handling conversation in chat {chat_id} from {user_message.nickname}")

    if random.random() < settings.RANDOM_REPLY_CHANCE:
        last_any_message = await get_last_message(chat_id)
        if last_any_message and last_any_message.role == UserRole.AI:
            logger.info(f"Skipping random reply in chat {chat_id}: last message was from AI")
            return

        last_bot_message = await get_last_message(chat_id, role=UserRole.AI)
        if last_bot_message and last_bot_message.created_at:
            cooldown_threshold = datetime.now(timezone.utc) - timedelta(
                minutes=settings.RANDOM_REPLY_COOLDOWN_MINUTES)
            if last_bot_message.created_at > cooldown_threshold:
                logger.info(f"Skipping random reply in chat {chat_id}: bot cooldown not passed")
                return

        logger.info(f"Triggering random reply in chat {chat_id}")
        await _generate_answer(update, context)
        return

    await register_chat(chat_id)
    await push_history(chat_id, user_message)
    if user_message.media and user_message.media.status == MessageMediaStatus.PENDING:
        asyncio.create_task(handle_media_message(user_message, context))

    asyncio.create_task(_check_recap(chat_id, context))


@restricted
@send_action(ChatAction.TYPING)
async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_message = await _parse_user_message(update)
    if not user_message:
        return

    await push_history(chat_id, user_message)
    if user_message.media:
        await handle_media_message(user_message, context)
        await _generate_answer(update, context)

async def _check_recap(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    last_recap_timestamp = await get_last_recap_timestamp(chat_id, RecapType.PERIODIC)
    if last_recap_timestamp:
        messages_count = await get_messages_count_since(chat_id, last_recap_timestamp)
    else:
        messages_count = await get_messages_count(chat_id)

    if messages_count >= settings.LAST_MESSAGES_SIZE:
        logger.info(
            f"Triggering periodic recap for chat {chat_id} (count since last: {messages_count})"
        )
        await generate_and_save_recap(chat_id, RecapType.PERIODIC)


@send_action(ChatAction.TYPING)
async def _generate_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = await _parse_user_message(update)
    if not user_message:
        return

    chat_id = update.effective_chat.id
    logger.info(f"Generating answer for chat {chat_id} (user: {user_message.nickname})")

    await register_chat(chat_id)
    await push_history(chat_id, user_message)

    last_recap = await get_last_recap(chat_id, RecapType.PERIODIC)
    hourly_recap = await get_last_recap(chat_id, RecapType.HOURLY)
    daily_recap = await get_last_recap(chat_id, RecapType.DAILY)

    character = get_chat_character(
        context=context,
        last_messages_recap=last_recap.text if last_recap else None,
        hourly_recap=hourly_recap.text if hourly_recap else None,
        daily_recap=daily_recap.text if daily_recap else None,
    )
    last_messages = await get_history(
        chat_id,
        size=settings.LAST_MESSAGES_SIZE,
        from_date=last_recap.created_at if last_recap else None
    )
    model_code = get_chat_model(context)
    llm = llm_module.get_model(model_code)
    response = await character.respond(user_message, last_messages, llm=llm)

    await update.message.reply_text(response)

    await push_history(
        chat_id, Message(
            role=UserRole.AI,
            text=response,
            reply=MessageReply(text=user_message.text, nickname=user_message.nickname),
            nickname=f'{settings.BOT_NICKNAME}({character.name})',
        )
    )
    asyncio.create_task(_check_recap(chat_id, context))


async def _parse_user_message(update: Update) -> Message | None:
    if not update.message:
        return None

    message_text = update.message.text
    reply = None
    if update.message.reply_to_message:
        reply_msg = update.message.reply_to_message
        reply_nickname = reply_msg.from_user.username or reply_msg.from_user.first_name if reply_msg.from_user else "unknown"
        reply = MessageReply(text=reply_msg.text or "", nickname=reply_nickname)

    user_nickname = update.message.from_user.username or update.message.from_user.first_name
    media_type, media_id = _get_message_media(update.message)
    media = await get_message_media_data(media_id)

    return Message(
        role=UserRole.USER,
        text=message_text,
        reply=reply,
        nickname=user_nickname,
        media=media
    )


def _get_message_media(tg_message: TgMessage) -> tuple[MessageMediaTypes, str] | None:
    photo_sizes: tuple[PhotoSize, ...] = tg_message.photo
    sticker = tg_message.sticker
    if not photo_sizes and not sticker:
        return None

    if photo_sizes:
        # selecting the biggest photo size that is less than 300_000 pixels ("magic number")
        photo_size = next(s for s in reversed(photo_sizes) if s.height * s.width <= 300_000)
        file_id = photo_size.file_id
    else:
        # sticker max dimensions are 512x512 - that's fine for detection
        file_id = sticker.file_id

    return MessageMediaTypes.IMAGE, file_id
