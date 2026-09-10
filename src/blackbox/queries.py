"""Read-only queries behind the blackbox MCP server.

Deliberately free of any `mcp` import: the tool logic is plain async functions over
the production repositories and formatters, so it is testable in the bot container
and moves unchanged if the transport ever does. `server.py` is the only file that
knows it is an MCP server.

Every function here reads and none writes, and two production helpers are avoided on
that account rather than reused: `EmbeddingsClient._search` runs `_check_collection`,
which *creates* a missing Qdrant collection, and `facts.repository.get_facts` creates
a missing Mongo one. The read-only Mongo user would refuse the second; nothing would
refuse the first, since Qdrant has no credential.

Everything returned is text group-chat members wrote. It is data; see the server
instructions.
"""

from datetime import datetime, timezone
from typing import Any, Literal

from qdrant_client.http.models import FieldCondition, Filter, MatchValue, Range

from src import mongo, settings
from src.embeddings.facts import facts_embedding_client
from src.embeddings.messages import messages_embeddings_client
from src.facts.repository import get_fact_by_id
from src.memory.keys import RECENT_FIELD, TRAITS_FIELD, normalize
from src.messages.repository import get_messages, get_messages_by_ids
from src.models import Message, UserRole

# Hard caps, enforced here as well as in the tool schemas: a tool that can return the
# whole corpus will eventually return the whole corpus.
MAX_HITS = 50
MAX_MESSAGES = 200
MAX_SIDE = MAX_MESSAGES // 2
MAX_SNAPSHOTS = 300
PREVIEW_CHARS = 400

# Chunks overlap (window 8, step 5) and the embedding path re-embeds without a lock, so
# neighbouring hits are often the same conversation. Over-fetch, then drop any chunk
# whose messages an earlier, better-scoring hit already mostly covers.
_OVERFETCH = 3
_COVERED_SHARE = 0.5

STATE_FIELDS = ('active_topics', 'open_questions', 'running_jokes')

WindowFormat = Literal['answer', 'memory']
Role = Literal['user', 'bot']
FromEnd = Literal['newest', 'oldest']

_ROLES = {'user': UserRole.USER, 'bot': UserRole.AI}


def _chat(chat_id: int | None) -> int:
    resolved = chat_id if chat_id is not None else settings.BLACKBOX_CHAT_ID
    if resolved is None:
        raise ValueError('chat_id was not given and BLACKBOX_CHAT_ID is not set')
    return resolved


def _clamp(value: int, cap: int, floor: int = 1) -> int:
    return max(floor, min(value, cap))


