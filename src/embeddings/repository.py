from datetime import datetime, UTC

from src import mongo as db
from src.embeddings.models import EmbeddingTask
from src.logs import event, logger


async def get_last_embedding_task(chat_id: int) -> EmbeddingTask | None:
    logger.debug('Getting embedding task', extra=event('EMBEDDING_TASK_FETCH'))
    embedding_task = await db.embedding_tasks.find_one(
        {'chat_id': chat_id}, sort=[('created_at', -1)]
    )
    if not embedding_task:
        return None

    return EmbeddingTask(**embedding_task)


async def save_embedding_task(chat_id: int, last_message_time: datetime):
    logger.debug('Saving embedding task', extra=event('EMBEDDING_TASK_SAVED'))
    data = {
        'chat_id': chat_id,
        'last_message_time': last_message_time.timestamp(),
        'created_at': datetime.now(UTC).timestamp(),
    }
    await db.embedding_tasks.insert_one(data)
