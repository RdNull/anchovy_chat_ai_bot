import asyncio
from datetime import datetime, UTC
from collections.abc import Iterable

from bson import ObjectId

from src import mongo, settings
from src.logs import event, logger
from src.media import get_media_description_by_media_id, wait_for_media_ready
from src.messages.models import Message, MessageMedia, MessageReply, UpdateMessage, UserRole


async def save_message(message: Message):
    chat_id = message.chat_id
    logger.debug(
        'Pushing history',
        extra=event('DB_MESSAGE_PUSH', nickname=message.nickname, text_len=len(message.text or '')),
    )
    data = {
        'chat_id': chat_id,
        'telegram_id': message.telegram_id,
        'role': message.role.value,
        'text': message.text,
        'nickname': message.nickname,
        'media_id': message.media.media_id if message.media else None,
        'media_unique_id': message.media.unique_id if message.media else None,
        'created_at': datetime.now(UTC).timestamp(),
    }
    if message.character_code:
        data['character_code'] = message.character_code
    if message.reply:
        reply_doc = await mongo.messages.find_one({
            'chat_id': chat_id,
            'telegram_id': message.reply.telegram_id,
        })
        if reply_doc:
            data['reply_id'] = reply_doc['_id']

    result = await mongo.messages.insert_one(data)
    message.id = result.inserted_id


async def update_message_reactions(
    message: Message,
    user_nickname: str,
    old_emojis: list[str],
    new_emojis: list[str],
):
    to_remove = set(old_emojis) - set(new_emojis)
    to_add = set(new_emojis) - set(old_emojis)

    update: dict = {}
    for emoji in to_remove:
        if message.reactions.get(emoji) == [user_nickname]:
            update.setdefault('$unset', {})[f'reactions.{emoji}'] = ''
        else:
            update.setdefault('$pull', {})[f'reactions.{emoji}'] = user_nickname

    for emoji in to_add:
        update.setdefault('$addToSet', {})[f'reactions.{emoji}'] = user_nickname

    if update:
        await mongo.messages.update_one({'_id': ObjectId(message.id)}, update)


async def add_bot_reaction(message: Message, bot_nickname: str, emoji: str):
    await mongo.messages.update_one(
        {'_id': ObjectId(message.id)},
        {'$addToSet': {f'reactions.{emoji}': bot_nickname}},
    )


async def update_message(update_message_data: UpdateMessage):
    logger.info(
        'Message edited',
        extra=event(
            'MESSAGE_EDITED',
            message_id=update_message_data.id,
            text_len=len(update_message_data.text),
        ),
    )
    update_payload = update_message_data.model_dump(exclude={'id'}, exclude_unset=True)
    if not update_payload:
        return

    await mongo.messages.update_one(
        {'_id': ObjectId(update_message_data.id)}, {'$set': update_payload}
    )


async def get_messages(
    chat_id: int,
    size: int = 50,
    from_date: datetime | None = None,
    sort_order: int = -1,
    to_date: datetime | None = None,
    role: UserRole | None = None,
    nickname: str | None = None,
    to_date_inclusive: bool = False,
) -> list[Message]:
    """Reads a chat's messages, always oldest first.

    Args:
        chat_id: Chat to read.
        size: Most messages to return.
        from_date: Keep only messages strictly newer than this.
        to_date: Keep only messages older than this — strictly, unless
            `to_date_inclusive`.
        role: Keep only messages from this role.
        nickname: Keep only messages by this exact nickname.
        sort_order: Which end of the matching range `size` takes — `-1` keeps the
            newest, `1` keeps the oldest. It does not affect the order of the
            returned list, which is chronological either way. Callers rely on
            that: `src/processors/context/embeddings.py` and
            `src/initiative/handlers.py` read their next watermark off
            `messages[-1]`.
        to_date_inclusive: When true, `to_date` also keeps a message exactly at that
            timestamp. Off by default because `src/blackbox/queries.py` relies on
            exclusivity to omit its anchor message; the initiative context fetch
            needs the watermark message itself, which is the newest message the
            previous run judged. Kept last and keyword-only in spirit (every call
            site already uses keywords) so inserting it never shifts an existing
            positional argument.

    Returns:
        The selected messages, oldest first.
    """
    logger.debug(
        'Fetching history',
        extra=event(
            'DB_MESSAGES_FETCH',
            size=size,
            from_date=from_date.isoformat() if from_date else None,
            to_date=to_date.isoformat() if to_date else None,
            sort_order=sort_order,
        ),
    )
    search_query = {'chat_id': chat_id}
    created_at = {}
    if from_date:
        created_at['$gt'] = from_date.timestamp()
    if to_date:
        created_at['$lte' if to_date_inclusive else '$lt'] = to_date.timestamp()
    if created_at:
        search_query['created_at'] = created_at
    if role:
        search_query['role'] = role.value
    if nickname:
        search_query['nickname'] = nickname

    cursor = mongo.messages.find(search_query).sort('created_at', sort_order).limit(size)
    records = await cursor.to_list(length=size)
    if sort_order == -1:
        records.reverse()

    return [await _parse_message_record(record) for record in records]


