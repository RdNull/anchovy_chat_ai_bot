from datetime import date, datetime, UTC

from bson import ObjectId

from src import mongo
from src.facts.models import FactKind, FactStatus, UserFact
from src.logs import event, logger


async def get_facts(
    nickname: str,
    status: FactStatus | None = None,
    limit: int | None = None,
) -> list[UserFact]:
    """A nickname's facts, confirmed first, then the most recently seen."""
    logger.debug('Fetching facts', extra=event('DB_FACTS_FETCH', nickname=nickname))
    query: dict = {'nickname': nickname}
    if status is not None:
        query['status'] = status.value

    # 'confirmed' sorts after 'candidate', so descending status puts confirmed first.
    cursor = mongo.facts.find(query).sort([('status', -1), ('last_seen_at', -1)])
    if limit is not None:
        cursor = cursor.limit(limit)
    docs = await cursor.to_list(length=limit)
    return [UserFact.model_validate(d) for d in docs]


async def get_fact_by_id(fact_id: str) -> UserFact | None:
    logger.debug('Fetching fact by id', extra=event('DB_FACT_FETCH', fact_id=fact_id))
    fact = await mongo.facts.find_one({'_id': ObjectId(fact_id)})
    return UserFact.model_validate(fact) if fact else None


async def create_fact(
    nickname: str,
    kind: FactKind,
    text: str,
    status: FactStatus,
    day: date,
) -> UserFact:
    logger.debug('Saving fact', extra=event('DB_FACT_WRITE', nickname=nickname))
    now = datetime.now(UTC)
    data = {
        'nickname': nickname,
        'kind': kind.value,
        'status': status.value,
        'text': text,
        'sightings': [day.isoformat()],
        'created_at': now,
        'last_seen_at': now,
    }
    result = await mongo.facts.insert_one(data)
    data['_id'] = result.inserted_id
    return UserFact.model_validate(data)


async def add_sighting(fact_id: str, day: date) -> UserFact | None:
    """Adds `day` to the sightings (a no-op if already there) and stamps `last_seen_at`."""
    update = {
        '$addToSet': {'sightings': day.isoformat()},
        '$set': {'last_seen_at': datetime.now(UTC)},
    }
    doc = await mongo.facts.find_one_and_update(
        {'_id': ObjectId(fact_id)}, update, return_document=True
    )
    return UserFact.model_validate(doc) if doc else None


async def set_status(fact_id: str, status: FactStatus) -> None:
    await mongo.facts.update_one({'_id': ObjectId(fact_id)}, {'$set': {'status': status.value}})


async def replace_fact(
    fact_id: str,
    kind: FactKind,
    text: str,
    status: FactStatus,
    day: date,
) -> UserFact | None:
    update = {
        'kind': kind.value,
        'text': text,
        'status': status.value,
        'sightings': [day.isoformat()],
        'last_seen_at': datetime.now(UTC),
    }
    doc = await mongo.facts.find_one_and_update(
        {'_id': ObjectId(fact_id)}, {'$set': update}, return_document=True
    )
    return UserFact.model_validate(doc) if doc else None


async def delete_fact(fact_id: str) -> None:
    await mongo.facts.delete_one({'_id': ObjectId(fact_id)})


async def list_overflow(nickname: str, status: FactStatus, keep: int) -> list[UserFact]:
    """The facts beyond the `keep` most recently seen, for one nickname and status."""
    query = {'nickname': nickname, 'status': status.value}
    cursor = mongo.facts.find(query).sort('last_seen_at', -1).skip(keep)
    docs = await cursor.to_list(length=None)
    return [UserFact.model_validate(d) for d in docs]


async def find_expired_candidates(cutoff: datetime) -> list[UserFact]:
    query = {'status': FactStatus.CANDIDATE.value, 'last_seen_at': {'$lt': cutoff}}
    docs = await mongo.facts.find(query).to_list(length=None)
    return [UserFact.model_validate(d) for d in docs]
