from bson import ObjectId

from src.models import MediaDescription, MessageMediaStatus, MessageMediaTypes
from src.mongo import media_descriptions


async def create_media_description(
    media_id: str,
    content_hash: str | None = None,
    description: str | None = None,
    ocr_text: str | None = None,
    type: MessageMediaTypes = MessageMediaTypes.IMAGE,
    status: MessageMediaStatus = MessageMediaStatus.PENDING,
    is_sticker: bool = False,
    sticker_emoji: str | None = None,
    sticker_set: str | None = None,
):
    result = await media_descriptions.insert_one({
        'hash': content_hash or None,
        'description': description or None,
        'ocr_text': ocr_text or None,
        'media_id': media_id,
        'type': type.value,
        'status': status.value,
        'is_sticker': is_sticker,
        'sticker_emoji': sticker_emoji,
        'sticker_set': sticker_set,
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


def _parse_media_description(data: dict) -> MediaDescription:
    # The sticker fields use `.get`, unlike their siblings: every row written before
    # this unit has none of them, and they must parse as False/None rather than raise.
    return MediaDescription(
        _id=str(data['_id']),
        description=data['description'] or '',
        ocr_text=data['ocr_text'],
        type=data['type'],
        status=data['status'],
        media_id=data['media_id'],
        is_sticker=data.get('is_sticker', False),
        sticker_emoji=data.get('sticker_emoji'),
        sticker_set=data.get('sticker_set'),
    )
