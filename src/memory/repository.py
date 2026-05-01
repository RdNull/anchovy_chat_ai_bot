from datetime import datetime, timezone

from src import mongo
from src.logs import logger
from .models import MemoryData, StructuredMemory


async def save_memory(chat_id: int, memory: StructuredMemory):
    logger.debug(f"Saving memory for chat {chat_id}")
    data = {
        'chat_id': chat_id,
        'content': memory.model_dump(),
        'created_at': datetime.now(timezone.utc).timestamp()
    }
    await mongo.memory.insert_one(data)


async def get_last_memory(chat_id: int) -> MemoryData | None:
    logger.debug(f"Fetching last memory for chat {chat_id}")
    memory = await mongo.memory.find_one(
        {'chat_id': chat_id}, sort=[('created_at', -1)]
    )
    if not memory:
        return None

    # MongoDB stores created_at as float (timestamp)
    # Pydantic MemoryData expects datetime for created_at
    memory['created_at'] = datetime.fromtimestamp(memory['created_at'], tz=timezone.utc)
    return MemoryData(**memory)
