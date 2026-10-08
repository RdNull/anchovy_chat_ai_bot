from datetime import datetime, timedelta, UTC
from types import SimpleNamespace

import pytest
from bson import ObjectId

from src import mongo, settings
from src.blackbox import queries
from src.embeddings.facts import facts_embedding_client
from src.embeddings.messages import messages_embeddings_client
from src.messages.repository import save_message
from src.messages.models import UserRole
from src.tests.test_utils import make_message

CHAT_ID = 1
T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


async def _seed_chat(count: int) -> list:
    messages = []
    for i in range(count):
        message = make_message(chat_id=CHAT_ID, text=f'msg{i}')
        await save_message(message)
        # `save_message` leaves the raw ObjectId; everything read back carries a str.
        message.id = str(message.id)
        messages.append(message)
    return messages


async def _insert_message(
    chat_id: int,
    created_at: datetime,
    role: UserRole = UserRole.USER,
    nickname: str = 'alice',
    text: str = 'hi',
    reactions: dict | None = None,
) -> str:
    """Inserts a message with an exact `created_at` and, optionally, reactions.

    `save_message` always stamps `created_at` as wall clock and never writes reactions, so
    boundary-time and reaction fixtures go straight to Mongo, the same way `_save_snapshot`
    does for memory.
    """
    result = await mongo.messages.insert_one({
        'chat_id': chat_id,
        'role': role.value,
        'text': text,
        'nickname': nickname,
        'created_at': created_at.timestamp(),
        'reactions': reactions or {},
    })
    return str(result.inserted_id)


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
    """Strips the `[ГГГГ-ММ-ДД ЧЧ:ММ] ` prefix, which is wall clock at save time."""
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

    result = await queries.list_snapshots()

    assert len(result['rows']) == 1


async def test_missing_chat_id_without_a_default_raises(mocker):
    mocker.patch.object(settings, 'BLACKBOX_CHAT_ID', None)

    with pytest.raises(ValueError, match='chat_id was not given'):
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


async def test_find_windows_applies_the_score_floor(messages_qdrant):
    messages_qdrant.query_points.return_value = _points()

    await queries.find_windows('query', chat_id=CHAT_ID)
    default = messages_qdrant.query_points.call_args.kwargs['score_threshold']
    await queries.find_windows('query', chat_id=CHAT_ID, min_score=0.6)
    custom = messages_qdrant.query_points.call_args.kwargs['score_threshold']

    assert (default, custom) == (queries.DEFAULT_MIN_SCORE, 0.6)


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
    with pytest.raises(ValueError, match=r'message .+ not found'):
        await queries.get_window(str(ObjectId()), 'memory')


# --- list_messages ---


async def _say(text: str, role: UserRole = UserRole.USER, nickname: str = 'alice'):
    await save_message(make_message(chat_id=CHAT_ID, role=role, text=text, nickname=nickname))


async def test_list_messages_keeps_the_newest_bot_replies_in_time_order():
    await _say('q1')
    await _say('a1', UserRole.AI, 'bot')
    await _say('q2')
    await _say('a2', UserRole.AI, 'bot')
    await _say('a3', UserRole.AI, 'bot')

    rows = (await queries.list_messages(CHAT_ID, role='bot', limit=2))['rows']

    assert [r['role'] for r in rows] == ['bot', 'bot']
    assert _bodies('\n'.join(r['line'] for r in rows)) == ['bot: a2', 'bot: a3']


async def test_list_messages_oldest_end_by_nick_in_memory_form():
    await _say('first', nickname='alice')
    await _say('other', nickname='bob')
    await _say('second', nickname='alice')

    rows = (await queries.list_messages(CHAT_ID, nick='@alice', limit=1, from_end='oldest'))['rows']

    assert _bodies(rows[0]['line']) == ['alice: first']
    assert rows[0]['role'] == 'user'


