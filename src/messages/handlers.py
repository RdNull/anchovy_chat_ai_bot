import logging
import random

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (CallbackContext, CallbackQueryHandler, ContextTypes, filters)

from datetime import datetime, timedelta, timezone

from src import settings
from src.characters.repository import CHARACTERS
from src.models import Message, MessageReply, UserRole
from .history import get_history, push_history, get_last_message
from .utils import (
    escape_markdown_v2, get_chat_character, send_action, set_chat_character, restricted,
)

logger = logging.getLogger(__name__)


async def start(update: Update, context: CallbackContext):
    logger.info('started')
    await update.message.reply_text('Дарова, чорт!')


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("Чёт пошло не так, сорян.")


@restricted
async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    character = get_chat_character(context)
    name = escape_markdown_v2(character.name)
    description = escape_markdown_v2(character.description)
    await update.message.reply_text(
        f"*Персонаж:* {name}\n"
        f"*Описание:* {description}",
        parse_mode="MarkdownV2"
    )


@restricted
async def list_characters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(character.name, callback_data=f"select_char:{code}")]
        for code, character in CHARACTERS.items()
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите персонажа:", reply_markup=reply_markup)


@restricted
async def select_character(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    character_code = query.data.split(":")[1]
    set_chat_character(character_code, context)
    character = CHARACTERS[character_code]

    await query.edit_message_text(f"Персонаж изменён на: {character.name}")


@restricted
async def random_character(update: Update, context: ContextTypes.DEFAULT_TYPE):
    character_code = random.choice(list(CHARACTERS.keys()))
    set_chat_character(character_code, context)
    character = CHARACTERS[character_code]

    await update.message.reply_text(f"Выпал персонаж: {character.name}")


@restricted
async def handle_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_message = _parse_user_message(update)

    if random.random() < settings.RANDOM_REPLY_CHANCE:
        last_message = await get_last_message(chat_id)
        if last_message and last_message.role == UserRole.AI:
            logger.info(f"Skipping random reply in chat {chat_id}: last message was from AI")
            return

        if last_message and last_message.created_at:
            cooldown_threshold = datetime.now(timezone.utc) - timedelta(
                minutes=settings.RANDOM_REPLY_COOLDOWN_MINUTES)
            if last_message.created_at > cooldown_threshold:
                logger.info(f"Skipping random reply in chat {chat_id}: cooldown not passed")
                return

        logger.info(f"Triggering random reply in chat {chat_id}")
        await _generate_answer(update, context)
        return

    await push_history(chat_id, user_message)


@restricted
async def handle_mention(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check if this is a reply to another user (not the bot) in a group
    if update.message.reply_to_message and not update.message.chat.type == "private":
        bot_user = await context.bot.get_me()
        if update.message.reply_to_message.from_user.id != bot_user.id:
            # If it's a reply but NOT to the bot, and there's no mention, don't handle it here
            if not filters.Mention(settings.BOT_NICKNAME).filter(update.message):
                await handle_conversation(update, context)
                return

    await _generate_answer(update, context)


@send_action(ChatAction.TYPING)
async def _generate_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_message = _parse_user_message(update)

    await push_history(chat_id, user_message)

    character = get_chat_character(context)
    last_messages = await get_history(chat_id)
    response = await character.respond(user_message, last_messages)

    await update.message.reply_text(response)

    await push_history(
        chat_id, Message(
            role=UserRole.AI,
            text=response,
            reply=MessageReply(text=user_message.text, nickname=user_message.nickname),
            nickname=settings.BOT_NICKNAME,
        )
    )


def _parse_user_message(update: Update):
    message_text = update.message.text
    reply = None
    if update.message.reply_to_message:
        reply_msg = update.message.reply_to_message
        reply_nickname = reply_msg.from_user.username or reply_msg.from_user.first_name if reply_msg.from_user else "unknown"
        reply = MessageReply(text=reply_msg.text or "", nickname=reply_nickname)

    user_nickname = update.message.from_user.username or update.message.from_user.first_name
    return Message(
        role=UserRole.USER,
        text=message_text,
        reply=reply,
        nickname=user_nickname
    )
