from datetime import datetime, timezone
from decimal import Decimal

from bson import ObjectId

from src import mongo
from src.logs import logger
from src.models import UserFact


async def save_fact(nickname: str, text: str, confidence: float) -> UserFact:
    logger.info(f"Saving fact for chat {nickname}")
    fact = UserFact(
        nickname=nickname,
        text=text,
        confidence=confidence,
    )
    return await _save_fact(fact)


async def get_facts(nickname: str, limit: int = 5) -> list[UserFact]:
    logger.debug(f"Fetching facts for {nickname}")
    if 'facts' not in await mongo.db.list_collection_names():
        await mongo.db.create_collection('facts')
        return []

    cursor = mongo.facts.find({'nickname': nickname}).sort('confidence', -1).limit(limit)
    facts = await cursor.to_list(length=limit)

    return [
        UserFact( # todo check why not validated as is
            _id=str(f['_id']),
            nickname=f['nickname'],
            text=f['text'],
            confidence=float(Decimal(f['confidence'])),
            created_at=datetime.fromtimestamp(f['created_at'], tz=timezone.utc),
        ) for f in facts
    ]


async def get_fact_by_id(fact_id: str) -> UserFact | None:
    logger.debug(f"Fetching fact by id {fact_id}")
    fact = await mongo.facts.find_one({'_id': fact_id})
    return UserFact.model_validate(fact) if fact else None


async def update_fact(fact_id: str, confidence: float | None = None, text: str | None = None):
    update_data = {}
    if confidence is not None:
        update_data['confidence'] = confidence

    if text is not None:
        update_data['text'] = text

    if not update_data:
        return

    await mongo.facts.update_one(
        {'_id': ObjectId(fact_id)},
        {'$set': update_data}
    )

async def _save_fact(fact: UserFact):
    data = {
        'nickname': fact.nickname,
        'text': fact.text,
        'confidence': fact.confidence,
        'created_at': datetime.now(timezone.utc).timestamp()
    }
    result = await mongo.facts.insert_one(data)
    fact.id = result.inserted_id
    return fact
