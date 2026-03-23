from datetime import datetime, timezone

from src.logs import logger
from src import db
from src.models import Message, MessageMedia, MessageReply, RecapData, RecapType, UserRole


async def push_history(chat_id: int, message: Message):
    message_text = message.text[:50] if message.text else '<media>'
    logger.debug(f"Pushing history for chat {chat_id}: {message.nickname}: {message_text}...")
    data = {
        'chat_id': chat_id,
        'role': message.role.value,
        'text': message.text,
        'nickname': message.nickname,
        'created_at': datetime.now(timezone.utc).timestamp()
    }
    if message.reply:
        data['reply_text'] = message.reply.text
        data['reply_nickname'] = message.reply.nickname

    if message.media:
        data['media_type'] = message.media.type
        data['media_status'] = message.media.status
        data['media_id'] = message.media.media_id
        data['media_description'] = message.media.description
        data['media_ocr_text'] = message.media.ocr_text

    await db.messages.insert_one(data)


async def get_history(
    chat_id: int, size: int = 50, from_date: datetime | None = None
) -> list[Message]:
    logger.debug(f"Fetching history for chat {chat_id} ({size=} {from_date=})")
    search_query = {'chat_id': chat_id}
    if from_date:
        search_query['created_at'] = {'$gte': from_date.timestamp()}

    cursor = db.messages.find(search_query).sort('created_at', -1).limit(size)
    messages = await cursor.to_list(length=size)
    result = []
    for message in reversed(messages):
        reply, media = None, None
        if 'reply_text' in message:
            reply = MessageReply(text=message['reply_text'], nickname=message['reply_nickname'])

        if 'media_type' in message:
            media = MessageMedia(
                type=message['media_type'],
                status=message['media_status'],
                media_id=message['media_id'],
                description=message['media_description'],
                ocr_text=message['media_ocr_text']
            )

        result.append(
            Message(
                role=UserRole(message['role']),
                text=message['text'],
                reply=reply,
                media=media,
                nickname=message.get('nickname', 'unknown'),
                created_at=datetime.fromtimestamp(message['created_at'], tz=timezone.utc)
            )
        )
    return result


async def update_history_media(message_id: str, media: MessageMedia):
    await db.messages.update_one(
        {'_id': message_id},
        {'$set': {
            'media_status': media.status,
            'media_description': media.description,
            'media_ocr_text': media.ocr_text,
        }}
    )

async def get_last_message(chat_id: int, role: UserRole | None = None) -> Message | None:
    logger.debug(f"Fetching last message for chat {chat_id} (role={role})")
    query = {'chat_id': chat_id}
    if role:
        query['role'] = role.value

    message = await db.messages.find_one(query, sort=[('created_at', -1)])
    if not message:
        return None

    reply = None
    if 'reply_text' in message:
        reply = MessageReply(text=message['reply_text'], nickname=message['reply_nickname'])

    return Message(
        role=UserRole(message['role']),
        text=message['text'],
        reply=reply,
        nickname=message.get('nickname', 'unknown'),
        created_at=datetime.fromtimestamp(message['created_at'], tz=timezone.utc)
    )


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
