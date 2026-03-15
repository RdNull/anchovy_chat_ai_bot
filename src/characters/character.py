import asyncio
from typing import Generator

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src import settings
from src.logs import logger
from src import ai
from src.models import Message, UserRole

BASIC_SETUP_PROMPT = f"""
Ты развлекательный бот-персонаж в групповом чате Telegram.
Твоя задача — писать **одно короткое смешное сообщение** в чат (максимум ~20 слов).  
Выбирай один из вариантов длины ответа:
- 1 короткий абзац
- максимум 2 предложения
- сообщение из 1-5 слов

Пиши **только текст**, без никнеймов, скобок, стрелок, кавычек, меток или форматирования.  
Никогда не повторяй формат истории чата.

История чата передаётся так:
- обычные сообщения: Никнейм: текст
- ответ на сообщение: Никнейм (в ответ на "текст"): текст

Этот формат **только для анализа контекста**, не для повторения в ответе.
История чата содержит твои предыдущие ответы в том же формате что и для пользователей, но они предназначены только для контекста.
Никогда не повторяй их формат в новых сообщениях - **выводи только короткий текст без скобок и формата**, как обычное новое сообщение.

Отвечай в стиле других участников и персонажа.
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
        name: str,
        description: str,
        style_prompt: str,
    ):
        self.code = code
        self.name = name
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
        logger.debug(f"Invoking LLM for character {self.name} with {len(messages)} messages")
        try:
            if not llm:
                llm = ai.get_model()
            response = await asyncio.wait_for(
                llm.ainvoke(messages),
                timeout=settings.AI_TIMEOUT
            )
        except asyncio.TimeoutError:
            logger.error(f"LLM request timed out after {settings.AI_TIMEOUT}s for {self.name}")
            return "Чё-то я призадумался и забыл, че хотел сказать..."
        except Exception as e:
            logger.error(f"Error invoking LLM for {self.name}: {e}", exc_info=True)
            return "Голова чё-то разболелась, давай потом..."

        logger.info(f"LLM response from {self.name}: {response.content[:50]}...")
        return response.content
