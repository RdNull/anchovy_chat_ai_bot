import asyncio
from functools import wraps

from telegram import Update, Message
from telegram.ext import (ContextTypes, filters)

from src.logs import logger
from src import settings
from src.characters.repository import get_character
from src.models import MemoryData


class ReplyToBotFilter(filters.MessageFilter):
    def filter(self, message: Message) -> bool:
        return bool(
            message.reply_to_message and
            message.reply_to_message.from_user and
            message.reply_to_message.from_user.is_bot and
            message.reply_to_message.from_user.username == settings.BOT_NICKNAME
        )


def set_chat_character(character_code: str, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data['character_code'] = character_code


def get_chat_character(
    context: ContextTypes.DEFAULT_TYPE,
    memory: MemoryData | None = None,
):
    character_code = context.chat_data.get('character_code')
    character = get_character(
        character_code,
        memory=memory,
    )
    set_chat_character(character.code, context)
    return character


def escape_markdown_v2(text: str) -> str:
    """Escapes Telegram MarkdownV2 special characters."""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return "".join(str(c) if c not in escape_chars else f"\\{c}" for c in str(text))


def restricted(func):
    """Restricts access to the bot by chat and user ids."""

    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id

        is_allowed = False
        if str(user_id) in settings.ALLOWED_USER_IDS:
            is_allowed = True

        if str(chat_id) in settings.ALLOWED_CHAT_IDS:
            is_allowed = True

        if not is_allowed:
            logger.warning(f"Unauthorized access: user {user_id}, chat {chat_id}")
            if update.effective_message:
                await update.effective_message.reply_text(
                    f"Сорян, тебе нельзя пользоваться этим ботом\n"
                    f"Твой ID: `{user_id}`\n"
                    f"ID чата: `{chat_id}`",
                    parse_mode="MarkdownV2"
                )
            return

        return await func(update, context, *args, **kwargs)

    return wrapped


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
            chat_id = update.effective_chat.id
            bot = context.bot

            async def send_action_loop():
                try:
                    count = 0
                    while True:
                        if count % 5 == 0:
                            asyncio.create_task(
                                bot.send_chat_action(chat_id=chat_id, action=action)
                            )
                        await asyncio.sleep(1)
                        count += 1
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"Error in send_action_loop: {e}")

            action_task = asyncio.create_task(send_action_loop())
            try:
                return await func(update, context, *args, **kwargs)
            finally:
                action_task.cancel()

        return command_func

    return decorator
