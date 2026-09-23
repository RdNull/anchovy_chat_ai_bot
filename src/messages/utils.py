import asyncio
from functools import wraps

from telegram import Message, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes, filters

from src import settings
from src.logs import event, logger
from src.running_app import get_bot


class ReplyToBotFilter(filters.MessageFilter):
    def filter(self, message: Message) -> bool:
        return bool(
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.is_bot
            and message.reply_to_message.from_user.username == settings.BOT_NICKNAME
        )


def escape_markdown_v2(text: str) -> str:
    """Escapes Telegram MarkdownV2 special characters."""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return ''.join(str(c) if c not in escape_chars else f'\\{c}' for c in str(text))


def restricted(func):
    """Restricts access to the bot by chat and user ids.

    `chat_id`/`user_id` are already bound by `ContextBindingApplication.process_update`
    (`src/bot.py`) by the time any handler runs, so nothing here needs to bind them again —
    including on the rejection path below, which used to be the one place that passed them
    explicitly.
    """

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
            # chat_id/user_id come from the ambient context (bound by
            # ContextBindingApplication.process_update), not passed explicitly here.
            logger.warning('Unauthorized access', extra=event('ACCESS_DENIED'))
            if update.effective_message:
                await update.effective_message.reply_text(
                    f'Сорян, тебе нельзя пользоваться этим ботом\n'
                    f'Твой ID: `{user_id}`\n'
                    f'ID чата: `{chat_id}`',
                    parse_mode='MarkdownV2',
                )
            return

        return await func(update, context, *args, **kwargs)

    return wrapped


def send_action(action: ChatAction):
    """Sends `action` while processing func command."""

    def decorator(func):
        @wraps(func)
        async def command_func(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
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
                except Exception:
                    logger.error(
                        'Error in send_action_loop',
                        exc_info=True,
                        extra=event('TYPING_LOOP_FAILED'),
                    )

            action_task = asyncio.create_task(send_action_loop())
            try:
                return await func(update, context, *args, **kwargs)
            finally:
                action_task.cancel()

        return command_func

    return decorator


async def send_chat_action(chat_id: int, action: ChatAction):
    bot = get_bot()
    await bot.send_chat_action(chat_id=chat_id, action=action)
