from typing import Iterable

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

from src.logs import logger


class ToolRegistry:
    def __init__(self, tools: Iterable[BaseTool]):
        self.tools = tuple(tools)
        self._tool_by_name = {tool.name: tool for tool in tools}

    async def execute(self, tool_call) -> ToolMessage:
        tool = self._tool_by_name.get(tool_call["name"])
        if not tool:
            raise ValueError(f"Unknown tool: {tool_call['name']}")

        logger.info(f"Executing tool: {tool_call['name']} with arguments: {tool_call['args']}")
        tool_result = await tool.ainvoke(tool_call["args"])

        tool_message = ToolMessage(
            tool_call_id=tool_call["id"],
            content=str(tool_result)
        )
        return tool_message
