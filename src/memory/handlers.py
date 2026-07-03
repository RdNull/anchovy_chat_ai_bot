from datetime import datetime, timedelta, timezone

from src import mongo
from src.logs import logger


async def delete_old_memories(retention_days: int) -> None:
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(days=retention_days)).timestamp()

    cursor = await mongo.memory.aggregate([
        {'$sort': {'created_at': -1}},
        {'$group': {'_id': '$chat_id', 'latest_id': {'$first': '$_id'}}},
    ])
    latest_ids = {doc['latest_id'] async for doc in cursor}

    result = await mongo.memory.delete_many({
        'created_at': {'$lt': cutoff_ts},
        '_id': {'$nin': list(latest_ids)},
    })
    logger.info(f'Deleted {result.deleted_count} stale memory records older than {retention_days} days')
