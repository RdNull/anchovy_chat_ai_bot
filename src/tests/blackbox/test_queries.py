from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from bson import ObjectId

from src import mongo, settings
from src.blackbox import queries
from src.embeddings.facts import facts_embedding_client
from src.embeddings.messages import messages_embeddings_client
from src.messages.repository import save_message
from src.models import UserRole
from src.tests.test_utils import make_message

CHAT_ID = 1
T0 = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


async def _seed_chat(count: int) -> list:
    messages = []
    for i in range(count):
        message = make_message(chat_id=CHAT_ID, text=f'msg{i}')
        await save_message(message)
        # `save_message` leaves the raw ObjectId; everything read back carries a str.
        message.id = str(message.id)
        messages.append(message)
    return messages


async def _save_snapshot(created_at: datetime, content: dict, decay: dict | None = None):
    await mongo.memory.insert_one({
        'chat_id': CHAT_ID,
        'content': content,
        'decay': decay or {},
        'created_at': created_at.timestamp(),
    })


def _points(*hits):
    return SimpleNamespace(points=[SimpleNamespace(score=s, payload=p) for s, p in hits])


def _bodies(rendered: str) -> list[str]:
    """Strips the `[ГГ-ММ-ДД ЧЧ:ММ] ` prefix, which is wall clock at save time."""
    return [line.split('] ', 1)[1] for line in rendered.split('\n')]


@pytest.fixture
def messages_qdrant(mocker):
    client = messages_embeddings_client
    mocker.patch.object(client, '_get_embedding_vectors', return_value=[0.1])
    return SimpleNamespace(
        query_points=mocker.patch.object(client.qdrant_client, 'query_points'),
        check_collection=mocker.patch.object(client, '_check_collection'),
        create_collection=mocker.patch.object(client.qdrant_client, 'create_collection'),
    )


@pytest.fixture
def facts_qdrant(mocker):
    client = facts_embedding_client
    mocker.patch.object(client, '_get_embedding_vectors', return_value=[0.1])
    return SimpleNamespace(
        query_points=mocker.patch.object(client.qdrant_client, 'query_points'),
        check_collection=mocker.patch.object(client, '_check_collection'),
        create_collection=mocker.patch.object(client.qdrant_client, 'create_collection'),
    )


# --- chat default ---

async def test_chat_id_falls_back_to_the_configured_chat(mocker):
    mocker.patch.object(settings, 'BLACKBOX_CHAT_ID', CHAT_ID)
    await _save_snapshot(T0, {'participants': {}})

    snapshots = await queries.list_snapshots()

    assert len(snapshots) == 1


async def test_missing_chat_id_without_a_default_raises(mocker):
    mocker.patch.object(settings, 'BLACKBOX_CHAT_ID', None)

    with pytest.raises(ValueError):
        await queries.list_snapshots()


# --- find_windows ---

async def test_find_windows_skips_a_window_an_earlier_hit_covers(messages_qdrant):
    ids = [m.id for m in await _seed_chat(12)]
    messages_qdrant.query_points.return_value = _points(
        (0.7, {'message_ids': ids[1:8]}),  # wholly inside the best hit
        (0.9, {'message_ids': ids[0:8]}),
        (0.8, {'message_ids': ids[5:12]}),  # 3 of 7 covered — under half, kept
    )

    windows = await queries.find_windows('query', chat_id=CHAT_ID)

    assert [w['message_id'] for w in windows] == [ids[4], ids[8]]
    assert [w['score'] for w in windows] == [0.9, 0.8]
    assert windows[0]['participants'] == ['user1']
    assert _bodies(windows[0]['preview']) == [f'user1: msg{i}' for i in range(8)]


async def test_find_windows_filters_by_chat_and_time_range(messages_qdrant):
    messages_qdrant.query_points.return_value = _points()

    await queries.find_windows(
        'query',
        chat_id=CHAT_ID,
        since=T0.replace(tzinfo=None),
        until=T0 + timedelta(days=1),
    )

    must = messages_qdrant.query_points.call_args.kwargs['query_filter'].must
    assert must[0].key == 'chat_id'
    assert must[0].match.value == CHAT_ID
    assert must[1].key == 'timestamp'
    assert must[1].range.gte == T0.timestamp()  # naive read as UTC
    assert must[1].range.lte == (T0 + timedelta(days=1)).timestamp()


