import asyncio
import random
from typing import Generator, Sequence

import langsmith
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolCall
from langsmith import traceable

from src import ai, settings
from src.logs import logger
from src.memory.models import MemoryData
from src.models import Message, RelatedMessagesData, UserRole
from src.prompt_manager import prompt_manager
from . import tools
from .rate_limit import SlidingWindowRateLimiter
from .reply import Replier
from ..settings import CHAT_RATE_LIMIT
from ..tools import ToolContext, ToolFailure, ToolRegistry

# The `_depth > 5` branch below terminates only because a direct tool always returned.
# Now that one can fail and hand the model another turn, a send that fails every time
# would recurse forever without an absolute stop.
_MAX_LOOP_DEPTH = 8


def _format_previous_messages(
    replier: Replier, last_messages: list[Message]
) -> Generator[HumanMessage | AIMessage, None, None]:
    for idx, message in enumerate(last_messages, start=1):
        prefix = '[TARGET] ' if message.id == replier.target_message.id else ''
        if message.role == UserRole.USER:
            yield HumanMessage(f'{prefix}{message.ai_format}')
        else:
            yield AIMessage(f'{prefix}{message.response_format}')



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
        self.rate_limiter = SlidingWindowRateLimiter(CHAT_RATE_LIMIT)

    @property
    def system_message(self):
        setup_prompt = prompt_manager.get_prompt(
            'character_setup',
            version='v8',
            character_description=self.style_prompt,
            memory=self.memory.prompt_format() if self.memory else None,
            related_messages=self.related_messages or None,
        )
        return SystemMessage(setup_prompt)

    @traceable
    async def respond(
        self,
        replier: Replier,
        last_messages: list[Message] = None,
    ) -> None:
        chat_id = replier.chat_id
        if self.rate_limiter.is_exceeded(chat_id):
            return None

        llm = self._get_llm(versions=('v8',))
        messages = [
            self.system_message,
            *_format_previous_messages(replier, last_messages or []),
        ]

        context_tools = [tools.search_messages, tools.get_user_facts, tools.search_web]
        direct_tools = [tools.answer_text, tools.set_reaction]

        if settings.ENABLE_STICKER_REPLIES:
            # Both or neither: `send_sticker` without `find_stickers` gives the model an id
            # parameter it can only hallucinate.
            context_tools.append(tools.find_stickers)
            direct_tools.append(tools.send_sticker)

        tools_registry = ToolRegistry(
            context_tools=context_tools,
            direct_tools=direct_tools,
            context=ToolContext(chat_id=chat_id, replier=replier),
        )

        logger.debug(
            f'Invoking LLM for character {self.name} with {len(messages)} messages'
        )
        try:
            await asyncio.wait_for(
                self._run_llm_loop(llm, messages, tools_registry),
                timeout=settings.AI_TIMEOUT
            )
        except asyncio.TimeoutError:
            logger.error(
                f'LLM request timed out after {settings.AI_TIMEOUT}s for {self.name}'
            )
            await replier.reply_message('Чё-то я призадумался и забыл, че хотел сказать...')
        except Exception as e:
            logger.error(f'Error invoking LLM for {self.name}: {e}', exc_info=True)
            await replier.reply_message('Голова чё-то разболелась, давай потом...')

    @classmethod
    def _get_llm(cls, versions: Sequence[str]) -> BaseChatModel:
        version = random.choice(versions)  # an A/B test
        rt = langsmith.get_current_run_tree()
        rt.tags.append(version)
        return ai.get_model(version=version)

    async def _run_llm_loop(
        self,
        llm: BaseChatModel,
        messages: list[BaseMessage],
        tools_registry: ToolRegistry,
        _depth=1,
    ):
        if _depth > _MAX_LOOP_DEPTH:
            logger.error(f'LLM loop hard depth cap hit for {self.name}, giving up')
            return

        if _depth > 5:
            logger.warning(f'LLM loop depth exceeded for {self.name}, returning response')
            direct_response_llm = llm.bind_tools(
                tools_registry.direct_tools, tool_choice='any', parallel_tool_calls=False
            )
            response = await direct_response_llm.ainvoke(messages)
        else:
            llm_with_tools = llm.bind_tools(tools_registry.tools, tool_choice='any')
            response = await llm_with_tools.ainvoke(messages)

        if not response.tool_calls:
            # shouldn't happen, but still
            logger.warning('Tool requirement was ignored')
            return

        messages.append(response)
        for tool_call in response.tool_calls:  # type: ToolCall
            tool_message, tool_result = await tools_registry.execute(tool_call)
            if tools_registry.is_return_direct(tool_call):
                if not isinstance(tool_result, ToolFailure):
                    if len(response.tool_calls) > 1:
                        logger.warning(f'Multiple tools called for direct response')
                        rt = langsmith.get_current_run_tree()
                        rt.tags.append('multiple_response_called')

                    return

                logger.warning(
                    f'Direct tool {tool_call["name"]} failed: {tool_result.message}'
                )

            messages.append(tool_message)

        await self._run_llm_loop(llm, messages, tools_registry, _depth + 1)
