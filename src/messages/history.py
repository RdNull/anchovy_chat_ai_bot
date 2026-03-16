from datetime import datetime, timezone

from src.logs import logger
from src import db
from src.models import Message, MessageReply, RecapData, UserRole


async def push_history(chat_id: int, message: Message):
    logger.debug(f"Pushing history for chat {chat_id}: {message.nickname}: {message.text[:50]}...")
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
    for m in reversed(messages):
        reply = None
        if 'reply_text' in m:
            reply = MessageReply(text=m['reply_text'], nickname=m['reply_nickname'])

        result.append(Message(
            role=UserRole(m['role']),
            text=m['text'],
            reply=reply,
            nickname=m.get('nickname', 'unknown'),
            created_at=datetime.fromtimestamp(m['created_at'], tz=timezone.utc)
        ))
    return result


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


async def get_last_recap_timestamp(chat_id: int) -> float | None:
    logger.debug(f"Fetching last recap timestamp for chat {chat_id}")
    recap = await db.recaps.find_one({'chat_id': chat_id}, sort=[('created_at', -1)])
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


async def get_last_recap(chat_id: int) -> RecapData | None:
    logger.debug(f"Fetching last recap for chat {chat_id}")
    recap = await db.recaps.find_one({'chat_id': chat_id}, sort=[('created_at', -1)])
    return RecapData(**recap) if recap else None


async def save_recap(chat_id: int, text: str):
    logger.debug(f"Saving recap for chat {chat_id}")
    data = {
        'chat_id': chat_id,
        'text': text,
        'created_at': datetime.now(timezone.utc).timestamp()
    }
    await db.recaps.insert_one(data)
