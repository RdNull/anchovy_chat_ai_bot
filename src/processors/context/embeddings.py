import asyncio
from datetime import datetime, timezone

from src import mongo as db, settings
from src.embeddings.messages import messages_embeddings_client
from src.logs import logger
from src.messages.repository import get_messages
from src.models import EmbeddingTask, Message, RelatedMessagesData

# `run_context_checks` is a detached task per message, so the watermark's
# read-then-write is not atomic on its own: concurrent calls would all read the same
# checkpoint and each pay for the same embedding pass (observed at 8x in prod).
# Module-level and shared by every chat, like CHAT_CONTEXT_LOCK, and held across the
# whole body rather than released before the save: a failed Qdrant save must leave
# the watermark unadvanced so the window is retried, not lost.
EMBEDDING_TASK_LOCK = asyncio.Lock()


async def search_related_messages(user_message: Message) -> list[RelatedMessagesData]:
    query = user_message.text
    if user_message.media:
        query = f'{query}|{user_message.media.description}|{user_message.media.ocr_text}'

    if len(query) < 5:  # no need to spend time on spam messages
        return []

    return await messages_embeddings_client.search(
        chat_id=user_message.chat_id, query=query, limit=settings.EMBEDDINGS_SEARCH_MAX_SIZE
    )


async def update_chat_embeddings(chat_id: int):
    logger.info(f"Updating embeddings for chat {chat_id}")

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
            logger.info(f"No new messages for embeddings update in chat {chat_id}")
            return

        try:
            await messages_embeddings_client.save(messages)
            await save_embedding_task(chat_id, messages[-1].created_at)
            logger.info(f"Embeddings updated for chat {chat_id}")
        except Exception as e:
            logger.error(f"Error updating embeddings for chat {chat_id}: {e}", exc_info=True)


async def get_last_embedding_task(chat_id: int) -> EmbeddingTask | None:
    logger.debug(f"Getting embedding for chat {chat_id}")
    embedding_task = await db.embedding_tasks.find_one(
        {'chat_id': chat_id}, sort=[('created_at', -1)]
    )
    if not embedding_task:
        return None

    return EmbeddingTask(**embedding_task)


async def save_embedding_task(chat_id: int, last_message_time: datetime):
    logger.info(f"Saving embedding for chat {chat_id}")
    data = {
        'chat_id': chat_id,
        'last_message_time': last_message_time.timestamp(),
        'created_at': datetime.now(timezone.utc).timestamp()
    }
    await db.embedding_tasks.insert_one(data)
