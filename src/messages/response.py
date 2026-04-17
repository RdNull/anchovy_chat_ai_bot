import asyncio

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from src import settings
from src.logs import logger
from src.memory.repository import get_last_memory
from src.models import Message, MessageReply, UserRole
from .parsing import parse_user_message
from .repository import get_messages, save_message, register_chat
from .utils import get_chat_character, send_action
from ..processors.context import run_context_checks
from ..processors.context.embeddings import search_related_messages


@send_action(ChatAction.TYPING)
async def generate_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = await parse_user_message(update)
    if not user_message:
        return

    chat_id = update.effective_chat.id
    logger.info(f"Generating answer for chat {chat_id} (user: {user_message.nickname})")

    await asyncio.gather(
        register_chat(chat_id),
        save_message(user_message)
    )

    last_memory, related_messages = await asyncio.gather(
        get_last_memory(chat_id),
        search_related_messages(user_message)
    )
    character = get_chat_character(
        context=context,
        memory=last_memory if last_memory else None,
        related_messages=related_messages,
    )
    last_messages = await get_messages(
        chat_id,
        size=settings.LAST_MESSAGES_SIZE,
    )
    last_messages = last_messages[:-1]  # to trim the current user message from history
    response = await character.respond(user_message, last_messages)

    reply_message = await update.message.reply_text(response)

    await save_message(
        Message(
            telegram_id=reply_message.message_id,
            chat_id=chat_id,
            nickname=f'{settings.BOT_NICKNAME}({character.name})',
            role=UserRole.AI,
            text=response,
            reply=MessageReply(
                telegram_id=user_message.telegram_id,
                text=user_message.text,
                nickname=user_message.nickname,
                media=user_message.media
            ),
        )
    )
    asyncio.create_task(run_context_checks(chat_id))
