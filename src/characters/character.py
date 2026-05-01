import asyncio
from typing import Generator

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolCall
from langsmith import traceable

from src import ai, settings
from src.logs import logger
from src.models import MemoryData, Message, RelatedMessagesData, UserRole
from src.prompt_manager import prompt_manager
from . import tools
from .rate_limit import ChatRateLimiter
from ..settings import CHAT_RATE_LIMIT
from ..tools import ToolContext, ToolRegistry


def _format_previous_messages(last_messages: list[Message]) -> Generator[
    HumanMessage | AIMessage, None, None]:
    for message in last_messages:
        if message.role == UserRole.USER:
            yield HumanMessage(message.ai_format)
        else:
            yield AIMessage(message.text)


class Character:
    memory: MemoryData | None = None
    related_messages: list[RelatedMessagesData] | None = None

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
        self.rate_limiter = ChatRateLimiter(CHAT_RATE_LIMIT)

    @property
    def system_message(self):
        setup_prompt = prompt_manager.get_prompt(
            'character_setup',
            version='v3',
            character_description=self.style_prompt,
            memory=self.memory.content.model_dump_json(indent=1) if self.memory else None,
            related_messages=self.related_messages or None,
        )
        return SystemMessage(setup_prompt)

    @traceable
    async def respond(
        self,
        user_message: Message,
        last_messages: list[Message] = None,
    ) -> str:
        if self.rate_limiter.is_exceeded(user_message.chat_id):
            return 'Не гони, дай отдышаться...'

        llm = ai.get_model(version='v6')
        messages = [
            self.system_message,
            *_format_previous_messages(last_messages),
            HumanMessage(user_message.ai_format),
        ]
        tools_registry = ToolRegistry(
            (tools.search_messages, tools.get_user_facts,),
            context=ToolContext(chat_id=user_message.chat_id),
        )

        logger.debug(
            f"Invoking LLM for character {self.name} with {len(messages)} messages"
        )
        try:
            response = await asyncio.wait_for(
                self._run_llm_loop(llm, messages, tools_registry),
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

    async def _run_llm_loop(
        self,
        llm: BaseChatModel,
        messages: list[BaseMessage],
        tools_registry: ToolRegistry,
        _depth=1,
    ) -> AIMessage:
        if _depth > 5:
            logger.warning(f"LLM loop depth exceeded for {self.name}, returning response")
            return await llm.ainvoke(messages)

        llm_with_tools = llm.bind_tools(tools_registry.tools)
        response = await llm_with_tools.ainvoke(messages)
        if not response.tool_calls:
            return response

        messages.append(response)
        for tool_call in response.tool_calls:  # type: ToolCall
            tool_result = await tools_registry.execute(tool_call)
            messages.append(tool_result)

        return await self._run_llm_loop(llm, messages, tools_registry, _depth + 1)
