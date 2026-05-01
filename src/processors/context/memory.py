from typing import Any

from langchain_core.messages import SystemMessage
from langsmith import traceable

from src import ai
from src.logs import logger
from src.memory.models import StructuredMemory
from src.memory.repository import save_memory
from src.models import Message
from src.prompt_manager import prompt_manager


@traceable
async def extract_memory(
    chat_id: int,
    current_memory: StructuredMemory,
    new_messages: list[Message],
):
    formatted_messages = "\n".join([m.ai_format for m in new_messages])

    llm = ai.get_memory_model(version='v2')
    model_with_structure = llm.with_structured_output(StructuredMemory)

    system_prompt = prompt_manager.get_prompt(
        'memory',
        version='v2',
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