async def test_list_messages_time_range():
    await _say('before')
    start = datetime.now(UTC)
    await _say('inside')
    end = datetime.now(UTC)
    await _say('after')

    rows = (await queries.list_messages(CHAT_ID, since=start, until=end))['rows']

    assert _bodies(rows[0]['line']) == ['alice: inside']
    assert len(rows) == 1


async def test_list_messages_clamps_the_limit(mocker):
    mocker.patch.object(queries, 'MAX_MESSAGES', 2)
    for i in range(4):
        await _say(f'm{i}')

    rows = (await queries.list_messages(CHAT_ID, limit=50))['rows']

    assert _bodies('\n'.join(r['line'] for r in rows)) == ['alice: m2', 'alice: m3']


async def test_list_messages_rows_include_reactions_only_when_present():
    with_reaction = await _insert_message(
        CHAT_ID, T0, nickname='alice', text='has reaction', reactions={'🔥': ['bob']}
    )
    without_reaction = await _insert_message(
        CHAT_ID, T0 + timedelta(minutes=1), nickname='alice', text='no reaction'
    )

    rows = (await queries.list_messages(CHAT_ID))['rows']

    by_id = {r['message_id']: r for r in rows}
    assert by_id[with_reaction]['reactions'] == {'🔥': ['bob']}
    assert 'reactions' not in by_id[without_reaction]


async def test_list_messages_totals_ignore_limit_and_rows_stay_chronological():
    for i in range(3):
        await _say(f'm{i}')

    small = await queries.list_messages(CHAT_ID, limit=1)
    full = await queries.list_messages(CHAT_ID, limit=queries.MAX_MESSAGES)

    assert small['totals'] == full['totals']
    assert (small['truncated'], full['truncated']) == (True, False)
    assert _bodies('\n'.join(r['line'] for r in full['rows'])) == [
        'alice: m0',
        'alice: m1',
        'alice: m2',
    ]

    newest = (await queries.list_messages(CHAT_ID, limit=2, from_end='newest'))['rows']
    oldest = (await queries.list_messages(CHAT_ID, limit=2, from_end='oldest'))['rows']
    assert _bodies('\n'.join(r['line'] for r in newest)) == ['alice: m1', 'alice: m2']
    assert _bodies('\n'.join(r['line'] for r in oldest)) == ['alice: m0', 'alice: m1']


async def test_list_messages_totals_matched_equals_rows_at_every_filter_boundary():
    """`since`/`until` are exclusive, so a message exactly at either bound is excluded."""
    since = T0 + timedelta(minutes=5)
    until = T0 + timedelta(minutes=10)
    await _insert_message(CHAT_ID, since, nickname='alice', text='at since')
    await _insert_message(CHAT_ID, since + timedelta(minutes=1), nickname='alice', text='inside')
    await _insert_message(CHAT_ID, until, nickname='alice', text='at until')
    await _insert_message(
        CHAT_ID, since + timedelta(minutes=2), role=UserRole.AI, nickname='test_bot[x]', text='bot'
    )
    await _insert_message(CHAT_ID, since + timedelta(minutes=3), nickname='bob', text='other nick')

    result = await queries.list_messages(
        CHAT_ID, since=since, until=until, role='user', nick='alice', limit=queries.MAX_MESSAGES
    )

    assert result['totals']['matched'] == len(result['rows']) == 1
    assert _bodies(result['rows'][0]['line']) == ['alice: inside']


async def test_list_messages_totals_breakdowns():
    await _insert_message(CHAT_ID, T0, nickname='alice')
    await _insert_message(
        CHAT_ID, T0 + timedelta(minutes=1), nickname='alice', reactions={'🔥': ['bob']}
    )
    await _insert_message(CHAT_ID, T0 + timedelta(minutes=2), role=UserRole.AI, nickname='bot')

    result = await queries.list_messages(CHAT_ID)

    assert result['totals'] == {
        'matched': 3,
        'by_role': {'user': 2, 'bot': 1},
        'by_nick': {'alice': 2, 'bot': 1},
        'with_reactions': 1,
    }


