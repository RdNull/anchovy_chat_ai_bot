from dataclasses import dataclass
from typing import Iterable

from langchain_core.messages import ToolCall, ToolMessage
from langchain_core.tools import BaseTool

from src.characters.reply import Replier
from src.logs import logger


@dataclass
class ToolContext:
    chat_id: int
    replier: Replier


@dataclass
class ToolFailure:
    """A direct tool that could not deliver, so the turn must not end here.

    `is_return_direct` is a property of the tool, not of what happened when it ran.
    Without this the loop would treat a failed send as a delivered answer and the bot
    would say nothing at all.
    """
    message: str

class ToolRegistry:
    def __init__(
        self,
        context_tools: Iterable[BaseTool],
        direct_tools: Iterable[BaseTool],
        context: ToolContext
    ):
        self.context_tools = tuple(context_tools)
        self.direct_tools = tuple(direct_tools)
        self.tools = (*self.context_tools, *self.direct_tools)
        self.context = context
        self._tool_by_name = {tool.name: tool for tool in self.tools}

    async def execute(self, tool_call: ToolCall) -> tuple[ToolMessage, object]:
        """Runs a tool and returns its `ToolMessage` beside the raw result.

        The caller needs the raw value because a `ToolFailure` is indistinguishable
        from success once it has been stringified into a `ToolMessage`.
        """
        tool = self._get_tool(tool_call)
        logger.info(f'Executing tool: {tool_call['name']} with arguments: {tool_call['args']}')

        tool.metadata = {'context': self.context}
        tool_result = await tool.ainvoke(tool_call['args'])

        return ToolMessage(
            tool_call_id=tool_call['id'],
            content=str(tool_result)
        ), tool_result

    def is_return_direct(self, tool_call: ToolCall) -> bool:
        tool = self._get_tool(tool_call)
        return tool.return_direct

    def _get_tool(self, tool_call: ToolCall) -> BaseTool:
        tool: BaseTool | None = self._tool_by_name.get(tool_call['name'])
        if not tool:
            raise ValueError(f'Unknown tool: {tool_call['name']}')

        return tool