async def test_find_windows_stops_at_limit_and_never_creates_a_collection(messages_qdrant):
    ids = [m.id for m in await _seed_chat(10)]
    messages_qdrant.query_points.return_value = _points(
        (0.9, {'message_ids': ids[0:5]}),
        (0.8, {'message_ids': ids[5:10]}),
    )

    windows = await queries.find_windows('query', chat_id=CHAT_ID, limit=1)

    assert len(windows) == 1
    assert messages_qdrant.query_points.call_args.kwargs['limit'] == 3
    assert messages_qdrant.check_collection.call_count == 0
    assert messages_qdrant.create_collection.call_count == 0


async def test_find_windows_with_only_stale_hits_raises(messages_qdrant):
    """Hits whose messages are all gone must not read as an empty search."""
    messages_qdrant.query_points.return_value = _points(
        (0.9, {'message_ids': [str(ObjectId()), str(ObjectId())]}),
        (0.8, {'message_ids': [str(ObjectId())]}),
    )

    with pytest.raises(ValueError, match='2 matching chunk'):
        await queries.find_windows('query', chat_id=CHAT_ID)


async def test_find_windows_skips_stale_hits_among_good_ones(messages_qdrant):
    ids = [m.id for m in await _seed_chat(3)]
    messages_qdrant.query_points.return_value = _points(
        (0.9, {'message_ids': [str(ObjectId())]}),
        (0.8, {'message_ids': ids}),
    )

    windows = await queries.find_windows('query', chat_id=CHAT_ID)

    assert [w['message_id'] for w in windows] == [ids[1]]


# --- get_window ---

async def test_get_window_memory_format_matches_extraction():
    messages = await _seed_chat(7)

    window = await queries.get_window(messages[3].id, 'memory', before=2, after=2)

    assert _bodies(window['rendered']) == [f'user1: msg{i}' for i in range(1, 6)]
    assert [m['text'] for m in window['messages']] == [f'msg{i}' for i in range(1, 6)]
    assert window['anchor_id'] == messages[3].id
    assert window['chat_id'] == CHAT_ID


async def test_get_window_answer_format_renders_bot_turns_bare():
    await save_message(make_message(chat_id=CHAT_ID, text='question'))
    anchor = make_message(chat_id=CHAT_ID, role=UserRole.AI, text='bot reply', nickname='bot')
    await save_message(anchor)
    await save_message(make_message(chat_id=CHAT_ID, text='follow-up'))

    window = await queries.get_window(str(anchor.id), 'answer')

    rendered = window['rendered']
    assert [turn['role'] for turn in rendered] == ['user', 'assistant', 'user']
    assert rendered[1]['content'] == 'bot reply'
    assert _bodies(rendered[0]['content']) == ['user1: question']


async def test_get_window_zero_before_reads_no_history():
    """A Mongo `limit(0)` means unlimited, so zero must not reach the query."""
    messages = await _seed_chat(5)

    window = await queries.get_window(messages[2].id, 'memory', before=0, after=1)

    assert [m['text'] for m in window['messages']] == ['msg2', 'msg3']


async def test_get_window_clamps_each_side(mocker):
    mocker.patch.object(queries, 'MAX_SIDE', 2)
    messages = await _seed_chat(9)

    window = await queries.get_window(messages[4].id, 'memory', before=50, after=50)

    assert [m['text'] for m in window['messages']] == [f'msg{i}' for i in range(2, 7)]


async def test_get_window_unknown_message_raises():
    with pytest.raises(ValueError):
        await queries.get_window(str(ObjectId()), 'memory')


# --- list_snapshots / get_memory ---

async def test_list_snapshots_newest_first_with_counts():
    await _save_snapshot(T0, {'participants': {'alice': {'traits': ['a'], 'recent': []}}})
    await _save_snapshot(T0 + timedelta(days=1), {'participants': {
        'bob': {'traits': ['b', 'c'], 'recent': ['d']},
        'alice': {'traits': ['a']},
    }})

    snapshots = await queries.list_snapshots(CHAT_ID)

    assert snapshots == [
        {
            'created_at': (T0 + timedelta(days=1)).isoformat(),
            'nicks': ['alice', 'bob'],
            'entry_count': 4,
        },
        {'created_at': T0.isoformat(), 'nicks': ['alice'], 'entry_count': 1},
    ]


