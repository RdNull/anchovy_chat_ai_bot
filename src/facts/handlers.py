import time
from datetime import datetime, timedelta, timezone

from src.embeddings.facts import facts_embedding_client
from src.facts.processors import extract_facts
from src.facts.repository import create_fact, decay_facts, update_fact
from src.logs import elapsed_ms, event, logger
from src.messages.models import Message


async def update_user_facts(new_messages: list[Message]) -> None:
    started = time.monotonic()
    try:
        facts = await extract_facts(new_messages)
        for fact in facts:
            await upsert_fact(fact.nickname, fact.text, fact.confidence)

        logger.info(
            'Extracted and saved facts',
            extra=event(
                'FACT_EXTRACT', outcome='ok', count=len(facts),
                elapsed_ms=elapsed_ms(started),
            ),
        )
    except Exception:
        logger.error(
            'Error extracting facts from messages', exc_info=True,
            extra=event('FACT_EXTRACT', outcome='error'),
        )


async def upsert_fact(nickname: str, text: str, confidence: float) -> None:
    if confidence < 0.5 or confidence > 1:
        logger.warning(
            'Skipping fact with invalid confidence',
            extra=event(
                'FACT_REJECTED', reason='invalid_confidence', confidence=confidence,
                nickname=nickname,
            ),
        )
        return

    nickname = nickname.replace('@', '')

    similar_facts = await facts_embedding_client.search_facts(nickname, text, limit=1)
    if similar_facts:
        similar_fact = similar_facts[0]
        existing_confidence = similar_fact.fact.confidence
        if existing_confidence >= confidence:
            new_confidence = min(existing_confidence + 0.1, 1)
            await update_fact(similar_fact.fact.id, confidence=new_confidence)
            logger.info(
                'Reinforced fact confidence',
                extra=event('FACT_REINFORCED', fact_id=similar_fact.fact.id, confidence=new_confidence),
            )
        else:
            await update_fact(similar_fact.fact.id, confidence=confidence, text=text)
            logger.info(
                'Updated fact with new confidence',
                extra=event('FACT_UPDATED', fact_id=similar_fact.fact.id, confidence=confidence),
            )
        return

    fact = await create_fact(nickname, text, confidence)
    await facts_embedding_client.save_fact(fact)
    logger.info('Saved new fact', extra=event('FACT_SAVED', fact_id=fact.id, nickname=nickname))


async def decay_all_facts(decay_amount: float = 0.1) -> None:
    one_week_ago_ts = datetime.now(timezone.utc) - timedelta(weeks=1)
    await decay_facts(one_week_ago_ts, decay_amount)