async def get_messages_by_ids(
    ids: Iterable[str],
    size: int = 100,
    sort_order: int = -1,
) -> list[Message]:
    ids = list(ids)
    logger.debug(
        'Fetching messages by id',
        extra=event('DB_MESSAGES_FETCH_BY_ID', count=len(ids), size=size),
    )
    search_query = {'_id': {'$in': [ObjectId(id_str) for id_str in ids]}}

    cursor = mongo.messages.find(search_query).sort('created_at', sort_order).limit(size)
    messages = await cursor.to_list(length=size)
    return [await _parse_message_record(message) for message in messages]


async def fetch_last_messages(chat_id: int, size: int, **kwargs) -> list[Message]:
    last_messages = await get_messages(chat_id, size=size, **kwargs)
    pending_media_ids = [
        m.media.unique_id for m in last_messages if m.media and m.media.status.is_pending
    ]
    if not pending_media_ids:
        return last_messages

    await wait_for_media_ready(
        pending_media_ids, timeout=settings.RESPOND_MEDIA_PROCESSING_POLLING_TIMEOUT
    )
    return await get_messages(chat_id, size=size, **kwargs)


async def get_message_by_tg_id(chat_id: int, telegram_id: int) -> Message | None:
    logger.debug(
        'Fetching message by telegram id',
        extra=event('DB_MESSAGE_FETCH_BY_TG_ID', telegram_id=telegram_id),
    )
    message = await mongo.messages.find_one({
        'chat_id': chat_id,
        'telegram_id': telegram_id,
    })
    if not message:
        return None

    return await _parse_message_record(message)


async def get_last_message(chat_id: int, role: UserRole | None = None) -> Message | None:
    logger.debug(
        'Fetching last message',
        extra=event('DB_LAST_MESSAGE_FETCH', role=role.value if role else None),
    )
    query = {'chat_id': chat_id}
    if role:
        query['role'] = role.value

    message = await mongo.messages.find_one(query, sort=[('created_at', -1)])
    if not message:
        return None

    return await _parse_message_record(message)


async def get_messages_count_since(
    chat_id: int,
    timestamp: float,
    role: UserRole | None = None,
) -> int:
    logger.debug('Counting messages', extra=event('DB_MESSAGES_COUNT', since=timestamp))
    query = {'chat_id': chat_id, 'created_at': {'$gt': timestamp}}
    if role:
        query['role'] = role.value
    return await mongo.messages.count_documents(query)


async def get_messages_count(chat_id: int) -> int:
    logger.debug('Counting messages', extra=event('DB_MESSAGES_COUNT'))
    return await mongo.messages.count_documents({'chat_id': chat_id})


async def get_message_media_data(media_id: str, media_unique_id: str) -> MessageMedia:
    """Builds a `MessageMedia` from the description row, the single source for it.

    Everything the row does not know stays at its default, including the sticker
    fields — a message read back out of Mongo has no Telegram object to consult.
    Live parses overlay their own answer afterwards (`parsing.py:_mark_sticker`).
    """
    media = MessageMedia(
        media_id=media_id,
        unique_id=media_unique_id,
    )

    if media_description := await get_media_description_by_media_id(media_unique_id):
        media.description = media_description.description
        media.ocr_text = media_description.ocr_text
        media.status = media_description.status
        media.type = media_description.type
        media.sticker_emoji = media_description.sticker_emoji

    return media


async def _parse_reply(data: dict) -> MessageReply | None:
    if reply_id := data.get('reply_id'):
        reply_doc = await mongo.messages.find_one({'_id': reply_id})
        if reply_doc:
            return MessageReply(
                telegram_id=reply_doc.get('telegram_id'),
                text=reply_doc.get('text'),
                nickname=reply_doc.get('nickname', 'unknown'),
                media=await _parse_media(reply_doc),
            )
        return None

    # legacy flat-field format
    if reply_text := data.get('reply_text'):
        return MessageReply(
            telegram_id=data.get('reply_telegram_id'),
            text=reply_text,
            nickname=data['reply_nickname'],
            media=await _parse_media(data, prefix='reply_'),
        )

    return None


async def _parse_media(data: dict, prefix: str = '') -> MessageMedia | None:
    if media_id := data.get(f'{prefix}media_id'):
        return await get_message_media_data(media_id, data.get(f'{prefix}media_unique_id'))
    return None


async def _parse_message_record(data: dict) -> Message:
    reply, media = await asyncio.gather(
        _parse_reply(data),
        _parse_media(data),
    )
    return Message(
        _id=str(data['_id']),
        telegram_id=data.get('telegram_id'),
        chat_id=data['chat_id'],
        role=UserRole(data['role']),
        text=data['text'],
        nickname=data.get('nickname', 'unknown'),
        reply=reply,
        media=media,
        created_at=datetime.fromtimestamp(data['created_at'], tz=UTC),
        reactions=data.get('reactions', {}),
        character_code=data.get('character_code'),
    )
