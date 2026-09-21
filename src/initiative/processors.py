import time
from datetime import datetime, timezone

from langchain_core.messages import SystemMessage
from langsmith import traceable

from src import ai, settings
from src.characters.character import Character
from src.initiative.models import InitiativeDecision, InitiativeVerdict
from src.logs import elapsed_ms, event, logger
from src.models import Message, format_ts
from src.prompt_manager import prompt_manager


@traceable
async def evaluate_initiative(
    character: Character, context: list[Message], candidates: list[Message],
) -> InitiativeVerdict:
    rendered_context = '\n'.join(f'▸ {m.ai_format}' for m in context)
    rendered_candidates = '\n'.join(
        f'#{i} ▸ {m.ai_format}'
        for i, m in enumerate(candidates, start=1)
    )

    llm = ai.get_initiative_model(version='gemini-3.8-flash-low')
    model_with_structure = llm.with_structured_output(InitiativeDecision)

    system_prompt = prompt_manager.get_prompt(
        'initiative',
        version='v1',
        current_time=format_ts(datetime.now(timezone.utc)),
        context=rendered_context,
        messages=rendered_candidates,
        bot_nickname=settings.BOT_NICKNAME,
        current_memory=character.memory.initiative_format() if character.memory else None,
        character_description=character.style_prompt,
    )

    started = time.monotonic()
    try:
        evaluation_result: InitiativeDecision = await model_with_structure.ainvoke([
            SystemMessage(content=system_prompt)
        ])
    except Exception:
        logger.error(
            'Error while evaluating initiative', exc_info=True,
            extra=event('INITIATIVE_EVALUATE', outcome='error'),
        )
        return InitiativeVerdict(
            target_message=None,
            score=0,
            reason='Initiative evaluation error'
        )

    if not evaluation_result:
        return InitiativeVerdict(
            target_message=None,
            score=0,
            reason='Initiative evaluation empty response'
        )

    # target_index is 1-based, matching the `#N` labels the candidates were shown
    # with above; it never addresses a context line. Anything outside that range —
    # `0` included — means 'no target', not a failed run: a stray index must not
    # throw away an otherwise good score.
    target_index = evaluation_result.target_index or 0
    target_message = (
        candidates[target_index - 1] if 0 < target_index <= len(candidates) else None
    )
    if evaluation_result.target_index and target_message is None:
        logger.warning(
            'Initiative evaluation target_index out of range',
            extra=event(
                'INITIATIVE_TARGET_OUT_OF_RANGE', target_index=evaluation_result.target_index,
                candidates=len(candidates),
            ),
        )

    # The resolved target's text is high-cardinality chat content, not diagnostic
    # metadata -- kept at DEBUG, separate from the terminal INFO line below.
    logger.debug(
        'Initiative evaluation target',
        extra=event(
            'INITIATIVE_EVALUATE_TARGET',
            text=target_message.embedding_text if target_message else None,
        ),
    )
    # Distance/age from the window's newest message: target_index alone counts from
    # the candidates' oldest, so on its own it can't tell a stale target from a large
    # window. Omitted when there is no target — `candidates[-1]` is only safe because
    # a resolved target_message implies a non-empty candidates list. Also omitted
    # when a `created_at` is missing (not a real-traffic case, but true of hand-built
    # test messages), since target_age_s has nothing to measure against then.
    target_fields = {}
    if target_message and target_message.created_at and candidates[-1].created_at:
        target_fields = {
            'target_distance': len(candidates) - target_index,
            'target_age_s': int(
                (candidates[-1].created_at - target_message.created_at).total_seconds()
            ),
        }
    logger.info(
        'Initiative evaluation result',
        extra=event(
            'INITIATIVE_EVALUATE', outcome='ok', score=evaluation_result.score,
            reason=evaluation_result.reason, target_index=evaluation_result.target_index,
            elapsed_ms=elapsed_ms(started), **target_fields,
        ),
    )
    return InitiativeVerdict(
        target_message=target_message,
        score=evaluation_result.score,
        reason=evaluation_result.reason,
    )
