from datetime import datetime, timedelta, timezone

from src import settings
from src.initiative.models import InitiativeVerdict
from src.initiative.repository import count_replied_since
from src.logs import event, logger
from src.messages.repository import get_last_message, get_messages_count_since
from src.models import Message, UserRole

# The daily cap's window. A parameter on the repository function rather than baked
# into it, so a rolling window of any length is a call away — this is just the one
# `pre_check` currently asks for.
_DAILY_LIMIT_WINDOW = timedelta(hours=24)


def split_at_gap(messages: list[Message], gap_minutes: float) -> list[Message]:
    """Keeps only the messages after the newest gap larger than gap_minutes.

    messages must be chronological. Returns a suffix of the input (possibly the
    whole thing, never empty if the input is non-empty).
    """
    gap = timedelta(minutes=gap_minutes)
    for i in range(len(messages) - 1, 0, -1):
        previous, current = messages[i - 1], messages[i]
        if not previous.created_at or not current.created_at:
            continue
        if current.created_at - previous.created_at > gap:
            return messages[i:]

    return messages


async def pre_check(chat_id: int, messages: list[Message]) -> bool:
    if len(messages) < settings.INITIATIVE_TRIGGER_SIZE:
        logger.info(
            'Skipping initiative reply',
            extra=event('INITIATIVE_SKIPPED', reason='messages_too_few', count=len(messages)),
        )
        return False

    # The chat's true newest, not `messages[-1]`: the window is fetched before this
    # runs, so a message can land in between — the bot's own reply included — and this
    # guard has to see what was actually said last.
    last_message = await get_last_message(chat_id)
    if last_message and last_message.role == UserRole.AI:
        logger.info(
            'Skipping initiative reply', extra=event('INITIATIVE_SKIPPED', reason='last_from_ai'),
        )
        return False

    last_bot_message = await get_last_message(chat_id, role=UserRole.AI)
    if last_bot_message and last_bot_message.created_at:
        now = datetime.now(timezone.utc)
        cooldown_threshold = now - timedelta(minutes=settings.INITIATIVE_COOLDOWN_MINUTES)
        if last_bot_message.created_at > cooldown_threshold:
            logger.info(
                'Skipping initiative reply', extra=event('INITIATIVE_SKIPPED', reason='cooldown'),
            )
            return False

        user_messages_count = await get_messages_count_since(
            chat_id, last_bot_message.created_at.timestamp(), role=UserRole.USER,
        )
        if user_messages_count < settings.INITIATIVE_MIN_GAP_MESSAGES:
            logger.info(
                'Skipping initiative reply',
                extra=event('INITIATIVE_SKIPPED', reason='gap_too_small', count=user_messages_count),
            )
            return False

    # Last gate, after the other four, so their skip-reason counts stay comparable.
    # Runs before the claim: a capped chat costs no LLM call and does not advance
    # the watermark, like the gates above it. The two-claims-in-flight race exists
    # for the cooldown too and is ignored here.
    replied_count = await count_replied_since(chat_id, _DAILY_LIMIT_WINDOW)
    if replied_count >= settings.INITIATIVE_DAILY_LIMIT:
        logger.info(
            'Skipping initiative reply',
            extra=event(
                'INITIATIVE_SKIPPED', reason='daily_limit', count=replied_count,
                limit=settings.INITIATIVE_DAILY_LIMIT,
            ),
        )
        return False

    return True


async def decide(evaluation: InitiativeVerdict) -> bool:
    if evaluation.score < 0 or evaluation.score > 1:
        return False

    return evaluation.score >= settings.INITIATIVE_SCORE_THRESHOLD
