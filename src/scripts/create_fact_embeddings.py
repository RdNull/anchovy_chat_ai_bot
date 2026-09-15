"""Backfill embeddings for user fact documents in the database."""
import argparse
import asyncio
import time

from src.embeddings.facts import facts_embedding_client
from src.log_context import push_log_context
from src.logs import elapsed_ms, event, logger
from src.models import UserFact
from src import mongo

parser = argparse.ArgumentParser(description='Backfill embeddings for user facts in DB.')
parser.add_argument('--nickname', type=str, help='Limit to a single user nickname.')
parser.add_argument('--batch-size', type=int, default=100)


async def create_fact_embeddings(nickname: str | None, batch_size: int):
    """Generate embeddings for facts in the database.

    Args:
        nickname: Optional nickname to limit processing to a single user. If None, process all facts.
        batch_size: Number of facts to process before logging progress.
    """
    push_log_context()  # one CLI process, asyncio.run's own fresh task -- nothing to reset
    query = {'nickname': nickname} if nickname else {}
    total = await mongo.facts.count_documents(query)
    started = time.monotonic()
    logger.info('Backfill starting', extra=event('BACKFILL_START', kind='facts', total=total))

    cursor = mongo.facts.find(query).batch_size(batch_size)
    processed = 0
    async for raw in cursor:
        fact = UserFact.model_validate(raw)
        await facts_embedding_client.save_fact(fact)
        processed += 1
        if processed % batch_size == 0:
            logger.info(
                'Backfill progress',
                extra=event('BACKFILL_PROGRESS', kind='facts', processed=processed, total=total),
            )

    logger.info(
        'Backfill finished',
        extra=event(
            'BACKFILL_DONE', kind='facts', processed=processed, elapsed_ms=elapsed_ms(started),
        ),
    )


if __name__ == '__main__':  # pragma: no cover
    args = parser.parse_args()
    asyncio.run(create_fact_embeddings(nickname=args.nickname, batch_size=args.batch_size))
