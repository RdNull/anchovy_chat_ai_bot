# ruff: noqa: T201 -- a CLI script; printing is its output
"""One-off: tag legacy bot messages and reactions with the character that made them.

Bot messages saved as `AnchovyAiBot(<name>)` get `character_code` plus the tagged nickname
`AnchovyAiBot[<code>]`; bot reactions stored under the plain `AnchovyAiBot` are moved to the
chat's current character's tagged nickname (the real author is not recoverable).
Only the last `--days` days are touched. Idempotent: both filters match legacy values only.

    python -m src.scripts.migrate_bot_identity --dry-run
    python -m src.scripts.migrate_bot_identity
"""

import argparse
import asyncio
import re
from collections import Counter
from datetime import datetime, timedelta, UTC
from pathlib import Path

import yaml

from src import mongo, settings
from src.characters.character import bot_nickname_for

parser = argparse.ArgumentParser(description='Tag legacy bot messages with character codes.')
parser.add_argument('--dry-run', action='store_true', help='print counts, write nothing')
parser.add_argument('--days', type=int, default=90)

_LEGACY_NICKNAME = re.compile(rf'^{re.escape(settings.BOT_NICKNAME)}\((.*)\)$')


def load_name_to_code() -> dict[str, str]:
    """Every yaml under the characters directory — `debug` included even when it is off,
    so its old lines still map.
    """
    name_to_code: dict[str, str] = {}
    for path in Path(settings.CHARACTERS_DIRECTORY).rglob('*.yaml'):
        name = yaml.safe_load(path.read_text())['name']
        if name in name_to_code:
            raise ValueError(f'two characters share the name {name!r}; cannot map it to a code')
        name_to_code[name] = path.stem

    return name_to_code


async def _chat_character_code(chat_id: int, known_codes: set[str]) -> str:
    doc = await mongo.chat_settings.find_one({'chat_id': chat_id})
    code = doc.get('character_code') if doc else None
    return code if code in known_codes else settings.DEFAULT_CHARACTER


async def migrate_messages(cutoff: float, dry_run: bool) -> dict:
    name_to_code = load_name_to_code()
    base_filter = {
        'role': 'ai',
        'created_at': {'$gte': cutoff},
        'nickname': {'$regex': _LEGACY_NICKNAME.pattern},
    }
    pipeline = [
        {'$match': base_filter},
        {'$group': {'_id': {'chat_id': '$chat_id', 'nickname': '$nickname'}, 'count': {'$sum': 1}}},
    ]
    per_chat: Counter = Counter()
    per_code: Counter = Counter()
    unmatched: Counter = Counter()
    mapped_nicknames: dict[str, str] = {}

    async for row in await mongo.messages.aggregate(pipeline):
        old_nickname = row['_id']['nickname']
        chat_id = row['_id']['chat_id']
        name = _LEGACY_NICKNAME.match(old_nickname).group(1)
        code = name_to_code.get(name)
        if code is None:
            unmatched[old_nickname] += row['count']
            continue

        mapped_nicknames[old_nickname] = code
        per_chat[chat_id] += row['count']
        per_code[code] += row['count']

    if not dry_run:
        for old_nickname, code in mapped_nicknames.items():
            await mongo.messages.update_many(
                {**base_filter, 'nickname': old_nickname},
                {'$set': {'character_code': code, 'nickname': bot_nickname_for(code)}},
            )

    return {'per_chat': per_chat, 'per_code': per_code, 'unmatched': unmatched}


async def migrate_reactions(cutoff: float, dry_run: bool) -> Counter:
    known_codes = set(load_name_to_code().values())
    legacy = settings.BOT_NICKNAME
    per_chat: Counter = Counter()
    chat_nicknames: dict[int, str] = {}

    cursor = mongo.messages.find(
        {'created_at': {'$gte': cutoff}, 'reactions': {'$exists': True, '$ne': {}}},
        {'chat_id': 1, 'reactions': 1},
    )
    async for doc in cursor:
        chat_id = doc['chat_id']
        updates = {}
        for emoji, nicknames in doc['reactions'].items():
            if legacy not in nicknames:
                continue

            if chat_id not in chat_nicknames:
                code = await _chat_character_code(chat_id, known_codes)
                chat_nicknames[chat_id] = bot_nickname_for(code)

            tagged = chat_nicknames[chat_id]
            replaced = [tagged if n == legacy else n for n in nicknames]
            updates[f'reactions.{emoji}'] = list(dict.fromkeys(replaced))

        if not updates:
            continue

        per_chat[chat_id] += len(updates)
        if not dry_run:
            await mongo.messages.update_one({'_id': doc['_id']}, {'$set': updates})

    return per_chat


async def run(days: int, dry_run: bool) -> None:
    cutoff = (datetime.now(UTC) - timedelta(days=days)).timestamp()
    mode = 'DRY RUN (nothing written)' if dry_run else 'WRITING'
    print(f'{mode}: bot messages since the last {days} days')

    messages = await migrate_messages(cutoff, dry_run)
    print('messages retagged, per code:', dict(messages['per_code']) or 'none')
    print('messages retagged, per chat:', dict(messages['per_chat']) or 'none')
    print('unmatched nicknames (left as is):', dict(messages['unmatched']) or 'none')

    reactions = await migrate_reactions(cutoff, dry_run)
    print('reaction entries remapped, per chat:', dict(reactions) or 'none')


if __name__ == '__main__':  # pragma: no cover
    args = parser.parse_args()
    asyncio.run(run(days=args.days, dry_run=args.dry_run))
