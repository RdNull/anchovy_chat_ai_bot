# ruff: noqa: T201 -- a CLI script; printing is its output
"""One-off: file the bot's memory entry under its tagged nickname.

Memory snapshots written before character-tagged nicknames hold the bot as
`@<BOT_NICKNAME>`. The memory prompt tells the model never to rename a nickname, so left
alone it keeps carrying that entry forward and opens a second one for the tagged nick. This
renames the participant in each chat's *newest* snapshot — in `content.participants` and in
the `decay` sidecar, so entries keep their ages — to `@<BOT_NICKNAME>[<code>]`, `<code>`
being the chat's current character (the real author of old traits is not recoverable).
Older snapshots are left alone. A chat already holding the tagged nick is skipped and
reported, never overwritten. Idempotent: once renamed, the legacy key is gone.

    python -m src.scripts.rekey_bot_memory --dry-run
    python -m src.scripts.rekey_bot_memory
"""

import argparse
import asyncio

from src import mongo, settings
from src.characters.character import bot_nickname_for
from src.characters.loader import load_characters
from src.scripts.migrate_bot_identity import chat_character_code

parser = argparse.ArgumentParser(
    description='Rename the bot memory participant to its tagged nick.'
)
parser.add_argument('--dry-run', action='store_true', help='print what would change, write nothing')

LEGACY_KEY = f'@{settings.BOT_NICKNAME}'


def _renamed(mapping: dict, old: str, new: str) -> dict:
    """Same dict with one key swapped in place, so participant order is preserved."""
    return {(new if key == old else key): value for key, value in mapping.items()}


async def rekey(dry_run: bool) -> dict[str, list]:
    known_codes = set(load_characters(settings.CHARACTERS_DIRECTORY, include_debug=True))
    result: dict[str, list] = {'renamed': [], 'conflict': []}

    for chat_id in await mongo.memory.distinct('chat_id'):
        snapshot = await mongo.memory.find_one({'chat_id': chat_id}, sort=[('created_at', -1)])
        participants = snapshot['content']['participants']
        if LEGACY_KEY not in participants:
            continue

        code = await chat_character_code(chat_id, known_codes)
        new_key = f'@{bot_nickname_for(code)}'
        if new_key in participants:
            result['conflict'].append((chat_id, new_key))
            print(f'chat {chat_id}: {new_key} already exists, skipped')
            continue

        result['renamed'].append((chat_id, new_key))
        print(f'chat {chat_id}: {LEGACY_KEY} -> {new_key}')
        if dry_run:
            continue

        update = {'content.participants': _renamed(participants, LEGACY_KEY, new_key)}
        decay = snapshot.get('decay') or {}
        if LEGACY_KEY in decay:
            update['decay'] = _renamed(decay, LEGACY_KEY, new_key)

        await mongo.memory.update_one({'_id': snapshot['_id']}, {'$set': update})

    return result


async def run(dry_run: bool) -> None:
    mode = 'DRY RUN (nothing written)' if dry_run else 'WRITING'
    print(f"{mode}: renaming {LEGACY_KEY} in each chat's newest memory snapshot")
    result = await rekey(dry_run)
    print(f'renamed: {len(result["renamed"])}, skipped (conflict): {len(result["conflict"])}')


if __name__ == '__main__':  # pragma: no cover
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run))