async def test_list_messages_bare_bot_nickname_wildcards_across_characters():
    await _say('a1', UserRole.AI, f'{settings.BOT_NICKNAME}[charA]')
    await _say('a2', UserRole.AI, f'{settings.BOT_NICKNAME}[charB]')
    await _say('q', UserRole.USER, 'alice')

    by_wildcard = await queries.list_messages(CHAT_ID, nick=settings.BOT_NICKNAME)
    by_role = await queries.list_messages(CHAT_ID, role='bot')

    assert by_wildcard == by_role
    assert len(by_wildcard['rows']) == 2


async def test_list_messages_tagged_nick_narrows_to_one_character():
    await _say('a1', UserRole.AI, f'{settings.BOT_NICKNAME}[charA]')
    await _say('a2', UserRole.AI, f'{settings.BOT_NICKNAME}[charB]')

    result = await queries.list_messages(CHAT_ID, nick=f'{settings.BOT_NICKNAME}[charA]')

    assert len(result['rows']) == 1
    assert _bodies(result['rows'][0]['line']) == [f'{settings.BOT_NICKNAME}[charA]: a1']


# --- list_reactions ---


async def test_list_reactions_recognizes_every_bot_reactor_form():
    tagged = 'test_bot[whyzzzy]'
    parenthesized = 'test_bot(Dedka)'
    plain = 'test_bot'
    old_prefix = 'ShizoDedBot'  # a past BOT_NICKNAME value, never matching the current regex

    # `old_prefix` is recognized only because it has spoken as `role=ai`, not by the regex.
    await _insert_message(CHAT_ID, T0, role=UserRole.AI, nickname=old_prefix, text='old reply')
    target = await _insert_message(
        CHAT_ID,
        T0 + timedelta(minutes=1),
        nickname='alice',
        text='hi',
        reactions={'🤣': [tagged, parenthesized, plain, old_prefix], '👍': ['alice']},
    )

    result = await queries.list_reactions(CHAT_ID, reactor='bot')

    assert {r['reactor'] for r in result['rows']} == {tagged, parenthesized, plain, old_prefix}
    assert all(r['reactor_is_bot'] for r in result['rows'])
    assert all(r['message_id'] == target for r in result['rows'])


async def test_list_reactions_on_role_filters_to_bot_messages_with_mixed_reactors():
    await _insert_message(CHAT_ID, T0, nickname='alice', text='q', reactions={'👍': ['bob']})
    bot_message = await _insert_message(
        CHAT_ID,
        T0 + timedelta(minutes=1),
        role=UserRole.AI,
        nickname='test_bot[x]',
        text='a',
        reactions={'🤝': ['alice', 'bob'], '🤣': ['test_bot[x]']},
    )

    result = await queries.list_reactions(CHAT_ID, on_role='bot')

    assert {(r['message_id'], r['emoji'], r['reactor']) for r in result['rows']} == {
        (bot_message, '🤝', 'alice'),
        (bot_message, '🤝', 'bob'),
        (bot_message, '🤣', 'test_bot[x]'),
    }
    reactor_is_bot = {r['reactor']: r['reactor_is_bot'] for r in result['rows']}
    assert reactor_is_bot == {'alice': False, 'bob': False, 'test_bot[x]': True}


async def test_list_reactions_totals_ignore_limit_and_rows_stay_chronological():
    ids = [
        await _insert_message(
            CHAT_ID, T0 + timedelta(minutes=i), text=f'm{i}', reactions={'🔥': ['alice']}
        )
        for i in range(3)
    ]

    small = await queries.list_reactions(CHAT_ID, limit=1)
    full = await queries.list_reactions(CHAT_ID, limit=queries.MAX_MESSAGES)

    assert small['totals'] == full['totals']
    assert (small['truncated'], full['truncated']) == (True, False)
    assert [r['message_id'] for r in full['rows']] == ids

    newest = await queries.list_reactions(CHAT_ID, limit=2, from_end='newest')
    oldest = await queries.list_reactions(CHAT_ID, limit=2, from_end='oldest')
    assert [r['message_id'] for r in newest['rows']] == ids[1:]
    assert [r['message_id'] for r in oldest['rows']] == ids[:2]


