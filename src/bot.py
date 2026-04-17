import asyncio
import datetime as dt

from scheduler.asyncio import Scheduler
from telegram.ext import (
    ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, PicklePersistence,
    filters,
)

from src import const, settings, tasks
from src.messages import handlers
from src.messages.utils import ReplyToBotFilter


async def setup_scheduler():
    schedule = Scheduler(tzinfo=const.TIMEZONE_ALMATY)
    schedule.hourly(
        dt.time(minute=0, tzinfo=const.TIMEZONE_ALMATY),
        tasks.memory.update_all_chats_memory
    )
    while True:
        await asyncio.sleep(1)


def main() -> None:
    persistence = PicklePersistence(filepath=settings.BOT_PERSISTENCE_FILE)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)  # so that both tg app and scheduler run on a single loop

    loop.create_task(setup_scheduler())
    app = ApplicationBuilder().token(
        settings.TELEGRAM_TOKEN
    ).persistence(
        persistence
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

    app.add_handler(start_handler)
    app.add_handler(info_handler)
    app.add_handler(list_handler)
    app.add_handler(random_handler)
    app.add_handler(select_callback_handler)
    app.add_handler(mention_handler)
    app.add_handler(media_handler)

    app.add_handler(conversation_handler)

    app.add_error_handler(handlers.error_handler)

    app.run_polling()


if __name__ == '__main__':
    main()  # pragma: no cover
