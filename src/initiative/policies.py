from datetime import datetime, timedelta, timezone

from src import settings
from src.initiative.models import InitiativeVerdict
from src.logs import logger
from src.messages.repository import get_last_message, get_messages_count_since
from src.models import Message, UserRole


async def pre_check(chat_id: int, messages: list[Message]) -> bool:
    if len(messages) < settings.INITIATIVE_TRIGGER_SIZE:
        logger.info(f'Skipping initiative reply in chat {chat_id}: messages count too low')
        return False

    last_message = messages[-1]
    if last_message.role == UserRole.AI:
        logger.info(f'Skipping initiative reply in chat {chat_id}: last message was from AI')
        return False

    last_bot_message = await get_last_message(chat_id, role=UserRole.AI)
    if last_bot_message and last_bot_message.created_at:
        now = datetime.now(timezone.utc)
        cooldown_threshold = now - timedelta(minutes=settings.INITIATIVE_COOLDOWN_MINUTES)
        if last_bot_message.created_at > cooldown_threshold:
            logger.info(f'Skipping initiative reply in chat {chat_id}: bot cooldown not passed')
            return False

        user_messages_count = await get_messages_count_since(
            chat_id, last_bot_message.created_at.timestamp(), role=UserRole.USER,
        )
        if user_messages_count < settings.INITIATIVE_MIN_GAP_MESSAGES:
            logger.info(
                f'Skipping initiative reply in chat {chat_id}: '
                f'messages gap low {user_messages_count=}'
            )
            return False

    return True


async def decide(evaluation: InitiativeVerdict) -> bool:
    if evaluation.score < 0 or evaluation.score > 1:
        return False

    return evaluation.score >= settings.INITIATIVE_SCORE_THRESHOLD
