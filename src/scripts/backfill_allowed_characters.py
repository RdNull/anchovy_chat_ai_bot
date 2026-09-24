# ruff: noqa: T201 -- a CLI script; printing is its output
"""One-off: allow every chat's current private character in that chat.

Private characters are only available to chats whose `chat_settings.allowed_characters`
names them, and `get_chat_character` falls back to the default the moment a chat's stored
character is not available. Chats that were already on a now-private character (or on a
`debug`-style one) would therefore lose it on the first reply after the deploy. This adds
each chat's stored private character to its allow list. Public characters need nothing.
Idempotent: `$addToSet`, and an already-allowed chat is not counted as a change.

    python -m src.scripts.backfill_allowed_characters --dry-run
    python -m src.scripts.backfill_allowed_characters
"""

import argparse
import asyncio
from collections import Counter

from src import mongo, settings
from src.characters.registry import load_characters

parser = argparse.ArgumentParser(description='Allow each chat its current private character.')
parser.add_argument('--dry-run', action='store_true', help='print counts, write nothing')


def private_codes() -> set[str]:
    """Every private yaml, `debug` included even when its flag is off."""
    characters = load_characters(settings.CHARACTERS_DIRECTORY, include_debug=True)
    return {code for code, character in characters.items() if not character.public}


async def backfill(dry_run: bool) -> Counter:
    private = private_codes()
    granted: Counter = Counter()

    async for doc in mongo.chat_settings.find({'character_code': {'$in': sorted(private)}}):
        code = doc['character_code']
        if code in (doc.get('allowed_characters') or []):
            continue

        granted[code] += 1
        print(f'chat {doc["chat_id"]}: allow {code}')
        if not dry_run:
            await mongo.chat_settings.update_one(
                {'_id': doc['_id']}, {'$addToSet': {'allowed_characters': code}}
            )

    return granted


async def run(dry_run: bool) -> None:
    mode = 'DRY RUN (nothing written)' if dry_run else 'WRITING'
    print(f'{mode}: allowing each chat its current private character')
    granted = await backfill(dry_run)
    print('grants, per code:', dict(granted) or 'none')


if __name__ == '__main__':  # pragma: no cover
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run))
