import asyncio
import time

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from src import settings
from src.characters.character import Character
from src.characters.registry import get_chat_character
from src.characters.reply import Replier
from src.logs import elapsed_ms, event, logger
from src.memory.repository import get_last_memory
from src.running_app import get_bot
from .followups import run_followups
from .parsing import parse_user_message
from .repository import fetch_last_messages, save_message
from .utils import send_action


@send_action(ChatAction.TYPING)
async def generate_answer(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    user_message = await parse_user_message(update)
    if not user_message:
        return

    chat_id = update.effective_chat.id
    logger.info(
        'Generating answer',
        extra=event('MESSAGE_ANSWER_START', nickname=user_message.nickname),
    )

    started = time.monotonic()
    outcome = 'error'
    try:
        await save_message(user_message)

        bot = get_bot()
        last_memory = await get_last_memory(chat_id)
        character: Character = await get_chat_character(
            chat_id=chat_id,
            memory=last_memory or None,
        )
        replier = Replier(bot=bot, character=character, chat_id=chat_id, target=user_message)

        last_messages = await fetch_last_messages(chat_id, size=settings.LAST_MESSAGES_SIZE)
        await character.respond(replier, last_messages)

        asyncio.create_task(run_followups(chat_id))
        outcome = 'ok'
    finally:
        logger.info(
            'Answer generation finished',
            extra=event('MESSAGE_ANSWER_DONE', elapsed_ms=elapsed_ms(started), outcome=outcome),
        )
