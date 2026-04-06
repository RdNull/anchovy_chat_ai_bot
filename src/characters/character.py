import asyncio
from typing import Generator

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src import settings
from src.logs import logger
from src import ai
from src.models import MemoryData, Message, UserRole
from src.prompt_manager import prompt_manager




def _format_previous_messages(last_messages: list[Message]) -> Generator[
    HumanMessage | AIMessage, None, None]:
    for message in last_messages:
        if message.role == UserRole.USER:
            yield HumanMessage(message.ai_format())
        else:
            yield AIMessage(message.text)


class Character:
    memory: MemoryData | None = None

    def __init__(
        self,
        code: str,
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
        setup_prompt = prompt_manager.get_prompt(
            'character_setup',
            character_description=self.style_prompt,
            memory=self.memory.content.model_dump_json(indent=2) if self.memory else None
        )
        return SystemMessage(setup_prompt)

    async def respond(
        self, user_message: Message, last_messages: list[Message] = None, llm=None
    ) -> str:
        messages = [
            self.system_message,
            *_format_previous_messages(last_messages),
            HumanMessage(user_message.ai_format()),
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