async def test_get_memory_picks_the_snapshot_in_force_and_passes_it_through():
    for day in range(3):
        await _save_snapshot(T0 + timedelta(days=day), {'day': day, 'unmodelled': True})

    snapshot = await queries.get_memory(CHAT_ID, at=T0 + timedelta(days=1, hours=1))

    assert snapshot['content'] == {'day': 1, 'unmodelled': True}
    assert snapshot['created_at'] == (T0 + timedelta(days=1)).isoformat()
    assert isinstance(snapshot['_id'], str)


async def test_get_memory_defaults_to_the_newest():
    await _save_snapshot(T0, {'day': 0})
    await _save_snapshot(T0 + timedelta(days=1), {'day': 1})

    snapshot = await queries.get_memory(CHAT_ID)

    assert snapshot['content'] == {'day': 1}


async def test_get_memory_before_any_snapshot_raises():
    await _save_snapshot(T0, {})

    with pytest.raises(ValueError):
        await queries.get_memory(CHAT_ID, at=T0 - timedelta(days=1))


# --- diff_memory ---

OLDER = {
    'created_at': T0.timestamp(),
    'content': {
        'participants': {
            'alice': {
                'traits': ['любит кофе'],
                'recent': ['купила велосипед', 'Сдала экзамен'],
            },
            'bob': {'traits': [], 'recent': ['ёлка дома']},
        },
        'state': {'open_questions': ['кто придёт?'], 'running_jokes': []},
    },
    # No `decay`: a snapshot written before the sidecar existed must still diff.
}

NEWER = {
    'created_at': (T0 + timedelta(days=1)).timestamp(),
    'content': {
        'participants': {
            'alice': {
                'traits': ['Любит кофе!', 'купила велосипед', 'катается на велосипеде'],
                'recent': [],
            },
            'bob': {'traits': [], 'recent': ['Елка дома @alice']},
        },
        'state': {'open_questions': [], 'running_jokes': ['шутка про кофе']},
    },
    'decay': {
        'alice': {
            'катается на велосипеде': {'born': '26-09-02 12:00', 'cycles': 0, 'field': 'traits'},
        },
    },
}


def test_diff_snapshots_classifies_every_entry():
    diff = queries.diff_snapshots(OLDER, NEWER)

    assert diff['births'] == [{
        'nick': 'alice',
        'field': 'traits',
        'text': 'катается на велосипеде',
        'born': '26-09-02 12:00',
        'cycles': 0,
    }]
    assert diff['vanishes'] == [{'nick': 'alice', 'field': 'recent', 'text': 'Сдала экзамен'}]
    assert diff['promotions'] == [
        {'nick': 'alice', 'field': 'traits', 'text': 'купила велосипед'},
    ]
    assert [c['text'] for c in diff['promote_candidates']] == ['катается на велосипеде']
    assert diff['carries'] == 3
    assert diff['per_nick'] == {
        'alice': {'births': 1, 'carries': 2, 'vanishes': 1, 'promotions': 1},
        'bob': {'births': 0, 'carries': 1, 'vanishes': 0, 'promotions': 0},
    }


def test_diff_snapshots_compares_in_the_production_keyspace():
    """Punctuation, case, `ё` and `@nick` differences are one entry, not a vanish and a birth."""
    diff = queries.diff_snapshots(OLDER, NEWER)

    assert [v['text'] for v in diff['vanishes']] == ['Сдала экзамен']
    assert diff['per_nick']['bob']['carries'] == 1


def test_diff_snapshots_reports_state_changes():
    diff = queries.diff_snapshots(OLDER, NEWER)

    assert diff['state']['open_questions'] == {'added': [], 'removed': ['кто придёт?'], 'kept': 0}
    assert diff['state']['running_jokes'] == {'added': ['шутка про кофе'], 'removed': [], 'kept': 0}
    assert diff['state']['active_topics'] == {'added': [], 'removed': [], 'kept': 0}


