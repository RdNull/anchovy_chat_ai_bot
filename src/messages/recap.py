from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage

from src import ai, settings
from src.logs import logger
from src.messages.history import (
    get_history, get_last_recap, get_last_recap_timestamp, get_last_recaps,
    get_messages_count_since, save_recap,
)
from src.models import RecapType

RECAP_PROMPT = """
Ты создаёшь краткую сводку сообщений для персонажа Telegram-бота.
Задача: объединить предыдущую сводку и новые сообщения в одну короткую сводку.

Правила:
* Пиши 1–5 предложений.
* Передавай только факты из сообщений и их эмоции, если они явно выражены.
* Упоминай важные вопросы, события и конфликты.
* Не добавляй ничего, чего нет в сообщениях.
* Не делай предположений и не добавляй события.
* Если информация неясна — пропусти её.
* Игнорируй формат чата: не используй кавычки, скобки, двоеточия и метки.
* Если бот пишет как `<имя>(персонаж)`, используй имя персонажа.

Пример:
Вход:
Антон: Привет, кто идёт на встречу?
Ирина (в ответ на "Привет, кто идёт на встречу?"): Я не приду, занята
Алексей: Я буду
ShizoDedBot(Агафон): А мы на мусорку собираемся?

Сводка:
`Антон спрашивает о встрече, Ирина отказывается, Алексей соглашается, Агафон спрашивает идут ли они на мусорку.`

Ответ должен содержать только сводку.
"""


async def generate_and_save_recap(
    chat_id: int, recap_type: RecapType = RecapType.PERIODIC
):
    logger.info(f"Generating {recap_type.value} recap for chat {chat_id}")

    previous_recap = await _get_previous_recap_data(chat_id, recap_type)
    recap_text = await _get_recap_text(chat_id, recap_type)

    data_prompt = (
        "Контекст:\n"
        f"{previous_recap})\n"
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
        await save_recap(chat_id, recap_text, recap_type=recap_type)
        logger.info(f"{recap_type.value.capitalize()} recap saved for chat {chat_id}")
        return recap_text
    except Exception as e:
        logger.error(f"Error generating recap for chat {chat_id}: {e}", exc_info=True)
        return None


async def _get_previous_recap_data(chat_id: int, recap_type: RecapType) -> str:
    recap = await get_last_recap(chat_id, recap_type=recap_type)
    return recap.text if recap else 'НЕТ ДАННЫХ'


async def _get_recap_text(chat_id: int, recap_type: RecapType) -> str | None:
    if recap_type == RecapType.PERIODIC:
        return await _get_new_messages(chat_id)

    recap_target = RecapType.HOURLY if recap_type == RecapType.DAILY else RecapType.PERIODIC
    recap = await get_last_recap(chat_id, recap_type=recap_target)
    last_recap_date = recap.created_at if recap else None
    last_recaps = await _get_last_recaps_data(chat_id, recap_target, from_date=last_recap_date)

    return "\n".join(last_recaps) if last_recaps else None


async def _get_new_messages(chat_id: int) -> str:
    last_recap_timestamp = await get_last_recap_timestamp(chat_id, recap_type=RecapType.PERIODIC)
    if last_recap_timestamp:
        messages_count = await get_messages_count_since(chat_id, last_recap_timestamp)
        size = max(settings.MESSAGES_RECAP_SIZE, messages_count)
    else:
        size = settings.MESSAGES_RECAP_SIZE

    last_messages = await get_history(chat_id, size=size)

    return "\n".join([m.ai_format() for m in last_messages])


async def _get_last_recaps_data(
    chat_id: int, recap_type: RecapType, from_date: datetime | None
) -> list[str]:
    recaps = await get_last_recaps(chat_id, recap_type=recap_type, from_date=from_date, size=20)
    return [r.text for r in recaps]
