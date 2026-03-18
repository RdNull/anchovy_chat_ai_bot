import re
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage, SystemMessage

from src import ai, settings
from src.logs import logger
from src.messages.history import (
    get_history, get_last_recap, get_last_recaps, save_recap,
)
from src.models import RecapType

RECAP_PROMPT = """
Ты создаёшь краткую сводку сообщений для персонажа Telegram-бота.
Задача: объединить предыдущую сводку и новые сообщения или несколько сводок в одну короткую сводку.

Правила:
* Пиши 1–5 предложений.
* Передавай только факты из сообщений и их эмоции, если они явно выражены.
* Упоминай важные вопросы, события и конфликты.
* Не делай предположений и не добавляй события.
* Если информация неясна — пропусти её.
* Игнорируй формат чата: не используй кавычки, скобки, двоеточия и метки.
* Если в сообщениях бот пишет как `<имя>(персонаж)`, используй имя персонажа.
* Если ничего не происходило - верни в ответе `(нет данных)` 

Ответ должен содержать только сводку.
"""

EMPTY_DATA_PATTERN = re.compile(r'^\s*\(?\s*нет\s+данных\s*\)?[\s.]*$', re.IGNORECASE)


async def generate_and_save_recap(
    chat_id: int, recap_type: RecapType = RecapType.PERIODIC
):
    logger.info(f"Generating {recap_type.value} recap for chat {chat_id}")

    recap_started_at = datetime.now(timezone.utc)
    previous_recap = await _get_previous_recap_data(chat_id, recap_type)
    recap_text = await _get_recap_text(chat_id, recap_type)

    if not recap_text or not recap_text.strip():
        logger.info(f"No new messages for {recap_type.value} recap in chat {chat_id}")
        return

    data_prompt = (
        "Предыдущий контекст:\n"
        f"{previous_recap or 'НЕТ ДАННЫХ'}\n"
        "Материал для сводки:\n"
        f"{recap_text}"
    )

    llm = ai.get_recap_model()

    messages = [
        SystemMessage(content=RECAP_PROMPT),
        HumanMessage(content=data_prompt)
    ]

    try:
        response = await llm.ainvoke(messages)
        recap_text = response.content
        if EMPTY_DATA_PATTERN.match(recap_text):
            logger.info(f"Empty recap for {recap_type.value} recap in chat {chat_id}")
            return

        await save_recap(chat_id, recap_text, recap_type=recap_type, created_at=recap_started_at)
        logger.info(f"{recap_type.value.capitalize()} recap saved for chat {chat_id}")
    except Exception as e:
        logger.error(f"Error generating recap for chat {chat_id}: {e}", exc_info=True)


async def _get_previous_recap_data(chat_id: int, recap_type: RecapType) -> str:
    recap = await get_last_recap(chat_id, recap_type=recap_type)
    return recap.text if recap else None


async def _get_recap_text(chat_id: int, recap_type: RecapType) -> str | None:
    if recap_type == RecapType.PERIODIC:
        return await _get_new_messages(chat_id)

    recap_target = RecapType.HOURLY if recap_type == RecapType.DAILY else RecapType.PERIODIC
    recap = await get_last_recap(chat_id, recap_type=recap_target)
    last_recap_date = recap.created_at if recap else None
    last_recaps = await _get_last_recaps_data(chat_id, recap_target, from_date=last_recap_date)

    return "\n".join(last_recaps) if last_recaps else None


async def _get_new_messages(chat_id: int) -> str | None:
    last_recap = await get_last_recap(chat_id, recap_type=RecapType.PERIODIC)
    last_messages = await get_history(
        chat_id, size=settings.MESSAGES_RECAP_MAX_SIZE, from_date=last_recap.created_at
    )

    return "\n".join([m.ai_format() for m in last_messages]) if last_messages else None


async def _get_last_recaps_data(
    chat_id: int, recap_type: RecapType, from_date: datetime | None
) -> list[str]:
    recaps = await get_last_recaps(chat_id, recap_type=recap_type, from_date=from_date, size=20)
    return [r.text for r in recaps]
