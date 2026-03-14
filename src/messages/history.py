from datetime import datetime, timezone

from src.logs import logger
from src import db
from src.models import Message, MessageReply, UserRole


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


async def get_history(chat_id: int, size:int=50) -> list[Message]:
    logger.debug(f"Fetching history for chat {chat_id} (size={size})")
    cursor = db.messages.find({'chat_id': chat_id}).sort('created_at', -1).limit(size)
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
