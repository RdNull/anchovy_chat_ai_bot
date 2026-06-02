from typing import Literal

from langchain_core.tools import tool

from src.const import ALLOWED_REACTIONS
from src.logs import logger

ReactionLiteral = Literal[tuple(ALLOWED_REACTIONS)]

ANSWER_TEXT_DESCRIPTION = '''
Ответить текстом (включая эмодзи)
'''


@tool(description=ANSWER_TEXT_DESCRIPTION, return_direct=True)
async def answer_text(text: str) -> None:
    if not text:
        logger.info('Empty text provided, skipping answer')
        return


SET_REACTION_DESCRIPTION = '''
Поставить реакцию на сообщение
emoji: ровно один из эмодзи из разрешенного списка
'''


@tool(description=SET_REACTION_DESCRIPTION)
async def set_reaction(emoji: ReactionLiteral) -> None:
    if emoji not in ALLOWED_REACTIONS:
        raise ValueError(f'Invalid reaction: {emoji}')  # maybe log only?