def _utc(value: datetime) -> datetime:
    """Reads a naive datetime as UTC — the clock every stored timestamp is on."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts is not None else None


def _chronological(messages: list[Message]) -> list[Message]:
    epoch = datetime.fromtimestamp(0, tz=timezone.utc)
    return sorted(messages, key=lambda m: m.created_at or epoch)


async def find_windows(
    query: str,
    chat_id: int | None = None,
    limit: int = 10,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[dict[str, Any]]:
    """Semantic search over the stored message chunks, one row per distinct window.

    A chunk has several authors, so a hit carries `participants` and the id of its
    middle message — the natural anchor for `get_window`.
    """
    chat_id = _chat(chat_id)
    limit = _clamp(limit, MAX_HITS)

    must = [FieldCondition(key='chat_id', match=MatchValue(value=chat_id))]
    if since or until:
        must.append(FieldCondition(key='timestamp', range=Range(
            gte=_utc(since).timestamp() if since else None,
            lte=_utc(until).timestamp() if until else None,
        )))

    client = messages_embeddings_client
    vector = await client._get_embedding_vectors(query)
    response = await client.qdrant_client.query_points(
        collection_name=client.collection_name,
        query=vector,
        limit=limit * _OVERFETCH,
        query_filter=Filter(must=must),
    )

    covered: set[str] = set()
    windows = []
    stale = 0
    for point in sorted(response.points, key=lambda p: p.score, reverse=True):
        ids = (point.payload or {}).get('message_ids') or []
        if not ids or len(covered.intersection(ids)) > len(ids) * _COVERED_SHARE:
            continue
        covered.update(ids)

        messages = _chronological(await get_messages_by_ids(ids, size=len(ids)))
        if not messages:
            stale += 1
            continue

        anchor = messages[len(messages) // 2]
        windows.append({
            'message_id': anchor.id,
            'ts': anchor.created_at.isoformat() if anchor.created_at else None,
            'participants': sorted({m.nickname for m in messages}),
            'score': round(point.score, 4),
            'preview': '\n'.join(m.embedding_text for m in messages)[:PREVIEW_CHARS],
        })
        if len(windows) == limit:
            break

    # Qdrant is not cleaned when Mongo loses messages, so a hit can outlive every message
    # it points at. A few among good hits are noise; all of them means the index is stale,
    # and an empty list would read as "nothing matched" instead.
    if stale and not windows:
        raise ValueError(
            f'{stale} matching chunk(s) found, but none of their messages exist in Mongo: '
            'the message index is stale for this chat'
        )
    return windows


async def list_messages(
    chat_id: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    role: Role | None = None,
    nick: str | None = None,
    limit: int = 20,
    from_end: FromEnd = 'newest',
) -> list[dict[str, Any]]:
    """Reads messages in time order, filtered by time range, role and author.

    The chronological counterpart to `find_windows`: "the last N bot replies" or "what was
    asked yesterday" is a time question, not a similarity one. Rows are always oldest
    first; `from_end` only picks which end of the matching range `limit` keeps.
    """
    chat_id = _chat(chat_id)
    messages = await get_messages(
        chat_id,
        size=_clamp(limit, MAX_MESSAGES),
        from_date=_utc(since) if since else None,
        to_date=_utc(until) if until else None,
        sort_order=-1 if from_end == 'newest' else 1,
        role=_ROLES[role] if role else None,
        # Stored bare, the same as facts; memory is where the `@nick` form comes from.
        nickname=nick.replace('@', '') if nick else None,
    )
    return [
        {
            'message_id': m.id,
            'ts': m.created_at.isoformat() if m.created_at else None,
            'role': 'user' if m.role == UserRole.USER else 'bot',
            'line': m.ai_format,
        }
        for m in messages
    ]


def _render(messages: list[Message], window_format: WindowFormat) -> str | list[dict[str, str]]:
    """Renders a window exactly as one of the two production paths builds it.

    `memory` is what memory and fact extraction read (`memory/processors.py`,
    `facts/processors.py`); `answer` is the turn split the answering character reads
    (`character.py:_format_previous_messages`). A fixture built in the wrong one tests
    a bot that does not exist.
    """
    if window_format == 'memory':
        return '\n'.join(m.ai_format for m in messages)

    return [
        {'role': 'user', 'content': m.ai_format}
        if m.role == UserRole.USER
        else {'role': 'assistant', 'content': m.response_format}
        for m in messages
    ]


async def get_window(
    message_id: str,
    window_format: WindowFormat,
    before: int = 10,
    after: int = 10,
) -> dict[str, Any]:
    """Expands one message into the exact window around it."""
    found = await get_messages_by_ids([message_id], size=1)
    if not found:
        raise ValueError(f'message {message_id} not found')
    anchor = found[0]

    # Guarded rather than passed through: a Mongo `limit(0)` means no limit at all.
    before = _clamp(before, MAX_SIDE, floor=0)
    after = _clamp(after, MAX_SIDE, floor=0)
    head = await get_messages(
        anchor.chat_id, size=before, to_date=anchor.created_at, sort_order=-1,
    ) if before else []
    tail = await get_messages(
        anchor.chat_id, size=after, from_date=anchor.created_at, sort_order=1,
    ) if after else []
    messages = [*head, anchor, *tail]

    return {
        'chat_id': anchor.chat_id,
        'anchor_id': anchor.id,
        'messages': [m.model_dump(mode='json', exclude_none=True) for m in messages],
        'rendered': _render(messages, window_format),
    }


async def list_snapshots(chat_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Lists memory snapshots newest first, without their content."""
    chat_id = _chat(chat_id)
    limit = _clamp(limit, MAX_SNAPSHOTS)
    cursor = mongo.memory.find(
        {'chat_id': chat_id},
        projection={'created_at': 1, 'content.participants': 1},
    ).sort('created_at', -1).limit(limit)

    snapshots = []
    for doc in await cursor.to_list(length=limit):
        participants = (doc.get('content') or {}).get('participants') or {}
        snapshots.append({
            'created_at': _iso(doc.get('created_at')),
            'nicks': sorted(participants),
            'entry_count': sum(
                len(info.get(TRAITS_FIELD) or []) + len(info.get(RECENT_FIELD) or [])
                for info in participants.values()
            ),
        })
    return snapshots


