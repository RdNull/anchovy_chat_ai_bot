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
from src.models import RecapType


async def setup_scheduler():
    schedule = Scheduler(tzinfo=const.TIMEZONE_ALMATY)
    schedule.hourly(
        dt.time(minute=0, tzinfo=const.TIMEZONE_ALMATY),
        tasks.generate_all_chats_recap, args=(RecapType.HOURLY,)
    )
    schedule.daily(
        dt.time(hour=0, minute=0, tzinfo=const.TIMEZONE_ALMATY),
        tasks.generate_all_chats_recap,
        args=(RecapType.DAILY,)
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
        filters.TEXT & (~filters.COMMAND),
        handlers.handle_conversation
    )
    image_handler = MessageHandler(
        (filters.PHOTO | filters.Sticker.STATIC) & (
            filters.ChatType.PRIVATE |
            filters.Mention(settings.BOT_NICKNAME) |
            ReplyToBotFilter()
        ), handlers.handle_image
    )
    start_handler = CommandHandler('start', handlers.start)
    info_handler = CommandHandler('info', handlers.info)
    list_handler = CommandHandler('list', handlers.list_characters)
    recap_handler = CommandHandler('recap', handlers.send_recap)
    recap_hour_handler = CommandHandler('recap_hour', handlers.send_recap_hour)
    recap_day_handler = CommandHandler('recap_day', handlers.send_recap_day)
    model_handler = CommandHandler('model', handlers.list_models)
    random_handler = CommandHandler('random', handlers.random_character)
    select_callback_handler = CallbackQueryHandler(
        handlers.select_character,
        pattern="^select_char:"
    )
    select_model_callback_handler = CallbackQueryHandler(
        handlers.select_model,
        pattern="^select_model:"
    )

    app.add_handler(start_handler)
    app.add_handler(info_handler)
    app.add_handler(list_handler)
    app.add_handler(recap_handler)
    app.add_handler(recap_hour_handler)
    app.add_handler(recap_day_handler)
    app.add_handler(model_handler)
    app.add_handler(random_handler)
    app.add_handler(select_callback_handler)
    app.add_handler(select_model_callback_handler)
    app.add_handler(mention_handler)
    app.add_handler(conversation_handler)
    app.add_handler(image_handler)

    app.add_error_handler(handlers.error_handler)

    app.run_polling()


if __name__ == '__main__':
    main()
