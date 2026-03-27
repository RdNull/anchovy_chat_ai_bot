from src.logs import logger
from src.messages.history import get_active_chats
from src.processors.recap import generate_and_save_recap
from src.models import RecapType


async def generate_all_chats_recap(recap_type: RecapType):
    logger.info(f"Running scheduled task for {recap_type.value} recaps")
    chat_ids = await get_active_chats()
    for chat_id in chat_ids:
        try:
            await generate_and_save_recap(chat_id, recap_type=recap_type)
        except Exception as e:
            logger.error(f"Failed to generate {recap_type.value} recap for {chat_id}: {e}")