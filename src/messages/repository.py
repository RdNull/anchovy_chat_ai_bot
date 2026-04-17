from datetime import datetime, timezone
from typing import Iterable

from bson import ObjectId

from src import mongo
from src.logs import logger
from src.messages.media import get_media_description_by_media_id
from src.models import Message, MessageMedia, MessageReply, UpdateMessage, UserRole


async def save_message(message: Message):
    chat_id = message.chat_id
    message_text = message.text[:50] if message.text else '<media>'
    logger.debug(f"Pushing history for chat {chat_id}: {message.nickname}: {message_text}...")
    data = {
        'chat_id': chat_id,
        'telegram_id': message.telegram_id,
        'role': message.role.value,
        'text': message.text,
        'nickname': message.nickname,
        'media_id': message.media.media_id if message.media else None,
        'media_unique_id': message.media.unique_id if message.media else None,
        'created_at': datetime.now(timezone.utc).timestamp()
    }
    if message.reply:
        data['reply_telegram_id'] = message.reply.telegram_id
        data['reply_text'] = message.reply.text
        data['reply_nickname'] = message.reply.nickname
        data['reply_media_id'] = message.reply.media.media_id if message.reply.media else None
        reply_media_unique_id = message.reply.media.unique_id if message.reply.media else None
        data['reply_media_unique_id'] = reply_media_unique_id

    result = await mongo.messages.insert_one(data)
    message.id = result.inserted_id


async def update_message(update_message_data: UpdateMessage):
    logger.info(f"Updating message {update_message_data.id}: {update_message_data.text}")
    update_payload = update_message_data.model_dump(exclude={'id'}, exclude_unset=True)
    if not update_payload:
        return

    await mongo.messages.update_one(
        {'_id': ObjectId(update_message_data.id)},
        {'$set': update_payload}
    )

async def get_messages(
    chat_id: int, size: int = 50, from_date: datetime | None = None, sort_order: int = -1,
) -> list[Message]:
    logger.debug(f"Fetching history for chat {chat_id} ({size=} {from_date=})")
    search_query = {'chat_id': chat_id}
    if from_date:
        search_query['created_at'] = {'$gt': from_date.timestamp()}

    cursor = mongo.messages.find(search_query).sort('created_at', sort_order).limit(size)
    messages = await cursor.to_list(length=size)
    return [
        await _parse_message_record(message)
        for message in reversed(messages)
    ]


async def get_messages_by_ids(
    ids: Iterable[str], size: int = 100, sort_order: int = -1,
) -> list[Message]:
    logger.debug(f"Fetching messages: {ids} ({size=} {ids=})")
    search_query = {'_id': {'$in': [ObjectId(id_str) for id_str in ids]}}

    cursor = mongo.messages.find(search_query).sort('created_at', sort_order).limit(size)
    messages = await cursor.to_list(length=size)
    return [
        await _parse_message_record(message)
        for message in messages
    ]


async def get_message_by_tg_id(chat_id: int, telegram_id: int) -> Message | None:
    logger.debug(f"Fetching message by telegram id {telegram_id}")
    message = await mongo.messages.find_one({
        'chat_id': chat_id,
        'telegram_id': telegram_id,
    })
    if not message:
        return None

    return await _parse_message_record(message)

async def get_last_message(chat_id: int, role: UserRole | None = None) -> Message | None:
    logger.debug(f"Fetching last message for chat {chat_id} (role={role})")
    query = {'chat_id': chat_id}
    if role:
        query['role'] = role.value

    message = await mongo.messages.find_one(query, sort=[('created_at', -1)])
    if not message:
        return None

    return await _parse_message_record(message)


async def get_messages_count_since(chat_id: int, timestamp: float) -> int:
    logger.debug(f"Counting messages for chat {chat_id} since {timestamp}")
    return await mongo.messages.count_documents({
        'chat_id': chat_id,
        'created_at': {'$gt': timestamp}
    })


async def get_messages_count(chat_id: int) -> int:
    logger.debug(f"Counting messages for chat {chat_id}")
    return await mongo.messages.count_documents({'chat_id': chat_id})


async def register_chat(chat_id: int):
    await mongo.chats.update_one(
        {'chat_id': chat_id},
        {'$set': {'last_active': datetime.now(timezone.utc).timestamp()}},
        upsert=True
    )


async def get_active_chats() -> list[int]:
    cursor = mongo.chats.find({})
    chats = await cursor.to_list(length=1000)
    return [c['chat_id'] for c in chats]


async def get_message_media_data(media_id: str, media_unique_id: str):
    media = MessageMedia(
        media_id=media_id,
        unique_id=media_unique_id,
    )

    if media_description := await get_media_description_by_media_id(media_unique_id):
        media.description = media_description.description
        media.ocr_text = media_description.ocr_text
        media.status = media_description.status
        media.type = media_description.type

    return media


async def _parse_message_record(data: dict) -> Message:
    reply, media = None, None
    if reply_text := data.get('reply_text'):
        reply_media = None
        if reply_media_id := data.get('reply_media_id'):
            reply_media = await get_message_media_data(
                reply_media_id, data.get('reply_media_unique_id')
            )

        reply = MessageReply(
            telegram_id=data.get('reply_telegram_id'),
            text=reply_text,
            nickname=data['reply_nickname'],
            media=reply_media,
        )

    if media_id := data.get('media_id'):
        media = await get_message_media_data(media_id, data.get('media_unique_id'))

    return Message(
        _id=str(data['_id']),
        telegram_id=data.get('telegram_id'),
        chat_id=data['chat_id'],
        role=UserRole(data['role']),
        text=data['text'],
        reply=reply,
        media=media,
        nickname=data.get('nickname', 'unknown'),
        created_at=datetime.fromtimestamp(data['created_at'], tz=timezone.utc)
    )
