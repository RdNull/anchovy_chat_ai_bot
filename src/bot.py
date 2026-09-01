import asyncio
import datetime as dt

from scheduler.asyncio import Scheduler
from scheduler.trigger import Monday
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler,
    MessageReactionHandler, filters,
)

from src import const, settings, tasks
from src.logs import logger
from src.messages import handlers
from src.messages.media import sticker_corpus_size
from src.messages.utils import ReplyToBotFilter


async def log_sticker_corpus():
    """One line at boot: how much of the group's sticker vocabulary is searchable yet?

    The corpus fills as people re-send stickers the bot has already seen, so this
    number is how you watch a cold start warm up — and if it stalls, it is the
    evidence that the group recycles a very small sticker set, the one thing that
    would make the narrow-vocabulary decision worth revisiting.
    """
    size = await sticker_corpus_size()
    logger.info(
        f'STICKER_CORPUS size={size} enabled={settings.ENABLE_STICKER_REPLIES}'
    )


async def setup_scheduler():
    schedule = Scheduler(tzinfo=const.TIMEZONE_ALMATY)
    schedule.weekly(
        Monday(dt.time(3, 0, tzinfo=const.TIMEZONE_ALMATY)),
        tasks.facts.run_fact_decay,
    )
    schedule.daily(
        dt.time(4, 0, tzinfo=const.TIMEZONE_ALMATY),
        tasks.memory.run_memory_cleanup,
    )
    while True:
        await asyncio.sleep(1)


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)  # so that both tg app and scheduler run on a single loop

    loop.create_task(log_sticker_corpus())
    loop.create_task(setup_scheduler())
    app = ApplicationBuilder().token(
        settings.TELEGRAM_TOKEN
    ).http_version('2').build()

    mention_handler = MessageHandler(
        filters.TEXT & (
            filters.ChatType.PRIVATE |
            filters.Mention(settings.BOT_NICKNAME) |
            ReplyToBotFilter()
        ),
        handlers.handle_mention
    )
    conversation_handler = MessageHandler(
        (
            filters.TEXT | filters.PHOTO | filters.Sticker.ALL | filters.ANIMATION
        ) & (~filters.COMMAND),
        handlers.handle_conversation
    )
    edits_handler = MessageHandler(
        filters.UpdateType.EDITED_MESSAGE & filters.TEXT,
        handlers.handle_message_edit
    )
    reaction_handler = MessageReactionHandler(handlers.handle_message_reaction)
    media_handler = MessageHandler(
        (filters.PHOTO | filters.Sticker.ALL | filters.ANIMATION) & (
            filters.ChatType.PRIVATE |
            filters.Mention(settings.BOT_NICKNAME) |
            ReplyToBotFilter()
        ), handlers.handle_media
    )
    start_handler = CommandHandler('start', handlers.start)
    info_handler = CommandHandler('info', handlers.info)
    list_handler = CommandHandler('list', handlers.list_characters)
    random_handler = CommandHandler('random', handlers.random_character)
    select_callback_handler = CallbackQueryHandler(
        handlers.select_character,
        pattern="^select_char:"
    )

    # commands
    app.add_handler(start_handler)
    app.add_handler(info_handler)
    app.add_handler(list_handler)
    app.add_handler(random_handler)
    app.add_handler(select_callback_handler)

    # chat meta handlers
    app.add_handler(edits_handler)
    app.add_handler(reaction_handler)

    # chat reply handlers
    app.add_handler(mention_handler)
    app.add_handler(media_handler)
    app.add_handler(conversation_handler)

    app.add_error_handler(handlers.error_handler)

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()  # pragma: no cover
