import asyncio
from dataclasses import dataclass
from datetime import datetime

from telegram.constants import ChatAction

from src import settings
from src.characters.character import Character
from src.characters.reply import Replier
from src.initiative.models import InitiativeVerdict
from src.initiative.policies import decide, pre_check, split_at_gap
from src.initiative.processors import evaluate_initiative
from src.initiative.repository import (
    get_last_initiative_run, mark_initiative_replied, save_initiative_run,
)
from src.logs import event, logger
from src.memory.repository import get_last_memory
from src.messages.repository import fetch_last_messages
from src.messages.utils import get_chat_character, send_chat_action
from src.models import Message
from src.running_app import get_bot

# `run_context_checks` is a detached task per message, so the watermark's
# read-then-write is not atomic on its own: two messages arriving together would both
# read the same watermark, claim the same window and answer it twice. Module-level and
# shared by every chat, like `CHAT_CONTEXT_LOCK` — the critical section is short and
# ends before the LLM call.
INITIATIVE_RUN_LOCK = asyncio.Lock()


async def run_initiative_checks(chat_id: int):
    if not settings.INITIATIVE_CHECKS_ENABLED:
        return

    logger.debug('Running initiative checks', extra=event('INITIATIVE_CHECK_START'))
    claim = await _claim_window(chat_id)
    if not claim.candidates:
        return

    last_memory = await get_last_memory(chat_id)
    character: Character = await get_chat_character(chat_id=chat_id, memory=last_memory)
    evaluation = await evaluate_initiative(character, claim.context, claim.candidates)
    if not await decide(evaluation):
        logger.info(
            'Initiative run skipped',
            extra=event(
                'INITIATIVE_SKIPPED', reason='below_threshold', score=evaluation.score,
                threshold=settings.INITIATIVE_SCORE_THRESHOLD,
            ),
        )
        return

    if not settings.INITIATIVE_ENABLED:
        logger.info(
            'Initiative run would reply (dry run)',
            extra=event('INITIATIVE_DRY_RUN', score=evaluation.score, reason=evaluation.reason),
        )
        return

    # Stamped before the task is spawned, not inside it: this reserves the day's slot
    # at decision time and keeps the daily-cap gate simple, and it avoids threading a
    # run id into a detached task for a write that has nothing to do with the reply.
    await mark_initiative_replied(claim.run_id)

    asyncio.create_task(
        _run_initiative_reply(chat_id=chat_id, character=character, evaluation=evaluation)
    )


@dataclass
class ClaimedWindow:
    """What one `_claim_window` call hands back to its caller.

    `run_id` is the claimed run document's id, used by the send path to stamp
    `replied_at`; it is `None` exactly when `candidates` is empty (pre-checks said no).
    """
    context: list[Message]
    candidates: list[Message]
    run_id: str | None


async def _claim_window(chat_id: int) -> ClaimedWindow:
    """Reads the pending window and advances the watermark under a single lock.

    Returns an empty `ClaimedWindow` when the pre-checks say no. The gap split runs
    before `pre_check`, so `TRIGGER_SIZE` counts messages in one live conversation
    rather than messages since the last judgment.
    """
    async with INITIATIVE_RUN_LOCK:
        last_initiative_run = await get_last_initiative_run(chat_id)
        watermark = last_initiative_run.last_message_time if last_initiative_run else None
        sequence = await _get_messages(chat_id, watermark)
        context, candidates = _split_window(chat_id, sequence, watermark)

        if not await pre_check(chat_id, candidates):
            logger.info('Initiative run pre-checks failed', extra=event('INITIATIVE_PRECHECK_FAILED'))
            return ClaimedWindow(context=[], candidates=[], run_id=None)

        logger.info(
            'Triggering initiative run',
            extra=event('INITIATIVE_CLAIMED', candidates=len(candidates)),
        )
        run_id = await save_initiative_run(chat_id, last_message_time=candidates[-1].created_at)

    return ClaimedWindow(context=context, candidates=candidates, run_id=run_id)