async def test_list_reactions_excludes_a_message_just_outside_the_range():
    since = T0
    until = T0 + timedelta(minutes=10)
    await _insert_message(CHAT_ID, since, text='at since', reactions={'🔥': ['alice']})
    inside = await _insert_message(
        CHAT_ID, since + timedelta(minutes=1), text='inside', reactions={'🔥': ['alice']}
    )
    await _insert_message(CHAT_ID, until, text='at until', reactions={'🔥': ['alice']})

    result = await queries.list_reactions(CHAT_ID, since=since, until=until)

    assert [r['message_id'] for r in result['rows']] == [inside]


async def test_list_reactions_totals_match_hand_computed_values():
    await _insert_message(
        CHAT_ID,
        T0,
        nickname='alice',
        reactions={'🔥': ['alice', 'bob'], '🤣': ['test_bot[x]']},
    )
    await _insert_message(
        CHAT_ID, T0 + timedelta(minutes=1), nickname='bob', reactions={'🔥': ['alice']}
    )
    await _insert_message(CHAT_ID, T0 + timedelta(minutes=2), nickname='carol')  # unreacted

    result = await queries.list_reactions(CHAT_ID)

    assert result['totals'] == {
        'matched': 4,
        'by_emoji': {'🔥': 3, '🤣': 1},
        'by_reactor': {'alice': 2, 'bob': 1, 'test_bot[x]': 1},
        'by_reactor_kind': {'bot': 1, 'user': 3},
        'messages_in_range': 3,
        'messages_reacted': 2,
    }


async def test_list_reactions_filters_by_emoji_and_exact_reactor():
    await _insert_message(CHAT_ID, T0, nickname='alice', reactions={'🔥': ['alice'], '🤣': ['bob']})

    by_emoji = await queries.list_reactions(CHAT_ID, emoji='🤣')
    by_reactor = await queries.list_reactions(CHAT_ID, reactor='@alice')

    assert [(r['emoji'], r['reactor']) for r in by_emoji['rows']] == [('🤣', 'bob')]
    assert [(r['emoji'], r['reactor']) for r in by_reactor['rows']] == [('🔥', 'alice')]


async def test_list_reactions_issues_no_write_and_no_out_or_merge(mocker):
    await _insert_message(CHAT_ID, T0, reactions={'🔥': ['alice']})
    spy = mocker.spy(mongo.messages, 'aggregate')
    before = await mongo.messages.find({}).to_list(length=None)

    await queries.list_reactions(CHAT_ID)

    after = await mongo.messages.find({}).to_list(length=None)
    assert before == after
    stages = spy.call_args.args[0]
    assert not any(('$out' in stage or '$merge' in stage) for stage in stages)


async def test_list_reactions_bare_bot_nickname_is_the_same_wildcard_as_bot():
    tagged = f'{settings.BOT_NICKNAME}[x]'
    parenthesized = f'{settings.BOT_NICKNAME}(Old)'
    await _insert_message(
        CHAT_ID,
        T0,
        nickname='alice',
        reactions={'🤣': [tagged, parenthesized, 'alice']},
    )

    by_bare = await queries.list_reactions(CHAT_ID, reactor=settings.BOT_NICKNAME)
    by_keyword = await queries.list_reactions(CHAT_ID, reactor='bot')

    assert by_bare == by_keyword
    assert {r['reactor'] for r in by_bare['rows']} == {tagged, parenthesized}


async def test_list_reactions_tagged_reactor_narrows_to_one_character():
    await _insert_message(
        CHAT_ID,
        T0,
        nickname='alice',
        reactions={'🤣': [f'{settings.BOT_NICKNAME}[x]', f'{settings.BOT_NICKNAME}[y]']},
    )

    result = await queries.list_reactions(CHAT_ID, reactor=f'{settings.BOT_NICKNAME}[x]')

    assert [r['reactor'] for r in result['rows']] == [f'{settings.BOT_NICKNAME}[x]']


