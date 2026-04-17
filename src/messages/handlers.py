import asyncio
import random
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import CallbackContext, ContextTypes

from src import settings
from src.characters.repository import CHARACTERS
from src.logs import logger
from src.models import MessageMediaStatus, UserRole
from .repository import get_last_message, push_history, register_chat
from .media import handle_media_message
from .parsing import parse_user_message
from .response import generate_answer
from .utils import (
    escape_markdown_v2, get_chat_character, restricted, send_action,
    set_chat_character,
)
from ..processors.context import run_context_checks


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
    await update.message.reply_text(
        f"*Персонаж:* {name}\n"
        f"*Описание:* {description}",
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
    await generate_answer(update, context)


@restricted
async def handle_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_message = await parse_user_message(update)
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
        await generate_answer(update, context)
        return

    await register_chat(chat_id)
    await push_history(user_message)
    if user_message.media and user_message.media.status == MessageMediaStatus.PENDING:
        asyncio.create_task(handle_media_message(user_message, context))

    asyncio.create_task(run_context_checks(chat_id))


@restricted
@send_action(ChatAction.TYPING)
async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = await parse_user_message(update)
    if not user_message:
        return

    if user_message.media:
        await handle_media_message(user_message, context)
        await generate_answer(update, context)
