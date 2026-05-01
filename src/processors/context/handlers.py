import asyncio

from src import settings
from src.logs import logger
from src.memory.models import StructuredMemory
from src.memory.repository import get_last_memory
from src.messages.repository import get_messages, get_messages_count, get_messages_count_since
from src.processors.context.embeddings import get_last_embedding_task, update_chat_embeddings
from src.processors.context.facts import extract_facts
from src.processors.context.memory import extract_memory

CHAT_CONTEXT_LOCK = asyncio.Lock()


async def run_context_checks(chat_id: int):
    await run_memory_checks(chat_id)
    await run_embedding_checks(chat_id)


async def update_chat_context(chat_id: int):
    logger.info(f"Updating memory for chat {chat_id}")
    try:
        async with CHAT_CONTEXT_LOCK:
            await _update_chat_memory(chat_id)
    except Exception as e:
        logger.error(f"Error updating memory for chat {chat_id}: {e}", exc_info=True)


async def _update_chat_memory(chat_id: int):
    last_memory_data = await get_last_memory(chat_id)
    current_memory = last_memory_data.content if last_memory_data else StructuredMemory()

    from_date = last_memory_data.created_at if last_memory_data else None
    new_messages = await get_messages(
        chat_id, size=settings.MESSAGES_MEMORY_MAX_SIZE, from_date=from_date
    )

    if not new_messages:
        logger.info(f"No new messages for memory update in chat {chat_id}")
        return

    await extract_memory(chat_id, current_memory, new_messages)
    await extract_facts(new_messages)


async def run_memory_checks(chat_id: int):
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
        await update_chat_context(chat_id)


async def run_embedding_checks(chat_id: int):
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
