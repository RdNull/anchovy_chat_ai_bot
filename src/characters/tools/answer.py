from langchain_core.tools import tool
from telegram.error import BadRequest

from src.characters.tools.registry import ToolContext, ToolFailure
from src.embeddings.stickers import stickers_embedding_client
from src.logs import event, logger
from src.media.repository import get_sendable_file_id
from src.types import ReactionEmoji

ANSWER_TEXT_DESCRIPTION = '''
[answer]: Ответить текстом (включая эмодзи)
'''


@tool(description=ANSWER_TEXT_DESCRIPTION, return_direct=True)
async def answer_text(text: str) -> ToolFailure | None:
    if not text:
        logger.warning('Empty text provided, skipping answer', extra=event('TOOL_ANSWER_EMPTY'))
        return ToolFailure('пустой ответ')

    tool_context: ToolContext = answer_text.metadata['context']
    await tool_context.replier.reply_message(text)

SET_REACTION_DESCRIPTION = '''
[answer] Поставить реакцию на сообщение
emoji: ровно один из эмодзи из разрешенного списка
'''


@tool(description=SET_REACTION_DESCRIPTION, return_direct=True)
async def set_reaction(emoji: ReactionEmoji) -> ToolFailure | None:
    # No `if emoji not in ALLOWED_REACTIONS` guard here: `emoji`'s `Literal` type
    # (src/types.py) makes pydantic validate tool-call args against the allowed set
    # before this body ever runs, so an off-enum value never reaches it. That failure
    # is caught in `ToolRegistry.execute` (src/characters/tools/registry.py) instead, as a
    # `ValidationError`.
    tool_context: ToolContext = set_reaction.metadata['context']

    try:
        sent = await tool_context.replier.reply_reaction(emoji, is_big=True)
    except BadRequest:
        # Only BadRequest: a network blip must surface as an error rather than quietly
        # swallowing a reaction that would otherwise have gone through.
        logger.warning(
            'set_reaction failed', exc_info=True,
            extra=event('TOOL_REACTION_SET', outcome='error', emoji=emoji),
        )
        return ToolFailure('не получилось поставить реакцию')

    if not sent:
        logger.warning(
            'set_reaction rejected',
            extra=event('TOOL_REACTION_SET', outcome='error', emoji=emoji),
        )
        return ToolFailure('не получилось поставить реакцию')

SEND_STICKER_DESCRIPTION = '''
[answer]: Ответить стикером
sticker_id: ровно один id из результатов find_stickers
'''


@tool(description=SEND_STICKER_DESCRIPTION, return_direct=True)
async def send_sticker(sticker_id: str) -> ToolFailure | None:
    tool_context: ToolContext = send_sticker.metadata['context']

    file_id = await get_sendable_file_id(sticker_id)
    if not file_id:
        logger.warning(
            'No sendable file_id for sticker',
            extra=event('TOOL_STICKER_SEND', outcome='not_found', sticker_id=sticker_id),
        )
        await stickers_embedding_client.drop_sticker(sticker_id)
        return ToolFailure('стикер недоступен')

    try:
        await tool_context.replier.reply_sticker(file_id, sticker_id)
    except BadRequest:
        # Only BadRequest: a network blip must surface as an error rather than quietly
        # evicting a sticker that is still perfectly good.
        logger.warning(
            'send_sticker failed', exc_info=True,
            extra=event('TOOL_STICKER_SEND', outcome='error', sticker_id=sticker_id),
        )
        await stickers_embedding_client.drop_sticker(sticker_id)
        return ToolFailure('стикер недоступен')
