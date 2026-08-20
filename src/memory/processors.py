from langchain_core.messages import SystemMessage
from langsmith import traceable

from src import ai, settings
from src.logs import logger
from src.memory.decay import (
    BIRTH,
    CARRY,
    PROMOTE,
    PROMOTE_CANDIDATE,
    VANISH,
    apply_decay,
    reconcile,
    resolve_now,
)
from src.memory.dedup import resolve_attribution_conflicts
from src.memory.models import MemoryData, StructuredMemory
from src.memory.repository import save_memory
from src.models import Message
from src.prompt_manager import prompt_manager


def _prompt_memory(current: MemoryData | None) -> str:
    """Renders the current memory as the model should see it.

    `active_topics` is stripped. The prompt used to carry a rule telling the model
    to drop topics nobody had touched in 30 minutes; deleting them from the input
    enforces the same thing with zero compliance required — the model cannot carry
    forward what it never sees, and there is no reformulation escape hatch.

    Copies rather than mutates: the same object is the incumbent map for the
    attribution guard, and the caller holds a reference to it.
    """
    if not current:
        return '{}'

    for_prompt = current.content.model_copy(deep=True)
    for_prompt.state.active_topics = []
    return for_prompt.model_dump_json()


def _log_churn(chat_id: int, churn: list) -> None:
    counts = {BIRTH: 0, CARRY: 0, VANISH: 0, PROMOTE: 0, PROMOTE_CANDIDATE: 0}
    for record in churn:
        counts[record.event] += 1

    logger.info(
        f'MEMORY_CHURN chat_id={chat_id} nicks={len({r.nick for r in churn})} '
        f'carried={counts[CARRY]} added={counts[BIRTH]} '
        f'vanished={counts[VANISH]} promoted={counts[PROMOTE]} '
        f'promote_candidates={counts[PROMOTE_CANDIDATE]}'
    )
    for record in churn:
        if record.event == VANISH:
            logger.info(f'MEMORY_CHURN_LOST chat_id={chat_id} nick={record.nick} text={record.text}')


def _log_evictions(chat_id: int, evictions: list) -> None:
    for record in evictions:
        action = 'evicted' if record.applied else 'would_evict'
        logger.info(
            f'MEMORY_DECAY chat_id={chat_id} nick={record.nick} field={record.field} '
            f'action={action} reason={record.reason} text={record.text}'
        )


def _log_trait_overflow(chat_id: int, memory: StructuredMemory) -> None:
    """Reports participants over the trait cap, before eviction reshapes the list.

    Whether trait eviction needs a real rule at all is an open question, and this
    is the number that answers it: if overflow is rare, the placeholder rule never
    mattered.
    """
    for nick, info in memory.participants.items():
        if len(info.traits) > settings.TRAITS_KEEP:
            logger.info(f'MEMORY_TRAIT_OVERFLOW chat_id={chat_id} nick={nick} count={len(info.traits)}')


@traceable
async def extract_memory(
    chat_id: int,
    current_memory: MemoryData | None,
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
        current_memory=_prompt_memory(current_memory),
        new_messages=formatted_messages
    )

    updated_memory: StructuredMemory = await model_with_structure.ainvoke([
        SystemMessage(content=system_prompt)
    ])
    if not updated_memory:
        logger.error(f'No memory extracted for chat {chat_id}')
        return

    guard_records = resolve_attribution_conflicts(
        updated_memory, current_memory.content if current_memory else None
    )
    for record in guard_records:
        action = 'dropped' if record.removed else 'kept'
        kept = record.kept_owner or '-'
        logger.info(
            f'MEMORY_ATTRIBUTION_CONFLICT chat_id={chat_id} action={action} '
            f'reason={record.reason} owner={record.owner} kept={kept} '
            f'field={record.field} text={record.text}'
        )

    decay, churn = reconcile(
        updated_memory,
        current_memory.decay if current_memory else {},
        guard_records,
        resolve_now([m.created_at for m in new_messages]),
    )
    _log_churn(chat_id, churn)
    _log_trait_overflow(chat_id, updated_memory)

    evictions = apply_decay(
        updated_memory,
        decay,
        settings.ENABLE_MEMORY_DECAY,
        settings.TRAITS_KEEP,
        settings.RECENT_KEEP,
        settings.RECENT_MAX_CYCLES,
    )
    _log_evictions(chat_id, evictions)

    try:
        await save_memory(chat_id, updated_memory, decay)
        logger.info(f'Memory updated and saved for chat {chat_id}')
    except Exception as e:
        logger.error(
            f'Failed to parse memory JSON for chat {chat_id}: {e}\nContent: {updated_memory}'
        )
