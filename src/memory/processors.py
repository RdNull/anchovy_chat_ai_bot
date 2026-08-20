from langchain_core.messages import SystemMessage
from langsmith import traceable

from src import ai, settings
from src.logs import logger
from src.memory.decay import (
    BIRTH,
    CARRY,
    EVENTS,
    PROMOTE,
    PROMOTE_CANDIDATE,
    VANISH,
    DecayCaps,
    apply_decay,
    reconcile,
    resolve_now,
    summarize_churn,
)
from src.memory.dedup import resolve_attribution_conflicts
from src.memory.keys import RECENT_FIELD
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
    """Reports the cycle's churn counters.

    `lost_recent` sits next to `promote_candidates` on purpose: a candidate is a
    coincidence, not a pairing, so the pair of numbers is the signal — three lost
    `recent` entries against three new traits reads very differently from three
    against none.
    """
    counts = dict.fromkeys(EVENTS, 0)
    for record in churn:
        counts[record.event] += 1

    lost_recent = sum(
        1 for record in churn if record.event == VANISH and record.field == RECENT_FIELD
    )
    logger.info(
        f'MEMORY_CHURN chat_id={chat_id} nicks={len({r.nick for r in churn})} '
        f'carried={counts[CARRY]} added={counts[BIRTH]} '
        f'vanished={counts[VANISH]} lost_recent={lost_recent} '
        f'promoted={counts[PROMOTE]} promote_candidates={counts[PROMOTE_CANDIDATE]}'
    )
    for record in churn:
        if record.event == VANISH:
            logger.info(
                f'MEMORY_CHURN_LOST chat_id={chat_id} nick={record.nick} '
                f'field={record.field} text={record.text}'
            )


def _log_evictions(chat_id: int, evictions: list) -> None:
    for record in evictions:
        action = 'evicted' if record.applied else 'would_evict'
        logger.info(
            f'MEMORY_DECAY chat_id={chat_id} nick={record.nick} field={record.field} '
            f'action={action} reason={record.reason} text={record.text}'
        )


def _log_trait_overflow(chat_id: int, memory: StructuredMemory, traits_keep: int) -> None:
    """Reports participants over the trait cap, before eviction reshapes the list.

    Whether trait eviction needs a real rule at all is an open question, and this
    is the number that answers it: if overflow is rare, the placeholder rule never
    mattered.

    Takes the cap rather than reading it, so the threshold measured against is the
    one eviction is about to apply and not a second reading of `settings`.
    """
    for nick, info in memory.participants.items():
        if len(info.traits) > traits_keep:
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

    prior_decay = current_memory.decay if current_memory else {}
    caps = DecayCaps.from_settings()

    decay = reconcile(
        updated_memory, prior_decay, resolve_now([m.created_at for m in new_messages])
    )
    # Must precede eviction: the cap is what reshapes the list, so afterwards there
    # is no overflow left to count.
    _log_trait_overflow(chat_id, updated_memory, caps.traits_keep)

    evictions = apply_decay(updated_memory, decay, caps)
    _log_evictions(chat_id, evictions)

    # Churn last, so it describes the memory that is actually saved rather than the
    # one the model emitted.
    churn = summarize_churn(updated_memory, prior_decay, decay, guard_records, evictions)
    _log_churn(chat_id, churn)

    try:
        await save_memory(chat_id, updated_memory, decay)
        logger.info(f'Memory updated and saved for chat {chat_id}')
    except Exception as e:
        logger.error(
            f'Failed to parse memory JSON for chat {chat_id}: {e}\nContent: {updated_memory}'
        )
