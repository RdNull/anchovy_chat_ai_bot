# ruff: noqa: T201 -- a CLI script; printing is its output
"""Replace the `facts` store with a hand-picked seed: every fact lands as `confirmed`.

The seed data is deliberately not part of the repo or the image. It is piped in:

    python -m src.scripts.seed_facts --file - --dry-run < facts-seed.json
    python -m src.scripts.seed_facts --file - < facts-seed.json

A real run drops `mongo.facts` and the Qdrant `facts` collection first; take a backup.
"""

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter, field_validator

from src import mongo
from src.embeddings.facts import facts_embedding_client
from src.facts.models import FactKind, FactStatus
from src.facts.repository import create_fact, ensure_indexes
from src.log_context import push_log_context
from src.logs import elapsed_ms, event, logger
from src.models import BaseModel

parser = argparse.ArgumentParser(description='Replace stored facts with a seed list.')
parser.add_argument('--file', required=True, help="Path to a JSON list, or '-' for stdin.")
parser.add_argument('--dry-run', action='store_true', help='Validate and report, write nothing.')


class SeedFact(BaseModel):
    nickname: str
    kind: FactKind
    text: str

    @field_validator('nickname')
    @classmethod
    def _bare(cls, value: str) -> str:
        return value.replace('@', '')


def read_seed(path: str) -> list[SeedFact]:
    raw = sys.stdin.read() if path == '-' else Path(path).read_text(encoding='utf-8')
    return TypeAdapter(list[SeedFact]).validate_python(json.loads(raw))


def describe(seed: list[SeedFact]) -> str:
    nicknames = {fact.nickname for fact in seed}
    kinds = Counter(fact.kind.value for fact in seed)
    return f'{len(seed)} facts across {len(nicknames)} nicknames, by kind: {dict(kinds)}'


async def seed_facts(seed: list[SeedFact], dry_run: bool) -> None:
    push_log_context()  # one CLI process, asyncio.run's own fresh task -- nothing to reset
    print(describe(seed))
    if dry_run:
        print('dry run: nothing written')
        return

    started = time.monotonic()
    client = facts_embedding_client
    await mongo.facts.drop()
    if await client.qdrant_client.collection_exists(client.collection_name):
        await client.qdrant_client.delete_collection(client.collection_name)

    await ensure_indexes()
    today = datetime.now(UTC).date()
    for item in seed:
        fact = await create_fact(item.nickname, item.kind, item.text, FactStatus.CONFIRMED, today)
        await client.save_fact(fact)

    logger.info(
        'Facts seeded',
        extra=event('FACTS_SEEDED', count=len(seed), elapsed_ms=elapsed_ms(started)),
    )
    print(f'seeded {len(seed)} confirmed facts')


if __name__ == '__main__':  # pragma: no cover
    args = parser.parse_args()
    asyncio.run(seed_facts(read_seed(args.file), args.dry_run))
