from src import mongo
from src.logs import event, logger


async def get_character_code(chat_id: int) -> str | None:
    doc = await mongo.chat_settings.find_one({'chat_id': chat_id})
    return doc.get('character_code') if doc else None


async def set_character_code(chat_id: int, character_code: str) -> None:
    logger.debug('Setting character', extra=event('DB_CHARACTER_SET', character=character_code))
    await mongo.chat_settings.update_one(
        {'chat_id': chat_id},
        {'$set': {'character_code': character_code}},
        upsert=True,
    )


async def get_allowed_characters(chat_id: int) -> list[str]:
    doc = await mongo.chat_settings.find_one({'chat_id': chat_id})
    return list(doc.get('allowed_characters') or []) if doc else []


async def toggle_allowed_character(chat_id: int, character_code: str) -> list[str]:
    """Adds the code to the chat's allow list, or removes it if already there.

    Returns the list as it stands afterwards.
    """
    allowed = await get_allowed_characters(chat_id)
    operator = '$pull' if character_code in allowed else '$addToSet'
    logger.info(
        'Character access toggled',
        extra=event('CHARACTER_ACCESS_TOGGLED', character=character_code, op=operator),
    )
    await mongo.chat_settings.update_one(
        {'chat_id': chat_id},
        {operator: {'allowed_characters': character_code}},
        upsert=True,
    )
    return await get_allowed_characters(chat_id)
