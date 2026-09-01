from langchain_core.tools import tool
from telegram.error import BadRequest

from src.const import ALLOWED_REACTIONS
from src.embeddings.stickers import stickers_embedding_client
from src.logs import logger
from src.messages.media.repository import get_sendable_file_id
from src.tools import ToolContext, ToolFailure
from src.types import ReactionEmoji

ANSWER_TEXT_DESCRIPTION = '''
[answer]: Ответить текстом (включая эмодзи)
'''


@tool(description=ANSWER_TEXT_DESCRIPTION, return_direct=True)
async def answer_text(text: str) -> ToolFailure | None:
    if not text:
        logger.warning('Empty text provided, skipping answer')
        return ToolFailure('пустой ответ')

    tool_context: ToolContext = answer_text.metadata['context']
    await tool_context.replier.reply_message(text)

SET_REACTION_DESCRIPTION = '''
[answer] Поставить реакцию на сообщение
emoji: ровно один из эмодзи из разрешенного списка
'''


@tool(description=SET_REACTION_DESCRIPTION, return_direct=True)
async def set_reaction(emoji: ReactionEmoji) -> None:
    if emoji not in ALLOWED_REACTIONS:
        raise ValueError(f'Invalid reaction: {emoji}')  # maybe log only?

    tool_context: ToolContext = set_reaction.metadata['context']
    await tool_context.replier.reply_reaction(emoji, is_big=True)

SEND_STICKER_DESCRIPTION = '''
[answer]: Ответить стикером
sticker_id: ровно один id из результатов find_stickers
'''


@tool(description=SEND_STICKER_DESCRIPTION, return_direct=True)
async def send_sticker(sticker_id: str) -> ToolFailure | None:
    tool_context: ToolContext = send_sticker.metadata['context']

    file_id = await get_sendable_file_id(sticker_id)
    if not file_id:
        logger.warning(f'[TOOL] send_sticker: no sendable file_id for {sticker_id}')
        await stickers_embedding_client.drop_sticker(sticker_id)
        return ToolFailure('стикер недоступен')

    try:
        await tool_context.replier.reply_sticker(file_id, sticker_id)
    except BadRequest as e:
        # Only BadRequest: a network blip must surface as an error rather than quietly
        # evicting a sticker that is still perfectly good.
        logger.warning(f'[TOOL] send_sticker failed for {sticker_id}: {e}')
        await stickers_embedding_client.drop_sticker(sticker_id)
        return ToolFailure('стикер недоступен')
