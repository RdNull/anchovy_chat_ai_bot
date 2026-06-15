import argparse
import asyncio

from src import mongo
from src.embeddings.facts import facts_embedding_client
from src.logs import logger
from src.models import UserFact

parser = argparse.ArgumentParser(description='Generate embeddings for facts in DB.')
parser.add_argument('--nickname', type=str, help='Limit to a single user nickname.')
parser.add_argument('--batch-size', type=int, default=100)


async def create_fact_embeddings(nickname: str | None, batch_size: int):
    query = {'nickname': nickname} if nickname else {}
    total = await mongo.facts.count_documents(query)
    logger.info(f"Found {total} facts to embed")

    cursor = mongo.facts.find(query).batch_size(batch_size)
    processed = 0
    async for raw in cursor:
        fact = UserFact.model_validate(raw)
        await facts_embedding_client.save_fact(fact)
        processed += 1
        if processed % batch_size == 0:
            logger.info(f"Embedded {processed}/{total} facts")

    logger.info(f"Done. Embedded {processed} facts")


if __name__ == '__main__':  # pragma: no cover
    args = parser.parse_args()
    asyncio.run(create_fact_embeddings(nickname=args.nickname, batch_size=args.batch_size))
