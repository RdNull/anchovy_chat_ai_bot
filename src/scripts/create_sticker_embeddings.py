import argparse
import asyncio
import time
from uuid import uuid4

from src.embeddings.stickers import stickers_embedding_client
from src.log_context import log_context
from src.logs import elapsed_ms, event, logger
from src.messages.media.repository import _parse_media_description
from src.models import MessageMediaStatus, MessageMediaTypes
from src import mongo

parser = argparse.ArgumentParser(description='Generate embeddings for stickers in DB.')
parser.add_argument('--batch-size', type=int, default=100)


async def create_sticker_embeddings(batch_size: int):
    """Re-indexes the whole sticker corpus.

    Not needed at launch, and not the way the corpus fills: the media pipeline retypes
    and indexes a sticker the first time it is seen after the sticker unit shipped, so
    ordinary traffic does the backfill. This is the re-index path after an
    embedding-model change. Point ids are derived from `unique_id`, so re-running
    upserts rather than duplicating.
    """
    with log_context(request_id=uuid4().hex[:8]):
        query = {
            'type': MessageMediaTypes.STICKER.value, 'status': MessageMediaStatus.READY.value,
        }
        total = await mongo.media_descriptions.count_documents(query)
        started = time.monotonic()
        logger.info('Backfill starting', extra=event('BACKFILL_START', kind='stickers', total=total))

        cursor = mongo.media_descriptions.find(query).batch_size(batch_size)
        processed = 0
        async for raw in cursor:
            await stickers_embedding_client.save_sticker(_parse_media_description(raw))
            processed += 1
            if processed % batch_size == 0:
                logger.info(
                    'Backfill progress',
                    extra=event(
                        'BACKFILL_PROGRESS', kind='stickers', processed=processed, total=total,
                    ),
                )

        logger.info(
            'Backfill finished',
            extra=event(
                'BACKFILL_DONE', kind='stickers', processed=processed,
                elapsed_ms=elapsed_ms(started),
            ),
        )


if __name__ == '__main__':  # pragma: no cover
    args = parser.parse_args()
    asyncio.run(create_sticker_embeddings(batch_size=args.batch_size))
