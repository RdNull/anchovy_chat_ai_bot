from src.logs import logger
from src.messages.repository import get_active_chats
from src.processors.context.handlers import update_chat_context


async def update_all_chats_context():
    logger.info("Running scheduled task for context updates")
    chat_ids = await get_active_chats()
    for chat_id in chat_ids:
        try:
            await update_chat_context(chat_id)
        except Exception as e:
            logger.error(f"Failed to update context for {chat_id}: {e}")
