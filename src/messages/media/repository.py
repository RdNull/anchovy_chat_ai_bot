from bson import ObjectId

from src.models import MediaDescription, MessageMediaStatus, MessageMediaTypes, UserRole
from src.mongo import media_descriptions, messages


async def create_media_description(
    media_id: str,
    content_hash: str | None = None,
    description: str | None = None,
    ocr_text: str | None = None,
    type: MessageMediaTypes = MessageMediaTypes.IMAGE,
    status: MessageMediaStatus = MessageMediaStatus.PENDING,
    sticker_emoji: str | None = None,
):
    result = await media_descriptions.insert_one({
        'hash': content_hash or None,
        'description': description or None,
        'ocr_text': ocr_text or None,
        'media_id': media_id,
        'type': type.value,
        'status': status.value,
        'sticker_emoji': sticker_emoji,
    })
    return await get_media_description(result.inserted_id)


async def update_media_description(
    description_id: str,
    content_hash: str | None = None,
    description: str | None = None,
    ocr_text: str | None = None,
    status: MessageMediaStatus = MessageMediaStatus.PROCESSING,
):
    update = {}
    if content_hash:
        update['hash'] = content_hash
    if description:
        update['description'] = description
    if ocr_text:
        update['ocr_text'] = ocr_text
    if status:
        update['status'] = status.value

    if update:
        await media_descriptions.update_one({'_id': ObjectId(description_id)}, {'$set': update})

    return await get_media_description(description_id)


async def mark_as_sticker(
    description_id: str, sticker_emoji: str | None,
) -> MediaDescription | None:
    """Retypes an existing row as a sticker.

    Rows written before the sticker unit are typed `image` or `gif`, because only
    Telegram's metadata can tell them apart and nothing was reading it. Backfilled on
    re-sighting rather than by a migration — see `pipeline.py:_backfill_sticker`.
    """
    await media_descriptions.update_one(
        {'_id': ObjectId(description_id)},
        {'$set': {
            'type': MessageMediaTypes.STICKER.value,
            'sticker_emoji': sticker_emoji,
        }},
    )
    return await get_media_description(description_id)


async def get_media_description(description_id: str) -> MediaDescription | None:
    result = await media_descriptions.find_one({'_id': ObjectId(description_id)})
    return _parse_media_description(result) if result else None


async def get_media_description_by_media_id(media_id: str) -> MediaDescription | None:
    result = await media_descriptions.find_one({'media_id': media_id})
    return _parse_media_description(result) if result else None


async def get_media_descriptions_by_hash(content_hash: str) -> MediaDescription | None:
    result = await media_descriptions.find_one({'hash': content_hash})
    return _parse_media_description(result) if result else None


async def update_media_description_status(description_id: str, status: MessageMediaStatus):
    await media_descriptions.update_one(
        {'_id': description_id},
        {'$set': {'status': status.value}}
    )


async def get_sendable_file_id(unique_id: str) -> str | None:
    """Resolves a sticker's identity to something Telegram will accept in a send.

    `media_descriptions.media_id` holds a `file_unique_id` despite the name — stable,
    but the API forbids sending with it. Only `messages.media_id` is a `file_id`.
    Reads the newest carrier so a re-issued `file_id` wins over a stale one.
    """
    doc = await messages.find_one(
        {'media_unique_id': unique_id}, sort=[('created_at', -1)],
    )
    return doc.get('media_id') if doc else None


async def get_recent_sticker_ids(chat_id: int, limit: int) -> set[str]:
    """The stickers this chat has just seen the bot send, to keep it from repeating."""
    cursor = messages.find(
        {'chat_id': chat_id, 'role': UserRole.AI.value, 'media_unique_id': {'$ne': None}},
        sort=[('created_at', -1)],
    ).limit(limit)
    return {doc['media_unique_id'] async for doc in cursor}


async def sticker_corpus_size() -> int:
    """How many stickers are actually searchable — typed, described and indexed.

    Read once at boot for the corpus log; nothing on the reply path calls it.
    """
    return await media_descriptions.count_documents(
        {'type': MessageMediaTypes.STICKER.value, 'status': MessageMediaStatus.READY.value},
    )


def _parse_media_description(data: dict) -> MediaDescription:
    # `sticker_emoji` uses `.get`, unlike its siblings: every row written before the
    # sticker unit lacks the key and must parse as None rather than raise.
    return MediaDescription(
        _id=str(data['_id']),
        description=data['description'] or '',
        ocr_text=data['ocr_text'],
        type=data['type'],
        status=data['status'],
        media_id=data['media_id'],
        sticker_emoji=data.get('sticker_emoji'),
    )
