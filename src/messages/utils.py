import asyncio
import logging
from functools import wraps

from telegram import Update
from telegram.ext import (ContextTypes)

from src.characters.repository import get_character

logger = logging.getLogger(__name__)
def set_chat_character(character_code: str, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data['character_code'] = character_code


def get_chat_character(context: ContextTypes.DEFAULT_TYPE, last_messages_recap: str | None = None):
    character_code = context.chat_data.get('character_code')
    character =  get_character(character_code, last_messages_recap)
    set_chat_character(character.code, context)
    return character


def escape_markdown_v2(text: str) -> str:
    """Escapes Telegram MarkdownV2 special characters."""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return "".join(f"\\{c}" if c in escape_chars else c for c in text)


def send_action(action):
    """Sends `action` while processing func command."""

    def decorator(func):
        @wraps(func)
        async def command_func(
            update: Update,
            context: ContextTypes.DEFAULT_TYPE,
            *args,
            **kwargs
        ):
            chat_id = update.effective_message.chat_id

            async def send_action_loop():
                try:
                    while True:
                        await context.bot.send_chat_action(chat_id=chat_id, action=action)
                        await asyncio.sleep(5)
                except asyncio.CancelledError:
                    pass

            action_task = asyncio.create_task(send_action_loop())
            try:
                return await func(update, context, *args, **kwargs)
            finally:
                action_task.cancel()

        return command_func

    return decorator
