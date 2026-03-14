import logging

from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, PicklePersistence, filters,
)

from src import settings
from src.messages import handlers

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def main() -> None:
    persistence = PicklePersistence(filepath=f'./{settings.APP_NAME}.tg')
    app = ApplicationBuilder().token(settings.TELEGRAM_TOKEN).persistence(persistence).build()

    mention_handler = MessageHandler(
        filters.TEXT & (
            filters.Mention(settings.BOT_NICKNAME) |
            filters.ChatType.PRIVATE |
            filters.REPLY
        ),
        handlers.handle_mention
    )
    conversation_handler = MessageHandler(
        filters.TEXT & (~filters.COMMAND),
        handlers.handle_conversation
    )
    start_handler = CommandHandler('start', handlers.start)
    info_handler = CommandHandler('info', handlers.info)

    app.add_handler(start_handler)
    app.add_handler(info_handler)
    app.add_handler(mention_handler)
    app.add_handler(conversation_handler)

    app.add_error_handler(handlers.error_handler)

    app.run_polling()


if __name__ == '__main__':
    main()
