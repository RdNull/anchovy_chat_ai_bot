from datetime import datetime, timezone

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

    return [UserFact.model_validate(f) for f in facts]


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
