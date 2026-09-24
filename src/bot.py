import asyncio
import datetime as dt

from scheduler.asyncio import Scheduler
from scheduler.trigger import Monday
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    MessageReactionHandler,
    filters,
)

from src import const, settings, tasks
from src.characters.registry import CHARACTERS, check_default_character
from src.log_context import log_context, push_log_context
from src.logs import event, logger
from src.messages import handlers
from src.media import sticker_corpus_size
from src.messages.utils import ReplyToBotFilter
from src.running_app import set_running_app


class ContextBindingApplication(Application):
    """Binds `chat_id`/`user_id` once per update, in the one place that reaches every
    handler group's callback for it.

    PTB's own `process_update` is that place: it dispatches each handler group either by
    `await`ing the callback directly or via `self.create_task(...)` for the (default)
    non-blocking case, and a handler that raises has its error routed to
    `add_error_handler`'s callback from within that same call. `create_task` copies
    whatever `log_context` bound here into the new task, so both paths inherit it without
    each handler — or `error_handler` — binding its own copy.
    """

    async def process_update(self, update: object) -> None:
        chat_id = getattr(getattr(update, 'effective_chat', None), 'id', None)
        user_id = getattr(getattr(update, 'effective_user', None), 'id', None)
        with log_context(chat_id=chat_id, user_id=user_id):
            await super().process_update(update)


async def log_sticker_corpus():
    """One line at boot: how much of the group's sticker vocabulary is searchable yet?

    The corpus fills as people re-send stickers the bot has already seen, so this
    number is how you watch a cold start warm up — and if it stalls, it is the
    evidence that the group recycles a very small sticker set, the one thing that
    would make the narrow-vocabulary decision worth revisiting.
    """
    push_log_context()  # a dedicated task at boot, never fired again -- nothing to reset
    size = await sticker_corpus_size()
    logger.info(
        'Sticker corpus size',
        extra=event('STICKER_CORPUS', size=size, enabled=settings.ENABLE_STICKER_REPLIES),
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
    # Keeps this task (and the event loop) alive forever; the scheduler runs jobs on its
    # own timers. The event is never set, so this never wakes on its own.
    await asyncio.Event().wait()


async def post_init(application: Application) -> None:
    set_running_app(application)


def main() -> None:
    logger.info('Bot starting', extra=event('APP_START'))
    check_default_character(CHARACTERS, settings.DEFAULT_CHARACTER)
    logger.info(
        'Characters loaded',
        extra=event('CHARACTERS_LOADED', characters={c: v.public for c, v in CHARACTERS.items()}),
    )
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)  # so that both tg app and scheduler run on a single loop

    loop.create_task(log_sticker_corpus())
    loop.create_task(setup_scheduler())
    app = (
        ApplicationBuilder()
        .token(settings.TELEGRAM_TOKEN)
        .application_class(ContextBindingApplication)
        .http_version('2')
        .post_init(post_init)
        .build()
    )

    mention_handler = MessageHandler(
        filters.TEXT
        & (filters.ChatType.PRIVATE | filters.Mention(settings.BOT_NICKNAME) | ReplyToBotFilter()),
        handlers.handle_mention,
    )
    conversation_handler = MessageHandler(
        (filters.TEXT | filters.PHOTO | filters.Sticker.ALL | filters.ANIMATION)
        & (~filters.COMMAND),
        handlers.handle_conversation,
    )
    edits_handler = MessageHandler(
        filters.UpdateType.EDITED_MESSAGE & filters.TEXT, handlers.handle_message_edit
    )
    reaction_handler = MessageReactionHandler(handlers.handle_message_reaction)
    media_handler = MessageHandler(
        (filters.PHOTO | filters.Sticker.ALL | filters.ANIMATION)
        & (filters.ChatType.PRIVATE | filters.Mention(settings.BOT_NICKNAME) | ReplyToBotFilter()),
        handlers.handle_media,
    )
    start_handler = CommandHandler('start', handlers.start)
    info_handler = CommandHandler('info', handlers.info)
    list_handler = CommandHandler('list', handlers.list_characters)
    random_handler = CommandHandler('random', handlers.random_character)
    characters_handler = CommandHandler('characters', handlers.manage_character_access)
    select_callback_handler = CallbackQueryHandler(
        handlers.select_character, pattern='^select_char:'
    )
    access_callback_handler = CallbackQueryHandler(
        handlers.character_access, pattern='^char_access'
    )

    # commands
    app.add_handler(start_handler)
    app.add_handler(info_handler)
    app.add_handler(list_handler)
    app.add_handler(random_handler)
    app.add_handler(characters_handler)
    app.add_handler(select_callback_handler)
    app.add_handler(access_callback_handler)

    # chat meta handlers
    app.add_handler(edits_handler)
    app.add_handler(reaction_handler)

    # chat reply handlers
    app.add_handler(mention_handler)
    app.add_handler(media_handler)
    app.add_handler(conversation_handler)

    app.add_error_handler(handlers.error_handler)

    app.run_polling(allowed_updates=Update.ALL_TYPES)
    logger.info('Bot stopped', extra=event('APP_STOP'))


if __name__ == '__main__':
    main()  # pragma: no cover