async def _get_messages(chat_id: int, watermark: datetime | None) -> list[Message]:
    # Newest-first against the cap, so the judge reads the conversation as it is now.
    # A backlog past the cap is dropped, not deferred like the memory window: a moment
    # that has passed is not worth answering, and after a blocked stretch (cooldown)
    # an oldest-first read judged the conversation from an hour ago. With no prior
    # run there is no lower bound, which anchors a first run on the latest messages
    # rather than the chat's oldest history, and there is no context to fetch either.
    candidates = await fetch_last_messages(
        chat_id,
        size=settings.INITIATIVE_RUN_MESSAGES_MAX_SIZE,
        from_date=watermark,
        sort_order=-1,
    )
    if not watermark:
        return candidates

    # Read-only context from before the watermark, inclusive: the watermark message
    # is the newest message the previous run judged, and the gap split needs it to
    # measure the gap into the candidate region correctly.
    context = await fetch_last_messages(
        chat_id,
        size=settings.INITIATIVE_CONTEXT_SIZE,
        to_date=watermark,
        to_date_inclusive=True,
        sort_order=-1,
    )
    return [*context, *candidates]


def _split_window(
    _chat_id: int, sequence: list[Message], watermark: datetime | None,
) -> tuple[list[Message], list[Message]]:
    """Cuts `sequence` at its newest large gap, then re-partitions around the watermark.

    `_chat_id` is unused now that `INITIATIVE_WINDOW` reads it from the bound context
    rather than an explicit field; kept positional so the call site and the direct-call
    tests need no change.

    Cutting inside the candidate region empties the context — a fresh conversation
    with nothing worth carrying forward. Cutting inside the context region merely
    trims it. Messages dropped below the cut are not queued or deferred: they can
    still resurface as context on a later run, matching the "a passed moment is
    dropped, not deferred" rule the plain backlog cap already follows.
    """
    window = split_at_gap(sequence, settings.INITIATIVE_GAP_MINUTES)
    cut_at = len(sequence) - len(window)
    cut_gap_minutes = None
    if cut_at:
        cut_gap_minutes = (
            window[0].created_at - sequence[cut_at - 1].created_at
        ).total_seconds() / 60

    context = [m for m in window if watermark is not None and m.created_at <= watermark]
    candidates = [m for m in window if watermark is None or m.created_at > watermark]

    logger.info(
        'Initiative window built',
        extra=event(
            'INITIATIVE_WINDOW', fetched=len(sequence), cut_gap_minutes=cut_gap_minutes,
            context_count=len(context), candidate_count=len(candidates),
        ),
    )
    return context, candidates


async def _run_initiative_reply(
    chat_id: int, character: Character, evaluation: InitiativeVerdict,
):
    logger.info('Initiative run triggered', extra=event('INITIATIVE_REPLY_SENT'))
    await send_chat_action(chat_id, ChatAction.TYPING)
    # No `character.memory = ...` here: `get_character` hands out a shared singleton,
    # and this runs as a detached task, so re-stamping it from a background task can
    # land another chat's memory in a reply already being built. `run_initiative_checks`
    # has already passed this chat's snapshot into `get_chat_character`.
    bot = get_bot()
    replier = Replier(
        bot=bot,
        character=character,
        chat_id=chat_id,
        target=evaluation.target_message,
    )

    # reload messages to fetch messages that might be sent in-between initiative evaluation
    last_messages = await fetch_last_messages(chat_id, size=settings.LAST_MESSAGES_SIZE)
    await character.respond(replier, _with_target(last_messages, evaluation.target_message))


def _with_target(messages: list[Message], target: Message | None) -> list[Message]:
    """Keeps the evaluated target inside the reply window.

    The evaluation window is the wider of the two, so a target picked from its older
    half can fall outside `LAST_MESSAGES_SIZE`: the character would get no `[TARGET]`
    marker while the reply still quotes that message. It is older than everything in
    the window by construction, so it goes at the front.
    """
    if not target or not target.id or any(m.id == target.id for m in messages):
        return messages

    return [target, *messages]