# --- list_snapshots / get_memory ---


async def test_list_snapshots_newest_first_with_counts():
    await _save_snapshot(T0, {'participants': {'alice': {'traits': ['a'], 'recent': []}}})
    await _save_snapshot(
        T0 + timedelta(days=1),
        {
            'participants': {
                'bob': {'traits': ['b', 'c'], 'recent': ['d']},
                'alice': {'traits': ['a']},
            }
        },
    )

    result = await queries.list_snapshots(CHAT_ID)

    assert result['rows'] == [
        {
            'created_at': (T0 + timedelta(days=1)).isoformat(),
            'nicks': ['alice', 'bob'],
            'entry_count': 4,
        },
        {'created_at': T0.isoformat(), 'nicks': ['alice'], 'entry_count': 1},
    ]
    assert result['totals'] == {'matched': 2}
    assert result['truncated'] is False


async def test_list_snapshots_totals_matched_ignores_limit():
    await _save_snapshot(T0, {'participants': {}})
    await _save_snapshot(T0 + timedelta(days=1), {'participants': {}})

    result = await queries.list_snapshots(CHAT_ID, limit=1)

    assert result['totals'] == {'matched': 2}
    assert result['truncated'] is True
    assert len(result['rows']) == 1


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


async def test_get_memory_for_one_nick_accepts_either_form():
    record = {'born': '2026-09-01 12:00', 'cycles': 2, 'field': 'traits'}
    await _save_snapshot(
        T0,
        {'participants': {'@alice': {'traits': ['a'], 'recent': ['r']}, '@bob': {'traits': ['b']}}},
        decay={'@alice': {'a': record}, '@bob': {}},
    )

    bare = await queries.get_memory(CHAT_ID, nick='alice')
    cased = await queries.get_memory(CHAT_ID, nick='@Alice')

    assert bare == cased
    assert bare['nick'] == '@alice'
    assert bare['participant'] == {'traits': ['a'], 'recent': ['r']}
    assert bare['decay'] == {'a': record}
    assert bare['created_at'] == T0.isoformat()


async def test_get_memory_unknown_nick_lists_the_participants():
    await _save_snapshot(T0, {'participants': {'@alice': {}, '@bob': {}}})

    with pytest.raises(ValueError, match="'@alice', '@bob'"):
        await queries.get_memory(CHAT_ID, nick='carol')


async def test_get_memory_before_any_snapshot_raises():
    await _save_snapshot(T0, {})

    with pytest.raises(ValueError, match='no memory snapshot for chat'):
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
            'катается на велосипеде': {'born': '2026-09-02 12:00', 'cycles': 0, 'field': 'traits'},
        },
    },
}


def test_diff_snapshots_classifies_every_entry():
    diff = queries.diff_snapshots(OLDER, NEWER)

    assert diff['births'] == [
        {
            'nick': 'alice',
            'field': 'traits',
            'text': 'катается на велосипеде',
            'born': '2026-09-02 12:00',
            'cycles': 0,
        }
    ]
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


async def test_diff_memory_counts_the_snapshots_it_skipped():
    for day in range(3):
        await _save_snapshot(T0 + timedelta(days=day), {'participants': {}})

    spanning = await queries.diff_memory(T0, chat_id=CHAT_ID)
    adjacent = await queries.diff_memory(T0 + timedelta(days=1), chat_id=CHAT_ID)

    assert (spanning['snapshots_between'], adjacent['snapshots_between']) == (1, 0)


async def test_diff_memory_reversed_bounds_raise():
    await _save_snapshot(T0, {})
    await _save_snapshot(T0 + timedelta(days=1), {})

    with pytest.raises(ValueError, match='later snapshot'):
        await queries.diff_memory(T0 + timedelta(days=1), T0, CHAT_ID)


