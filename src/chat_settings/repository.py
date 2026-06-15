from src import mongo
from src.logs import logger


async def get_character_code(chat_id: int) -> str | None:
    doc = await mongo.chat_settings.find_one({'chat_id': chat_id})
    return doc.get('character_code') if doc else None


async def set_character_code(chat_id: int, character_code: str) -> None:
    logger.debug(f"Setting character {character_code} for chat {chat_id}")
    await mongo.chat_settings.update_one(
        {'chat_id': chat_id},
        {'$set': {'character_code': character_code}},
        upsert=True,
    )
