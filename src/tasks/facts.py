import time
from datetime import datetime, timedelta, UTC

from src import settings
from src.embeddings.facts import facts_embedding_client
from src.facts.repository import delete_fact, find_expired_candidates
from src.log_context import log_context
from src.logs import elapsed_ms, event, logger


async def expire_candidates() -> int:
    """Deletes candidates unseen for `FACTS_CANDIDATE_TTL_DAYS`; confirmed facts are never touched."""
    cutoff = datetime.now(UTC) - timedelta(days=settings.FACTS_CANDIDATE_TTL_DAYS)
    expired = await find_expired_candidates(cutoff)
    for fact in expired:
        await delete_fact(fact.id)
        await facts_embedding_client.delete_fact(fact.id)
        logger.info(
            'Candidate fact expired',
            extra=event('FACT_DELETED', fact_id=fact.id, nickname=fact.nickname, reason='expired'),
        )

    return len(expired)


async def run_candidate_expiry():
    with log_context(task='candidate_expiry'):
        logger.info('Running scheduled task', extra=event('TASK_START'))
        started = time.monotonic()
        try:
            count = await expire_candidates()
            logger.info(
                'Scheduled task finished',
                extra=event('TASK_DONE', count=count, elapsed_ms=elapsed_ms(started)),
            )
        except Exception:
            logger.error(
                'Failed to run candidate expiry',
                exc_info=True,
                extra=event('TASK_FAILED'),
            )
