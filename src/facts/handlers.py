from datetime import datetime

from src import mongo
from src.facts.repository import create_fact, update_fact
from src.logs import logger
from src.models import UserFact


async def upsert_fact(nickname: str, text: str, confidence: float) -> None:
    from src.embeddings.facts import facts_embedding_client

    if confidence < 0.5 or confidence > 1:
        logger.warning(f"Skipping fact with invalid confidence {confidence} for {nickname}")
        return

    nickname = nickname.replace('@', '')

    similar_facts = await facts_embedding_client.search_facts(nickname, text, limit=1)
    if similar_facts:
        similar_fact = similar_facts[0]
        existing_confidence = similar_fact.fact.confidence
        if existing_confidence >= confidence:
            new_confidence = min(existing_confidence + 0.1, 1)
            await update_fact(similar_fact.fact.id, confidence=new_confidence)
            logger.info(f"Reinforced fact {similar_fact.fact.id} confidence to {new_confidence}")
        else:
            await update_fact(similar_fact.fact.id, confidence=confidence, text=text)
            logger.info(f"Updated fact {similar_fact.fact.id} with new confidence {confidence}")
        return

    fact = await create_fact(nickname, text, confidence)
    logger.info(f"Saved new fact {fact.id} for {nickname}")


async def decay_facts(up_to_date: datetime, decay_amount: float) -> None:
    up_to_date_ts = up_to_date.timestamp()
    cursor = mongo.facts.find({
        '$or': [
            {'updated_at': {'$lt': up_to_date_ts}},
            {'updated_at': {'$exists': False}, 'created_at': {'$lt': up_to_date_ts}},
        ]
    })
    facts = await cursor.to_list(length=None)
    logger.info(f"Decaying {len(facts)} stale facts")

    for fact_data in facts:
        fact = UserFact.model_validate(fact_data)
        new_confidence = round(fact.confidence - decay_amount, 10)
        if new_confidence <= 0:
            await mongo.facts.delete_one({'_id': fact_data['_id']})
            logger.info(f"Deleted fact {fact_data['_id']} (confidence decayed to zero)")
        else:
            await mongo.facts.update_one(
                {'_id': fact_data['_id']},
                {'$set': {'confidence': new_confidence}}
            )
