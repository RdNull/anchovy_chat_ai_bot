import logging

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (CallbackContext, ContextTypes, filters)

from src import settings
from src.models import Message, MessageReply, UserRole
from .history import get_history, push_history
from .utils import get_chat_character, send_action

logger = logging.getLogger(__name__)


async def start(update: Update, context: CallbackContext):
    logger.info('started')
    await update.message.reply_text('Дарова, чорт!')


async def handle_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_message = _parse_user_message(update)
    await push_history(chat_id, user_message)


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
