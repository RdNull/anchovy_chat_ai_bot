from langchain_core.tools import tool

from src.const import ALLOWED_REACTIONS
from src.logs import logger
from src.tools import ToolContext
from src.types import ReactionEmoji

ANSWER_TEXT_DESCRIPTION = '''
Ответить текстом (включая эмодзи)
'''


@tool(description=ANSWER_TEXT_DESCRIPTION, return_direct=True)
async def answer_text(text: str) -> None:
    if not text:
        logger.info('Empty text provided, skipping answer')
        return

    tool_context: ToolContext = answer_text.metadata['context']
    await tool_context.replier.reply_message(text)

SET_REACTION_DESCRIPTION = '''
Поставить реакцию на сообщение
emoji: ровно один из эмодзи из разрешенного списка
'''


@tool(description=SET_REACTION_DESCRIPTION, return_direct=True)
async def set_reaction(emoji: ReactionEmoji) -> None:
    if emoji not in ALLOWED_REACTIONS:
        raise ValueError(f'Invalid reaction: {emoji}')  # maybe log only?

    tool_context: ToolContext = set_reaction.metadata['context']
    await tool_context.replier.reply_reaction(emoji, is_big=True)
