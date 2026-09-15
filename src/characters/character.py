import asyncio
import random
import time
from dataclasses import dataclass
from typing import Generator, Sequence

import langsmith
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolCall
from langsmith import traceable

from src import ai, settings
from src.logs import elapsed_ms, event, logger
from src.memory.models import MemoryData
from src.model_manager import model_manager
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


@dataclass
class _LoopStats:
    """Accumulates across every recursive turn of `_run_llm_loop`, for the one `LLM_INVOKE`
    line logged when the whole loop finishes — model spend and latency are otherwise
    invisible outside LangSmith."""
    depth: int = 0
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0

    def record(self, response: AIMessage, depth: int) -> None:
        self.depth = depth
        self.tool_calls += len(response.tool_calls)
        usage = response.usage_metadata or {}
        self.tokens_in += usage.get('input_tokens') or 0
        self.tokens_out += usage.get('output_tokens') or 0


def _format_previous_messages(
    replier: Replier, last_messages: list[Message]
) -> Generator[HumanMessage | AIMessage, None, None]:
    # A `Message` built in memory rather than read from Mongo has `id=None`, and
    # comparing those as strings made every such message the target.
    target_id = replier.target_message.id if replier.target_message else None
    for message in last_messages:
        prefix = '[TARGET] ' if target_id and message.id == target_id else ''
        if message.role == UserRole.USER:
            yield HumanMessage(f'{prefix}{message.ai_format}')
        else:
            yield AIMessage(f'{prefix}{message.response_format}')


def _get_tools_registry(replier: Replier) -> ToolRegistry:
    context_tools = [tools.search_messages, tools.get_user_facts, tools.search_web]
    direct_tools = [tools.answer_text]

    if replier.target_message:
        # reactions only possible when replying to a message
        direct_tools.append(tools.set_reaction)

    if settings.ENABLE_STICKER_REPLIES:
        # Both or neither: `send_sticker` without `find_stickers` gives the model an id
        # parameter it can only hallucinate.
        context_tools.append(tools.find_stickers)
        direct_tools.append(tools.send_sticker)

    return ToolRegistry(
        context_tools=context_tools,
        direct_tools=direct_tools,
        context=ToolContext(chat_id=replier.chat_id, replier=replier),
    )


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
            version='v9',
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

        llm, version, model_name = self._get_llm(versions=('v8',))
        messages = [
            self.system_message,
            *_format_previous_messages(replier, last_messages or []),
        ]

        tools_registry = _get_tools_registry(replier)
        logger.debug(
            'Invoking LLM', extra=event('LLM_INVOKE_START', character=self.name, messages=len(messages)),
        )
        stats = _LoopStats()
        started = time.monotonic()
        try:
            await asyncio.wait_for(
                self._run_llm_loop(llm, messages, tools_registry, stats),
                timeout=settings.AI_TIMEOUT
            )
            logger.info(
                'LLM loop finished',
                extra=event(
                    'LLM_INVOKE', character=self.name, model=model_name, version=version,
                    elapsed_ms=elapsed_ms(started), depth=stats.depth,
                    tool_calls=stats.tool_calls, tokens_in=stats.tokens_in,
                    tokens_out=stats.tokens_out, outcome='ok',
                ),
            )
        except asyncio.TimeoutError:
            logger.error(
                'LLM request timed out',
                extra=event(
                    'LLM_INVOKE', outcome='timeout', timeout_s=settings.AI_TIMEOUT,
                    elapsed_ms=elapsed_ms(started),
                ),
            )
            await replier.reply_message('Чё-то я призадумался и забыл, че хотел сказать...')
        except Exception:
            logger.error(
                'Error invoking LLM', exc_info=True, extra=event('LLM_INVOKE', outcome='error'),
            )
            await replier.reply_message('Голова чё-то разболелась, давай потом...')

    @classmethod
    def _get_llm(cls, versions: Sequence[str]) -> tuple[BaseChatModel, str, str]:
        version = random.choice(versions)  # an A/B test
        rt = langsmith.get_current_run_tree()
        rt.tags.append(version)
        # Which arm ran is otherwise unrecoverable from the logs -- `LLM_INVOKE` stamps it.
        model_name = model_manager.get_model_settings('chat', version).get('model')
        return ai.get_model(version=version), version, model_name

    async def _run_llm_loop(
        self,
        llm: BaseChatModel,
        messages: list[BaseMessage],
        tools_registry: ToolRegistry,
        stats: _LoopStats,
        _depth=1,
    ):
        if _depth > _MAX_LOOP_DEPTH:
            logger.error(
                'LLM loop hard depth cap hit, giving up',
                extra=event('LLM_LOOP_ABORTED', reason='hard_depth_cap', depth=_depth),
            )
            return

        if _depth > 5:
            logger.warning(
                'LLM loop depth exceeded, returning response',
                extra=event('LLM_LOOP_DEPTH_EXCEEDED', depth=_depth),
            )
            direct_response_llm = llm.bind_tools(
                tools_registry.direct_tools, tool_choice='any', parallel_tool_calls=False
            )
            response = await direct_response_llm.ainvoke(messages)
        else:
            llm_with_tools = llm.bind_tools(tools_registry.tools, tool_choice='any')
            response = await llm_with_tools.ainvoke(messages)

        stats.record(response, _depth)

        if not response.tool_calls:
            # shouldn't happen, but still
            logger.warning(
                'Tool requirement was ignored', extra=event('LLM_TOOL_REQUIREMENT_IGNORED'),
            )
            return

        messages.append(response)
        for tool_call in response.tool_calls:  # type: ToolCall
            tool_message, tool_result = await tools_registry.execute(tool_call)
            if tools_registry.is_return_direct(tool_call):
                if not isinstance(tool_result, ToolFailure):
                    if len(response.tool_calls) > 1:
                        logger.warning(
                            'Multiple tools called for direct response',
                            extra=event('LLM_MULTIPLE_DIRECT_TOOLS', tool=tool_call['name']),
                        )
                        rt = langsmith.get_current_run_tree()
                        rt.tags.append('multiple_response_called')

                    return

                logger.warning(
                    'Direct tool failed',
                    extra=event(
                        'TOOL_DIRECT_FAILED', tool=tool_call['name'], error=tool_result.message,
                    ),
                )

            messages.append(tool_message)

        await self._run_llm_loop(llm, messages, tools_registry, stats, _depth + 1)
