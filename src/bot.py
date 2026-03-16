from telegram.ext import (
    ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, PicklePersistence,
    filters,
)

from src import settings
from src.messages import handlers
from src.messages.utils import ReplyToBotFilter


def main() -> None:
    persistence = PicklePersistence(filepath=settings.BOT_PERSISTENCE_FILE)
    app = ApplicationBuilder().token(settings.TELEGRAM_TOKEN).persistence(persistence).build()

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
    start_handler = CommandHandler('start', handlers.start)
    info_handler = CommandHandler('info', handlers.info)
    list_handler = CommandHandler('list', handlers.list_characters)
    recap_handler = CommandHandler('recap', handlers.send_recap)
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
    app.add_handler(model_handler)
    app.add_handler(random_handler)
    app.add_handler(select_callback_handler)
    app.add_handler(select_model_callback_handler)
    app.add_handler(mention_handler)
    app.add_handler(conversation_handler)

    # app.add_error_handler(handlers.error_handler)

    app.run_polling()


if __name__ == '__main__':
    main()
