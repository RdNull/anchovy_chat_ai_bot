import asyncio
import time
from datetime import datetime, timedelta, UTC

from src import mongo, settings
from src.facts.handlers import update_user_facts
from src.logs import elapsed_ms, event, logger
from src.memory.models import StructuredMemory
from src.memory.processors import MEMORY_MODEL_NAME, MEMORY_MODEL_VERSION, extract_memory
from src.memory.repository import get_last_memory, save_memory
from src.messages.repository import get_messages, get_messages_count, get_messages_count_since

# Module-level and shared by every chat, like `EMBEDDING_TASK_LOCK` — held across the
# whole read-watermark -> fetch -> save body, so a failed save leaves the watermark
# unadvanced rather than losing the window.
MEMORY_UPDATE_LOCK = asyncio.Lock()


async def run_memory_checks(chat_id: int):
    last_memory = await get_last_memory(chat_id)
    if last_memory:
        messages_count = await get_messages_count_since(chat_id, last_memory.created_at.timestamp())
    else:
        messages_count = await get_messages_count(chat_id)

    if messages_count >= settings.MEMORY_TRIGGER_SIZE:
        logger.info(
            'Triggering periodic memory update',
            extra=event(
                'MEMORY_TRIGGERED',
                count=messages_count,
                trigger_size=settings.MEMORY_TRIGGER_SIZE,
            ),
        )
        await update_chat_memory(chat_id)


async def update_chat_memory(chat_id: int):
    logger.debug('Updating memory', extra=event('MEMORY_UPDATE_START'))
    try:
        async with MEMORY_UPDATE_LOCK:
            await _update_chat_memory(chat_id)
    except Exception:
        logger.error(
            'Error updating memory',
            exc_info=True,
            extra=event('MEMORY_UPDATE', outcome='error'),
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

    if not settings.ENABLE_MEMORY_PROCESSING:
        await save_memory(chat_id, StructuredMemory())
        logger.info(
            'Memory processing disabled, saved empty memory',
            extra=event('MEMORY_EXTRACT', outcome='disabled'),
        )
    else:
        started = time.monotonic()
        updated_memory = await extract_memory(chat_id, last_memory_data, new_messages)
        if updated_memory:
            try:
                await save_memory(
                    chat_id,
                    updated_memory.content,
                    updated_memory.decay,
                    created_at=updated_memory.created_at,
                )
                logger.info(
                    'Memory updated and saved',
                    extra=event(
                        'MEMORY_EXTRACT',
                        outcome='ok',
                        elapsed_ms=elapsed_ms(started),
                        model=MEMORY_MODEL_NAME,
                        version=MEMORY_MODEL_VERSION,
                        window=len(new_messages),
                    ),
                )
            except Exception:
                logger.error(
                    'Failed to parse memory JSON',
                    exc_info=True,
                    extra=event('MEMORY_EXTRACT', outcome='error'),
                )
                # The raw model output is chat-derived content, not diagnostic metadata --
                # kept at DEBUG rather than shipped at ERROR.
                logger.debug(
                    'Memory extraction content',
                    extra=event('MEMORY_EXTRACT_CONTENT', content=str(updated_memory.content)),
                )

    # Runs after memory regardless of outcome above — including when memory
    # processing is disabled — so fact extraction never depends on the memory flag.
    await update_user_facts(new_messages)


async def delete_old_memories(retention_days: int) -> None:
    cutoff_ts = (datetime.now(UTC) - timedelta(days=retention_days)).timestamp()

    cursor = await mongo.memory.aggregate([
        {'$sort': {'created_at': -1}},
        {'$group': {'_id': '$chat_id', 'latest_id': {'$first': '$_id'}}},
    ])
    latest_ids = {doc['latest_id'] async for doc in cursor}

    result = await mongo.memory.delete_many({
        'created_at': {'$lt': cutoff_ts},
        '_id': {'$nin': list(latest_ids)},
    })
    logger.info(
        'Memory cleanup finished',
        extra=event('MEMORY_CLEANUP', deleted=result.deleted_count, retention_days=retention_days),
    )