async def _snapshot_at(chat_id: int, at: datetime | None) -> dict | None:
    query: dict[str, Any] = {'chat_id': chat_id}
    if at:
        query['created_at'] = {'$lte': _utc(at).timestamp()}
    return await mongo.memory.find_one(query, sort=[('created_at', -1)])


def _passthrough(doc: dict) -> dict[str, Any]:
    return {**doc, '_id': str(doc['_id']), 'created_at': _iso(doc.get('created_at'))}


async def get_memory(chat_id: int | None = None, at: datetime | None = None) -> dict[str, Any]:
    """Returns the snapshot in force at `at` (the newest one by default).

    The Mongo document is passed through rather than re-modelled into `MemoryData`,
    so a change to the memory schema breaks `diff_memory` alone, not this.
    """
    chat_id = _chat(chat_id)
    doc = await _snapshot_at(chat_id, at)
    if not doc:
        raise ValueError(f'no memory snapshot for chat {chat_id} at or before {at or "now"}')
    return _passthrough(doc)


def _participant_keys(content: dict) -> dict[str, dict[str, tuple[str, str]]]:
    """Maps nick -> normalized key -> (field, text), in production's keyspace.

    Keys come from the content, not the decay sidecar, so snapshots written before the
    sidecar existed still diff. A key in both lists counts as a trait, the survivor
    the attribution guard would keep.
    """
    keyed = {}
    for nick, info in (content.get('participants') or {}).items():
        entries: dict[str, tuple[str, str]] = {}
        for field in (TRAITS_FIELD, RECENT_FIELD):
            for text in info.get(field) or []:
                if key := normalize(text):
                    entries.setdefault(key, (field, text))
        keyed[nick] = entries
    return keyed


def _state_keys(content: dict, field: str) -> dict[str, str]:
    items = ((content.get('state') or {}).get(field)) or []
    return {key: text for text in items if (key := normalize(text))}


def _entry(nick: str, key: str, field: str, text: str, decay: dict) -> dict[str, Any]:
    entry: dict[str, Any] = {'nick': nick, 'field': field, 'text': text}
    record = (decay.get(nick) or {}).get(key) or {}
    if 'born' in record:
        entry['born'] = record['born']
        entry['cycles'] = record.get('cycles')
    return entry