async def test_diff_memory_before_any_snapshot_raises():
    await _save_snapshot(T0, {})

    with pytest.raises(ValueError, match='no memory snapshot for chat'):
        await queries.diff_memory(T0 - timedelta(days=1), chat_id=CHAT_ID)


# --- get_user_facts ---


async def _insert_fact(nickname='alice', text='x', status='confirmed', kind='habit', days_ago=0):
    seen = T0 - timedelta(days=days_ago)
    result = await mongo.facts.insert_one({
        'nickname': nickname,
        'kind': kind,
        'status': status,
        'text': text,
        'sightings': ['2026-09-01', '2026-09-02'],
        'created_at': seen,
        'last_seen_at': seen,
    })
    return str(result.inserted_id)


def _view(text, status='confirmed', kind='habit', days_ago=0):
    return {
        'text': text,
        'kind': kind,
        'status': status,
        'sightings': ['2026-09-01', '2026-09-02'],
        'last_seen_at': (T0 - timedelta(days=days_ago)).replace(tzinfo=None).isoformat(),
    }


async def test_get_user_facts_without_query_lists_confirmed_first_then_recent(facts_qdrant):
    await _insert_fact(text='old confirmed', days_ago=9)
    await _insert_fact(text='new candidate', status='candidate', days_ago=0)
    await _insert_fact(text='new confirmed', days_ago=1)
    await _insert_fact(nickname='bob', text='other')

    result = await queries.get_user_facts('alice')

    assert result['rows'] == [
        _view('new confirmed', days_ago=1),
        _view('old confirmed', days_ago=9),
        _view('new candidate', status='candidate'),
    ]
    assert result['totals'] == {'matched': 3}
    assert facts_qdrant.query_points.call_count == 0


async def test_get_user_facts_accepts_the_memory_form_of_a_nick(facts_qdrant):
    """Memory keys participants as `@nick`; facts are stored bare, since `apply_op` strips it."""
    fact_id = await _insert_fact(text='likes coffee')
    facts_qdrant.query_points.return_value = _points((0.9, {'id': fact_id}))

    by_listing = await queries.get_user_facts('@alice')
    by_query = await queries.get_user_facts('@alice', query='coffee')

    assert by_listing['rows'] == [_view('likes coffee')]
    assert by_query['rows'] == [{**_view('likes coffee'), 'score': 0.9}]
    assert facts_qdrant.query_points.call_args.kwargs['query_filter'].must[0].match.value == 'alice'


async def test_get_user_facts_totals_matched_is_population_size_in_both_branches(facts_qdrant):
    """With `query`, `matched` is the population searched, not the number of hits returned."""
    await _insert_fact(text='likes coffee')
    await _insert_fact(text='owns a bike')
    facts_qdrant.query_points.return_value = _points()

    by_listing = await queries.get_user_facts('alice', limit=1)
    by_query = await queries.get_user_facts('alice', query='coffee', limit=1)

    assert by_listing['totals'] == {'matched': 2}
    assert by_query['totals'] == {'matched': 2}


async def test_get_user_facts_with_query_dedupes_duplicated_points(facts_qdrant):
    coffee = await _insert_fact(text='likes coffee')
    bike = await _insert_fact(text='owns a bike')
    facts_qdrant.query_points.return_value = _points(
        (0.9, {'id': coffee}),
        (0.85, {'id': coffee}),  # a legacy re-embedding under a fresh uuid4
        (0.8, {'id': str(ObjectId())}),  # point outlived its fact
        (0.7, {'id': bike}),
    )

    facts = await queries.get_user_facts('alice', query='coffee')

    assert facts['rows'] == [
        {**_view('likes coffee'), 'score': 0.9},
        {**_view('owns a bike'), 'score': 0.7},
    ]
    assert facts_qdrant.query_points.call_args.kwargs['query_filter'].must[0].match.value == 'alice'
    assert facts_qdrant.check_collection.call_count == 0
    assert facts_qdrant.create_collection.call_count == 0
