from src import settings
from src.logs import logger
from src.messages.repository import get_last_memory, get_messages_count, get_messages_count_since
from src.processors.context.embeddings import get_last_embedding_task, update_chat_embeddings
from src.processors.context.memory import update_chat_memory


async def run_context_checks(chat_id: int):
    await _run_memory_checks(chat_id)
    await _run_embedding_checks(chat_id)


async def _run_memory_checks(chat_id: int):
    last_memory = await get_last_memory(chat_id)
    if last_memory:
        messages_count = await get_messages_count_since(
            chat_id, last_memory.created_at.timestamp()
        )
    else:
        messages_count = await get_messages_count(chat_id)

    if messages_count >= settings.LAST_MESSAGES_SIZE:
        logger.info(
            f"Triggering periodic memory update for chat {chat_id} (count since last: {messages_count})"
        )
        await update_chat_memory(chat_id)


async def _run_embedding_checks(chat_id: int):
    last_embeddings_task = await get_last_embedding_task(chat_id)
    if last_embeddings_task:
        messages_count = await get_messages_count_since(
            chat_id, last_embeddings_task.last_message_time.timestamp()
        )
    else:
        messages_count = await get_messages_count(chat_id)

    if messages_count >= settings.LAST_MESSAGES_SIZE:
        logger.info(
            f"Triggering periodic embedding update for chat {chat_id} (count since last: {messages_count})"
        )
        await update_chat_embeddings(chat_id)
