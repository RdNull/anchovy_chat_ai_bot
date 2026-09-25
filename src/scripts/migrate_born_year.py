# ruff: noqa: T201 -- a CLI script; printing is its output
"""One-off: widen the two-digit year in memory decay sidecars to four digits.

`DecayRecord.born` used to be stamped `ГГ-ММ-ДД ЧЧ:ММ` and is now `ГГГГ-ММ-ДД ЧЧ:ММ`. A legacy
`26-08-24 13:06` no longer parses (the age prefix silently vanishes) and, compared as a
string, sorts *after* every new stamp, so the wrong `recent` entries would be evicted.
Idempotent: only values that still start with two digits are rewritten.

    python -m src.scripts.migrate_born_year --dry-run
    python -m src.scripts.migrate_born_year
"""

import argparse
import asyncio
import re
from collections import Counter

from src import mongo

parser = argparse.ArgumentParser(description='Widen two-digit years in decay sidecars.')
parser.add_argument('--dry-run', action='store_true', help='print counts, write nothing')

_LEGACY_BORN = re.compile(r'^(\d{2}-\d{2}-\d{2} \d{2}:\d{2})$')


def widen_decay(decay: dict) -> tuple[dict, int]:
    """Returns the sidecar with every legacy `born` widened, and how many were."""
    changed = 0
    widened = {}
    for nick, records in decay.items():
        widened[nick] = {}
        for key, record in records.items():
            born = record.get('born', '')
            if _LEGACY_BORN.match(born):
                changed += 1
                born = f'20{born}'
            widened[nick][key] = {**record, 'born': born}

    return widened, changed


async def run(dry_run: bool) -> None:
    mode = 'DRY RUN (nothing written)' if dry_run else 'WRITING'
    print(f'{mode}: memory snapshots with two-digit `born` years')

    per_chat: Counter = Counter()
    snapshots = 0
    async for doc in mongo.memory.find({'decay': {'$exists': True}}, {'chat_id': 1, 'decay': 1}):
        widened, changed = widen_decay(doc['decay'])
        if not changed:
            continue

        snapshots += 1
        per_chat[doc['chat_id']] += changed
        if not dry_run:
            await mongo.memory.update_one({'_id': doc['_id']}, {'$set': {'decay': widened}})

    print('snapshots rewritten:', snapshots)
    print('records widened, per chat:', dict(per_chat) or 'none')


if __name__ == '__main__':  # pragma: no cover
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run))
