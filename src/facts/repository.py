from datetime import datetime, timezone

from bson import ObjectId

from src import mongo
from src.logs import event, logger
from src.facts.models import UserFact


async def get_facts(nickname: str, limit: int = 5) -> list[UserFact]:
    logger.debug('Fetching facts', extra=event('DB_FACTS_FETCH', nickname=nickname))
    if 'facts' not in await mongo.db.list_collection_names():
        await mongo.db.create_collection('facts')
        return []

    cursor = mongo.facts.find({'nickname': nickname}).sort('confidence', -1).limit(limit)
    facts = await cursor.to_list(length=limit)
    return [UserFact.model_validate(f) for f in facts]


async def get_fact_by_id(fact_id: str) -> UserFact | None:
    logger.debug('Fetching fact by id', extra=event('DB_FACT_FETCH', fact_id=fact_id))
    fact = await mongo.facts.find_one({'_id': ObjectId(fact_id)})
    return UserFact.model_validate(fact) if fact else None


async def update_fact(
    fact_id: str,
    confidence: float | None = None,
    text: str | None = None,
) -> None:
    update_data = {}
    if confidence is not None:
        update_data['confidence'] = confidence

    if text is not None:
        update_data['text'] = text

    if not update_data:
        return

    update_data['updated_at'] = datetime.now(timezone.utc).timestamp()
    await mongo.facts.update_one({'_id': ObjectId(fact_id)}, {'$set': update_data})


async def create_fact(nickname: str, text: str, confidence: float) -> UserFact:
    logger.debug('Saving fact', extra=event('DB_FACT_WRITE', nickname=nickname))
    fact = UserFact(nickname=nickname, text=text, confidence=confidence)
    return await save_fact(fact)


async def save_fact(fact: UserFact) -> UserFact:
    now_ts = datetime.now(timezone.utc).timestamp()
    data = {
        'nickname': fact.nickname,
        'text': fact.text,
        'confidence': fact.confidence,
        'created_at': now_ts,
        'updated_at': now_ts,
    }
    result = await mongo.facts.insert_one(data)
    data['_id'] = result.inserted_id
    return UserFact.model_validate(data)


async def decay_facts(up_to_date: datetime, decay_amount: float) -> None:
    up_to_date_ts = up_to_date.timestamp()
    cursor = mongo.facts.find(
        {
            '$or': [
                {'updated_at': {'$lt': up_to_date_ts}},
                {'updated_at': {'$exists': False}, 'created_at': {'$lt': up_to_date_ts}},
            ]
        }
    )
    facts = await cursor.to_list(length=1000)
    logger.info('Decaying stale facts', extra=event('FACT_DECAY_RUN', count=len(facts)))

    for fact_data in facts:
        fact = UserFact.model_validate(fact_data)
        new_confidence = round(fact.confidence - decay_amount, 10)
        if new_confidence <= 0:
            # TODO: only the Mongo row is deleted here — the matching Qdrant point in
            # `facts_embedding_client` (src/embeddings/facts.py) is left behind, so a
            # deleted fact can still surface as a `search_facts` hit and get
            # reinforced back into existence. Pre-existing, not introduced by this
            # refactor.
            await mongo.facts.delete_one({'_id': fact_data['_id']})
            logger.info(
                'Fact deleted, confidence decayed to zero',
                extra=event(
                    'FACT_DELETED',
                    fact_id=str(fact_data['_id']),
                    reason='confidence_zero',
                ),
            )
        else:
            await mongo.facts.update_one(
                {'_id': fact_data['_id']}, {'$set': {'confidence': new_confidence}}
            )
