import asyncio
from typing import Any

from langchain_core.messages import SystemMessage
from langsmith import traceable

from src import ai, settings
from src.logs import logger
from src.messages.history import get_history, get_last_memory, save_memory
from src.models import StructuredMemory
from src.prompt_manager import prompt_manager

MEMORY_LOCK = asyncio.Lock()


async def update_chat_memory(chat_id: int):
    logger.info(f"Updating memory for chat {chat_id}")
    async with MEMORY_LOCK:
        try:
            await _update_chat_memory(chat_id)
        except Exception as e:
            logger.error(f"Error updating memory for chat {chat_id}: {e}", exc_info=True)


@traceable
async def _update_chat_memory(chat_id: int):
    last_memory_data = await get_last_memory(chat_id)
    current_memory = last_memory_data.content if last_memory_data else StructuredMemory()

    from_date = last_memory_data.created_at if last_memory_data else None
    new_messages = await get_history(
        chat_id, size=settings.MESSAGES_MEMORY_MAX_SIZE, from_date=from_date
    )

    if not new_messages:
        logger.info(f"No new messages for memory update in chat {chat_id}")
        return

    formatted_messages = "\n".join([m.ai_format for m in new_messages])

    llm = ai.get_memory_model(version='v1')
    model_with_structure = llm.with_structured_output(StructuredMemory)

    system_prompt = prompt_manager.get_prompt(
        'memory',
        version='v1',
        current_memory=current_memory.model_dump_json(indent=2),
        new_messages=formatted_messages
    )

    updated_memory: StructuredMemory | dict[str, Any] = await model_with_structure.ainvoke([
        SystemMessage(content=system_prompt)
    ])

    try:
        await save_memory(chat_id, updated_memory)
        logger.info(f"Memory updated and saved for chat {chat_id}")
    except Exception as e:
        logger.error(
            f"Failed to parse memory JSON for chat {chat_id}: {e}\nContent: {updated_memory}"
        )
