from langchain_core.messages import HumanMessage, SystemMessage

from src import ai, settings
from src.logs import logger
from src.messages.history import (
    get_history, get_last_recap, get_last_recap_timestamp, get_messages_count_since, save_recap,
)
from src.models import Message

RECAP_PROMPT = """
Ты — аналитический помощник для персонажа в Telegram.
Твоя задача — из последних сообщений создать короткую сводку, чтобы персонаж мог на неё ориентироваться при ответе.

Правила:
-Сводка должна быть 1–5 предложений, максимально сжато.
-Пересказывай суть сообщений и ключевые реакции участников.
-Сохраняй настроение и эмоции, если они явно выражены.
-Упоминай важные события, вопросы и конфликты, которые могут повлиять на поведение персонажа.
-Игнорируй формат исходного чата: убирай кавычки, скобки, двоеточия, метки.
-Не добавляй своих комментариев, интерпретаций или советов — только факты и эмоции из сообщений.
-Для ответов на сообщения просто включай суть того, на что отвечали, без цитат.
-Учитывай под каким персонажем пишет бот. Пример: `<имя бота>(Агафон)` ->  персонаж "Агафон"

Пример:
Вход:

Антон: Привет, кто идёт на встречу?  
Ирина (в ответ на "Привет, кто идёт на встречу?"): Я не приду, занята  
Алексей: Я буду
ShizoDedBot(Агафон): А мы на мусорку собираемся?

Сводка:
Антон спрашивает о встрече, Ирина отказывается, Алексей соглашается, Бот Агафон узнает идут ли они на мусорку.

Цель: получить короткий, понятный конспект последних сообщений, который можно передавать персонажу вместо полного чата.
"""

def _format_message_for_recap(message: Message) -> str:
    if message.reply:
        return f'{message.nickname} (в ответ на "{message.reply.text}"): {message.text}'
    return f'{message.nickname}: {message.text}'


async def generate_and_save_recap(chat_id: int, model_code: str = None):
    logger.info(f"Generating recap for chat {chat_id}")

    previous_recap = await get_last_recap(chat_id) or "Нет предыдущей сводки."

    last_recap_timestamp = await get_last_recap_timestamp(chat_id)
    if last_recap_timestamp:
        messages_count = await get_messages_count_since(chat_id, last_recap_timestamp)
        # We want to include at least 50 messages, but maybe more if they were missed
        size = max(settings.MESSAGES_RECAP_SIZE, messages_count)
    else:
        size = settings.MESSAGES_RECAP_SIZE

    last_messages = await get_history(chat_id, size=size)

    formatted_messages = "\n".join([_format_message_for_recap(m) for m in last_messages])
    data_prompt = (
        "Предыдущая сводка:\n"
        f"{previous_recap})\n"
        "Новые сообщения:\n"
        f"{formatted_messages}"
    )

    llm = ai.get_model(model_code)

    messages = [
        SystemMessage(content=RECAP_PROMPT),
        HumanMessage(content=data_prompt)
    ]

    try:
        response = await llm.ainvoke(messages)
        recap_text = response.content
        await save_recap(chat_id, recap_text)
        logger.info(f"Recap saved for chat {chat_id}")
        return recap_text
    except Exception as e:
        logger.error(f"Error generating recap for chat {chat_id}: {e}", exc_info=True)
        return None
