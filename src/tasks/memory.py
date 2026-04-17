from src.logs import logger
from src.messages.repository import get_active_chats
from src.processors.context.memory import update_chat_memory


async def update_all_chats_memory():
    logger.info("Running scheduled task for memory updates")
    chat_ids = await get_active_chats()
    for chat_id in chat_ids:
        try:
            await update_chat_memory(chat_id)
        except Exception as e:
            logger.error(f"Failed to update memory for {chat_id}: {e}")
