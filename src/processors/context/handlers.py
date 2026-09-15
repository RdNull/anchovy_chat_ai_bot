import asyncio

from src import settings
from src.facts.processors import extract_facts
from src.initiative.handlers import run_initiative_checks
from src.logs import event, logger
from src.memory.processors import extract_memory
from src.memory.repository import get_last_memory
from src.messages.repository import get_messages, get_messages_count, get_messages_count_since
from src.processors.context.embeddings import get_last_embedding_task, update_chat_embeddings

CHAT_CONTEXT_LOCK = asyncio.Lock()


async def run_context_checks(chat_id: int):
    # Guarded on its own: this stage runs first, so an unhandled failure here would
    # take the memory and embedding passes down with it. The caller is a bare
    # `create_task`, so the traceback would surface only at GC time.
    try:
        await run_initiative_checks(chat_id)
    except Exception:
        logger.error(
            'Error running initiative checks', exc_info=True,
            extra=event('INITIATIVE_CHECK', outcome='error'),
        )

    await run_memory_checks(chat_id)
    await run_embedding_checks(chat_id)


async def update_chat_context(chat_id: int):
    logger.debug('Updating memory', extra=event('MEMORY_UPDATE_START'))
    try:
        async with CHAT_CONTEXT_LOCK:
            await _update_chat_memory(chat_id)
    except Exception:
        logger.error(
            'Error updating memory', exc_info=True, extra=event('MEMORY_UPDATE', outcome='error'),
        )


async def _update_chat_memory(chat_id: int):
    last_memory_data = await get_last_memory(chat_id)

    from_date = last_memory_data.created_at if last_memory_data else None
    # Oldest first: on a backlog past the cap the newest are the ones left behind,
    # and the watermark below defers them to the next cycle instead of skipping them.
    new_messages = await get_messages(
        chat_id,
        size=settings.MESSAGES_MEMORY_MAX_SIZE,
        from_date=from_date,
        sort_order=1,
    )

    if len(new_messages) < settings.LAST_MESSAGES_MIN_SIZE:
        logger.info(
            'No new messages for memory update',
            extra=event('MEMORY_UPDATE', outcome='empty', count=len(new_messages)),
        )
        return

    await extract_memory(chat_id, last_memory_data, new_messages)
    await extract_facts(new_messages)


async def run_memory_checks(chat_id: int):
    last_memory = await get_last_memory(chat_id)
    if last_memory:
        messages_count = await get_messages_count_since(
            chat_id, last_memory.created_at.timestamp()
        )
    else:
        messages_count = await get_messages_count(chat_id)

    if messages_count >= settings.MEMORY_TRIGGER_SIZE:
        logger.info(
            'Triggering periodic memory update',
            extra=event(
                'MEMORY_TRIGGERED', count=messages_count, trigger_size=settings.MEMORY_TRIGGER_SIZE,
            ),
        )
        await update_chat_context(chat_id)


async def run_embedding_checks(chat_id: int):
    last_embeddings_task = await get_last_embedding_task(chat_id)
    if last_embeddings_task:
        messages_count = await get_messages_count_since(
            chat_id, last_embeddings_task.last_message_time.timestamp()
        )
    else:
        messages_count = await get_messages_count(chat_id)

    if messages_count >= settings.EMBEDDINGS_TRIGGER_SIZE:
        logger.info(
            'Triggering periodic embedding update',
            extra=event(
                'EMBEDDING_TRIGGERED', count=messages_count,
                trigger_size=settings.EMBEDDINGS_TRIGGER_SIZE,
            ),
        )
        await update_chat_embeddings(chat_id)
