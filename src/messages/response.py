import asyncio

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from src import settings
from src.logs import logger
from src.memory.repository import get_last_memory
from src.models import Message
from src.running_app import get_bot
from .media.pipeline import wait_for_media_ready
from .parsing import parse_user_message
from .repository import get_messages, save_message
from .utils import get_chat_character, send_action
from ..characters.character import Character
from ..characters.reply import Replier
from ..processors.context.handlers import run_context_checks


@send_action(ChatAction.TYPING)
async def generate_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = await parse_user_message(update)
    if not user_message:
        return

    chat_id = update.effective_chat.id
    logger.info(f'Generating answer for chat {chat_id} (user: {user_message.nickname})')

    await save_message(user_message)

    bot = get_bot()
    last_memory = await get_last_memory(chat_id)
    character: Character = await get_chat_character(
        chat_id=chat_id,
        memory=last_memory if last_memory else None,
    )
    replier = Replier(bot=bot, character=character, chat_id=chat_id, target=user_message)

    last_messages = await _get_last_messages(chat_id)
    await character.respond(replier, last_messages)

    asyncio.create_task(run_context_checks(chat_id))


async def _get_last_messages(chat_id: int) -> list[Message]:
    last_messages = await get_messages(
        chat_id,
        size=settings.LAST_MESSAGES_SIZE,
    )
    pending_media_ids = [
        m.media.unique_id
        for m in last_messages
        if m.media and m.media.status.is_pending
    ]
    if not pending_media_ids:
        return last_messages[:-1]  # to trim the current user message from history

    await wait_for_media_ready(
        pending_media_ids,
        timeout=settings.RESPOND_MEDIA_PROCESSING_POLLING_TIMEOUT
    )
    return await get_messages(chat_id, size=settings.LAST_MESSAGES_SIZE)
