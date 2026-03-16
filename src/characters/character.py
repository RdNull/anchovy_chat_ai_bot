import asyncio
from typing import Generator

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src import settings
from src.logs import logger
from src import ai
from src.models import Message, UserRole

BASIC_SETUP_PROMPT = f"""
Ты — развлекательный бот-персонаж в групповом чате Telegram.
Задача: написать одно короткое смешное сообщение.

Длина:
* 1–2 предложения
  или
* 1–5 слов

Ограничение: максимум ~20 слов.
**Крайне важно!** Ответ должен быть **только чистый текст**, никаких описаний действий

Формат ответа:
* только текст
* без никнеймов
* без скобок, кавычек, стрелок, меток и форматирования

История чата передаётся так:
Никнейм: текст
Никнейм (в ответ на "текст"): текст

Этот формат нужен только для понимания контекста.
Никогда не повторяй его в ответе.
Твои прошлые сообщения могут быть в истории, но они тоже только для контекста.
Отвечай коротко и в стиле персонажа.
Если нечего сказать — пошути.
"""


def _format_message_text(message: Message) -> str:
    if message.reply:
        return f'{message.nickname} (в ответ на "{message.reply.text}"): {message.text}'

    return f'{message.nickname}: {message.text}'


def _format_previous_messages(last_messages: list[Message]) -> Generator[
    HumanMessage | AIMessage, None, None]:
    for message in last_messages:
        if message.role == UserRole.USER:
            yield HumanMessage(_format_message_text(message))
        else:
            yield AIMessage(message.text)


class Character:
    last_messages_recap: str | None = None

    def __init__(
        self,
        code:str,
        display_name: str,
        name: str,
        description: str,
        style_prompt: str,
    ):
        self.code = code
        self.name = name
        self.display_name = display_name
        self.description = description
        self.style_prompt = style_prompt

    @property
    def system_message(self):
        setup_prompt = f"{BASIC_SETUP_PROMPT}\nОписание твоего персонажа:\n{self.style_prompt}"
        if self.last_messages_recap:
            return SystemMessage(
                f'{setup_prompt}\n'
                'Сводка последних сообщений:\n'
                f'{self.last_messages_recap}'
            )

        return SystemMessage(setup_prompt)

    async def respond(
        self, user_message: Message, last_messages: list[Message] = None, llm=None
    ) -> str:
        messages = [
            self.system_message,
            *_format_previous_messages(last_messages),
            HumanMessage(_format_message_text(user_message)),
        ]
        logger.debug(
            f"Invoking LLM for character {self.name} with {len(messages)} messages")
        try:
            if not llm:
                llm = ai.get_model()
            response = await asyncio.wait_for(
                llm.ainvoke(messages),
                timeout=settings.AI_TIMEOUT
            )
        except asyncio.TimeoutError:
            logger.error(
                f"LLM request timed out after {settings.AI_TIMEOUT}s for {self.name}")
            return "Чё-то я призадумался и забыл, че хотел сказать..."
        except Exception as e:
            logger.error(f"Error invoking LLM for {self.name}: {e}", exc_info=True)
            return "Голова чё-то разболелась, давай потом..."

        logger.info(f"LLM response from {self.name}: {response.content[:50]}...")
        return response.content
