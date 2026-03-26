from datetime import datetime, timezone

from src import db
from src.logs import logger
from src.messages.media import get_media_description_by_media_id
from src.models import (
    Message, MessageMedia, MessageMediaTypes, MessageReply, RecapData,
    RecapType, UserRole,
)


async def push_history(chat_id: int, message: Message):
    message_text = message.text[:50] if message.text else '<media>'
    logger.debug(f"Pushing history for chat {chat_id}: {message.nickname}: {message_text}...")
    data = {
        'chat_id': chat_id,
        'role': message.role.value,
        'text': message.text,
        'nickname': message.nickname,
        'media_id': message.media.media_id,
        'created_at': datetime.now(timezone.utc).timestamp()
    }
    if message.reply:
        data['reply_text'] = message.reply.text
        data['reply_nickname'] = message.reply.nickname
        data['reply_media_id'] = message.reply.media.media_id

    result = await db.messages.insert_one(data)
    message.id = result.inserted_id


async def get_history(
    chat_id: int, size: int = 50, from_date: datetime | None = None
) -> list[Message]:
    logger.debug(f"Fetching history for chat {chat_id} ({size=} {from_date=})")
    search_query = {'chat_id': chat_id}
    if from_date:
        search_query['created_at'] = {'$gte': from_date.timestamp()}

    cursor = db.messages.find(search_query).sort('created_at', -1).limit(size)
    messages = await cursor.to_list(length=size)
    return [
        await _parse_message_record(message)
        for message in reversed(messages)
    ]


async def get_last_message(chat_id: int, role: UserRole | None = None) -> Message | None:
    logger.debug(f"Fetching last message for chat {chat_id} (role={role})")
    query = {'chat_id': chat_id}
    if role:
        query['role'] = role.value

    message = await db.messages.find_one(query, sort=[('created_at', -1)])
    if not message:
        return None

    return await _parse_message_record(message)


async def get_last_recap_timestamp(
    chat_id: int, recap_type: RecapType = RecapType.PERIODIC
) -> float | None:
    logger.debug(f"Fetching last {recap_type.value} recap timestamp for chat {chat_id}")
    recap = await db.recaps.find_one(
        {'chat_id': chat_id, 'type': recap_type.value}, sort=[('created_at', -1)]
    )
    if not recap:
        return None
    return recap['created_at']


async def get_messages_count_since(chat_id: int, timestamp: float) -> int:
    logger.debug(f"Counting messages for chat {chat_id} since {timestamp}")
    return await db.messages.count_documents({
        'chat_id': chat_id,
        'created_at': {'$gt': timestamp}
    })


async def get_messages_count(chat_id: int) -> int:
    logger.debug(f"Counting messages for chat {chat_id}")
    return await db.messages.count_documents({'chat_id': chat_id})


async def get_last_recap(
    chat_id: int, recap_type: RecapType = RecapType.PERIODIC
) -> RecapData | None:
    logger.debug(f"Fetching last {recap_type.value} recap for chat {chat_id}")
    recap = await db.recaps.find_one(
        {'chat_id': chat_id, 'type': recap_type.value}, sort=[('created_at', -1)]
    )
    return RecapData(**recap) if recap else None


async def get_last_recaps(
    chat_id: int, recap_type: RecapType = RecapType.PERIODIC, from_date: datetime | None = None,
    size: int = 20
) -> list[RecapData]:
    logger.debug(f"Fetching last {recap_type.value} {size} recaps for chat {chat_id}")
    search_params = {
        'chat_id': chat_id,
        'type': recap_type.value,
    }
    if from_date:
        search_params['created_at'] = {'$gte': from_date.timestamp()}

    recaps = await db.recaps.find(search_params).sort([('created_at', -1)]).to_list(length=size)
    return [RecapData(**r) for r in recaps]


async def register_chat(chat_id: int):
    await db.chats.update_one(
        {'chat_id': chat_id},
        {'$set': {'last_active': datetime.now(timezone.utc).timestamp()}},
        upsert=True
    )


async def get_active_chats() -> list[int]:
    cursor = db.chats.find({})
    chats = await cursor.to_list(length=1000)
    return [c['chat_id'] for c in chats]


async def save_recap(
    chat_id: int, text: str, recap_type: RecapType = RecapType.PERIODIC,
    created_at: datetime | None = None
):
    logger.debug(f"Saving {recap_type.value} recap for chat {chat_id}")
    created_at = created_at.timestamp() if created_at else datetime.now(timezone.utc).timestamp()
    data = {
        'chat_id': chat_id,
        'text': text,
        'type': recap_type.value,
        'created_at': created_at
    }
    await db.recaps.insert_one(data)


async def get_message_media_data(media_id: str):
    media = MessageMedia(
        media_id=media_id,
    )
    if media_description := await get_media_description_by_media_id(media_id):
        media.description = media_description.description
        media.ocr_text = media_description.ocr_text
        media.status = media_description.status

    return media


async def _parse_message_record(data: dict) -> Message:
    reply, media = None, None
    if 'reply_text' in data:
        reply_media = None
        if 'reply_media_id' in data:
            reply_media = await get_message_media_data(data['reply_media_id'])

        reply = MessageReply(
            text=data['reply_text'],
            nickname=data['reply_nickname'],
            media=reply_media,
        )

    if 'media_id' in data:
        media = await get_message_media_data(data['media_id'])

    return Message(
        _id=str(data['_id']),
        role=UserRole(data['role']),
        text=data['text'],
        reply=reply,
        media=media,
        nickname=data.get('nickname', 'unknown'),
        created_at=datetime.fromtimestamp(data['created_at'], tz=timezone.utc)
    )