def test_diff_snapshots_without_a_promotion_has_no_candidates():
    """A trait born on a nick that lost no `recent` entry is not a promotion candidate."""
    older = {'content': {'participants': {'alice': {'traits': ['a'], 'recent': []}}}}
    newer = {'content': {'participants': {'alice': {'traits': ['a', 'b'], 'recent': []}}}}

    diff = queries.diff_snapshots(older, newer)

    assert diff['promote_candidates'] == []
    assert [b['text'] for b in diff['births']] == ['b']


async def test_diff_memory_resolves_both_ends():
    await _save_snapshot(T0, {'participants': {'alice': {'traits': ['a']}}})
    await _save_snapshot(T0 + timedelta(days=1), {'participants': {'alice': {'traits': ['b']}}})
    await _save_snapshot(T0 + timedelta(days=2), {'participants': {'alice': {'traits': ['c']}}})

    diff = await queries.diff_memory(T0 + timedelta(hours=1), T0 + timedelta(days=1), CHAT_ID)

    assert [b['text'] for b in diff['births']] == ['b']
    assert [v['text'] for v in diff['vanishes']] == ['a']
    assert diff['to'] == (T0 + timedelta(days=1)).isoformat()


async def test_diff_memory_defaults_to_the_newest():
    await _save_snapshot(T0, {'participants': {'alice': {'traits': ['a']}}})
    await _save_snapshot(T0 + timedelta(days=2), {'participants': {'alice': {'traits': ['c']}}})

    diff = await queries.diff_memory(T0, chat_id=CHAT_ID)

    assert [b['text'] for b in diff['births']] == ['c']


async def test_diff_memory_before_any_snapshot_raises():
    await _save_snapshot(T0, {})

    with pytest.raises(ValueError):
        await queries.diff_memory(T0 - timedelta(days=1), chat_id=CHAT_ID)


# --- get_user_facts ---

async def test_get_user_facts_without_query_orders_by_confidence(facts_qdrant):
    await mongo.facts.insert_many([
        {'nickname': 'alice', 'text': 'low', 'confidence': 0.5},
        {'nickname': 'alice', 'text': 'high', 'confidence': 0.9},
        {'nickname': 'bob', 'text': 'other', 'confidence': 1.0},
    ])

    facts = await queries.get_user_facts('alice')

    assert facts == [
        {'text': 'high', 'confidence': 0.9},
        {'text': 'low', 'confidence': 0.5},
    ]
    assert facts_qdrant.query_points.call_count == 0


async def test_get_user_facts_accepts_the_memory_form_of_a_nick(facts_qdrant):
    """Memory keys participants as `@nick`; facts are stored bare, since `upsert_fact` strips it."""
    result = await mongo.facts.insert_one({'nickname': 'alice', 'text': 'likes coffee', 'confidence': 0.8})
    facts_qdrant.query_points.return_value = _points((0.9, {'id': str(result.inserted_id)}))

    by_confidence = await queries.get_user_facts('@alice')
    by_query = await queries.get_user_facts('@alice', query='coffee')

    assert by_confidence == [{'text': 'likes coffee', 'confidence': 0.8}]
    assert by_query == [{'text': 'likes coffee', 'confidence': 0.8, 'score': 0.9}]
    assert facts_qdrant.query_points.call_args.kwargs['query_filter'].must[0].match.value == 'alice'


async def test_get_user_facts_with_query_dedupes_duplicated_points(facts_qdrant):
    result = await mongo.facts.insert_many([
        {'nickname': 'alice', 'text': 'likes coffee', 'confidence': 0.8},
        {'nickname': 'alice', 'text': 'owns a bike', 'confidence': 0.6},
    ])
    coffee, bike = (str(i) for i in result.inserted_ids)
    facts_qdrant.query_points.return_value = _points(
        (0.9, {'id': coffee}),
        (0.85, {'id': coffee}),  # a re-embedding under a fresh uuid4
        (0.8, {'id': str(ObjectId())}),  # point outlived its fact
        (0.7, {'id': bike}),
    )

    facts = await queries.get_user_facts('alice', query='coffee')

    assert facts == [
        {'text': 'likes coffee', 'confidence': 0.8, 'score': 0.9},
        {'text': 'owns a bike', 'confidence': 0.6, 'score': 0.7},
    ]
    assert facts_qdrant.query_points.call_args.kwargs['query_filter'].must[0].match.value == 'alice'
    assert facts_qdrant.check_collection.call_count == 0
    assert facts_qdrant.create_collection.call_count == 0