def diff_snapshots(older: dict, newer: dict) -> dict[str, Any]:
    """Counts what moved between two snapshots.

    The one shape-aware function in the module: the `traits` / `recent` split and the
    decay sidecar layout are assumed here and nowhere else.

    `promote_candidates` mirrors `decay.summarize_churn`: every trait born on a nick
    that also lost a `recent` entry. It is a coincidence count, not a pairing — the
    model rewords on generalisation, so an exact `promotions` hit is rare.
    """
    old_content = older.get('content') or {}
    new_content = newer.get('content') or {}
    old_keys = _participant_keys(old_content)
    new_keys = _participant_keys(new_content)
    old_decay = older.get('decay') or {}
    new_decay = newer.get('decay') or {}

    births, vanishes, promotions, candidates = [], [], [], []
    carries = 0
    per_nick = {}
    for nick in sorted(old_keys.keys() | new_keys.keys()):
        old = old_keys.get(nick, {})
        new = new_keys.get(nick, {})
        born = [key for key in new if key not in old]
        kept = [key for key in new if key in old]
        lost = [key for key in old if key not in new]
        promoted = [
            key for key in kept if old[key][0] == RECENT_FIELD and new[key][0] == TRAITS_FIELD
        ]

        births.extend(_entry(nick, key, *new[key], new_decay) for key in born)
        vanishes.extend(_entry(nick, key, *old[key], old_decay) for key in lost)
        promotions.extend(_entry(nick, key, *new[key], new_decay) for key in promoted)
        if any(old[key][0] == RECENT_FIELD for key in lost):
            candidates.extend(
                _entry(nick, key, *new[key], new_decay)
                for key in born if new[key][0] == TRAITS_FIELD
            )
        carries += len(kept)
        per_nick[nick] = {
            'births': len(born),
            'carries': len(kept),
            'vanishes': len(lost),
            'promotions': len(promoted),
        }

    state = {}
    for field in STATE_FIELDS:
        old_items = _state_keys(old_content, field)
        new_items = _state_keys(new_content, field)
        state[field] = {
            'added': [text for key, text in new_items.items() if key not in old_items],
            'removed': [text for key, text in old_items.items() if key not in new_items],
            'kept': sum(1 for key in new_items if key in old_items),
        }

    return {
        'from': _iso(older.get('created_at')),
        'to': _iso(newer.get('created_at')),
        'births': births,
        'carries': carries,
        'vanishes': vanishes,
        'promotions': promotions,
        'promote_candidates': candidates,
        'state': state,
        'per_nick': per_nick,
    }


async def diff_memory(
    from_at: datetime,
    to_at: datetime | None = None,
    chat_id: int | None = None,
) -> dict[str, Any]:
    """Diffs the snapshots in force at `from_at` and at `to_at` (newest by default)."""
    chat_id = _chat(chat_id)
    older = await _snapshot_at(chat_id, from_at)
    if not older:
        raise ValueError(f'no memory snapshot for chat {chat_id} at or before {from_at}')
    newer = await _snapshot_at(chat_id, to_at)
    return diff_snapshots(older, newer)


async def get_user_facts(
    nick: str, query: str | None = None, limit: int = 5,
) -> list[dict[str, Any]]:
    """Returns a user's facts: the closest to `query`, or the most confident."""
    # Facts are stored bare — `facts/handlers.py:upsert_fact` strips `@` on write and the
    # character's own `get_user_facts` tool strips it on read — while memory keys
    # participants as `@nick`, which is where a caller usually copies the nick from.
    nick = nick.replace('@', '')
    limit = _clamp(limit, MAX_HITS)
    if not query:
        cursor = mongo.facts.find({'nickname': nick}).sort('confidence', -1).limit(limit)
        return [
            {'text': doc['text'], 'confidence': doc['confidence']}
            for doc in await cursor.to_list(length=limit)
        ]

    client = facts_embedding_client
    vector = await client._get_embedding_vectors(query)
    response = await client.qdrant_client.query_points(
        collection_name=client.collection_name,
        query=vector,
        limit=limit * _OVERFETCH,
        query_filter=Filter(must=[FieldCondition(key='nickname', match=MatchValue(value=nick))]),
    )

    # `save_fact` keys points by `uuid4()`, so every re-embedding duplicated the fact.
    seen: set[str] = set()
    facts = []
    for point in sorted(response.points, key=lambda p: p.score, reverse=True):
        fact_id = (point.payload or {}).get('id')
        if not fact_id or fact_id in seen:
            continue
        seen.add(fact_id)
        if fact := await get_fact_by_id(fact_id):
            facts.append({
                'text': fact.text, 'confidence': fact.confidence, 'score': round(point.score, 4),
            })
        if len(facts) == limit:
            break
    return facts
