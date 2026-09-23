import asyncio
import time

from src import settings
from src.embeddings.messages import messages_embeddings_client
from src.embeddings.repository import get_last_embedding_task, save_embedding_task
from src.logs import elapsed_ms, event, logger
from src.messages.repository import get_messages, get_messages_count, get_messages_count_since

# The read-then-write watermark below is not atomic on its own: `run_followups` is a
# detached task per message, so concurrent calls would all read the same checkpoint
# and each pay for the same embedding pass (observed at 8x in prod). Module-level and
# shared by every chat, like `memory.handlers.MEMORY_UPDATE_LOCK`, and held across the
# whole body rather than released before the save: a failed Qdrant save must leave
# the watermark unadvanced so the window is retried, not lost.
EMBEDDING_TASK_LOCK = asyncio.Lock()


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


async def update_chat_embeddings(chat_id: int):
    logger.debug('Updating embeddings', extra=event('EMBEDDING_UPDATE_START'))

    async with EMBEDDING_TASK_LOCK:
        last_embedding_task = await get_last_embedding_task(chat_id)
        from_date = last_embedding_task.last_message_time if last_embedding_task else None
        # Oldest first, for the same reason as the memory pass: the checkpoint below
        # is the newest message actually embedded, so overflow is deferred, never
        # dropped.
        messages = await get_messages(
            chat_id,
            size=settings.MESSAGES_EMBEDDINGS_MAX_SIZE,
            from_date=from_date,
            sort_order=1,
        )

        if len(messages) < settings.EMBEDDINGS_MIN_SIZE:
            logger.info(
                'No new messages for embeddings update',
                extra=event('EMBEDDING_UPDATE', outcome='empty'),
            )
            return

        started = time.monotonic()
        try:
            chunks = await messages_embeddings_client.save(messages)
            await save_embedding_task(chat_id, messages[-1].created_at)
            logger.info(
                'Embeddings updated',
                extra=event(
                    'EMBEDDING_UPDATE', outcome='ok', elapsed_ms=elapsed_ms(started),
                    messages=len(messages), chunks=chunks,
                ),
            )
        except Exception:
            logger.error(
                'Error updating embeddings', exc_info=True,
                extra=event('EMBEDDING_UPDATE', outcome='error'),
            )
