from datetime import datetime, timedelta, UTC

from bson import ObjectId

from src import mongo as db
from src.initiative.models import InitiativeRun
from src.logs import event, logger


async def get_last_initiative_run(chat_id: int) -> InitiativeRun | None:
    initiative_run = await db.initiative_runs.find_one(
        {'chat_id': chat_id}, sort=[('created_at', -1)]
    )
    if not initiative_run:
        return None

    return InitiativeRun(**initiative_run)


async def save_initiative_run(chat_id: int, last_message_time: datetime) -> str:
    logger.debug('Saving initiative run', extra=event('INITIATIVE_RUN_SAVED'))
    data = {
        'chat_id': chat_id,
        'last_message_time': last_message_time.timestamp(),
        'created_at': datetime.now(UTC).timestamp(),
    }
    result = await db.initiative_runs.insert_one(data)
    return str(result.inserted_id)


async def mark_initiative_replied(run_id: str) -> None:
    """Stamps the claimed run as sent — the record `count_replied_since` counts.

    Called from the send path only, before the reply is actually dispatched: it
    reserves the day's slot rather than confirming delivery, which is what keeps the
    daily-cap gate simple. `ObjectId` is mandatory here — a bare string id makes this
    an update against a document that never matches, the same silent-no-op failure
    `update_media_description_status` had (root CLAUDE.md, Media pipeline section).
    """
    await db.initiative_runs.update_one(
        {'_id': ObjectId(run_id)},
        {'$set': {'replied_at': datetime.now(UTC).timestamp()}},
    )


async def count_replied_since(chat_id: int, window: timedelta) -> int:
    since = datetime.now(UTC) - window
    return await db.initiative_runs.count_documents(
        {'chat_id': chat_id, 'replied_at': {'$gte': since.timestamp()}}
    )
