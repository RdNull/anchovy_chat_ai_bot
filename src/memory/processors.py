from langchain_core.messages import SystemMessage
from langsmith import traceable

from src import ai, settings
from src.logs import logger
from src.memory.dedup import resolve_attribution_conflicts
from src.memory.models import StructuredMemory
from src.memory.repository import save_memory
from src.models import Message
from src.prompt_manager import prompt_manager


@traceable
async def extract_memory(
    chat_id: int,
    current_memory: StructuredMemory | None,
    new_messages: list[Message],
):
    if not settings.ENABLE_MEMORY_PROCESSING:
        await save_memory(chat_id, StructuredMemory())
        logger.info(f'Memory processing disabled; saved empty memory for chat {chat_id}')
        return

    llm = ai.get_memory_model(version='v3-cheap')
    model_with_structure = llm.with_structured_output(StructuredMemory)

    formatted_messages = '\n'.join([m.ai_format for m in new_messages])
    system_prompt = prompt_manager.get_prompt(
        'memory',
        version='v4',
        current_memory=current_memory.model_dump_json() if current_memory else '{}',
        new_messages=formatted_messages
    )

    updated_memory: StructuredMemory = await model_with_structure.ainvoke([
        SystemMessage(content=system_prompt)
    ])
    if not updated_memory:
        logger.error(f'No memory extracted for chat {chat_id}')
        return

    for record in resolve_attribution_conflicts(updated_memory, current_memory):
        action = 'dropped' if record.removed else 'kept'
        kept = record.kept_owner or '-'
        logger.info(
            f'MEMORY_ATTRIBUTION_CONFLICT chat_id={chat_id} action={action} '
            f'reason={record.reason} owner={record.owner} kept={kept} '
            f'field={record.field} text={record.text}'
        )

    try:
        await save_memory(chat_id, updated_memory.trim())
        logger.info(f'Memory updated and saved for chat {chat_id}')
    except Exception as e:
        logger.error(
            f'Failed to parse memory JSON for chat {chat_id}: {e}\nContent: {updated_memory}'
        )
