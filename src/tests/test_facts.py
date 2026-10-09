from datetime import date, datetime, timedelta, UTC
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

from src import mongo, settings
from src.embeddings.facts import FactsSearchResult
from src.facts.handlers import (
    apply_op,
    enforce_caps,
    facts_for_reply,
    format_facts,
    is_bot_nickname,
    load_existing,
    select_fact_users,
    update_user_facts,
    window_participants,
)
from freezegun import freeze_time

from src.facts.models import FactKind, FactOp, FactOpType, FactOps, FactStatus, UserFact
from src.facts.processors import extract_facts, number_facts, render_existing_facts
from src.facts.repository import (
    add_sighting,
    create_fact,
    delete_fact,
    find_expired_candidates,
    get_fact_by_id,
    get_facts,
    list_overflow,
    replace_fact,
)
from src.messages.models import MessageReply, UserRole
from src.tests.test_utils import make_message

DAY1 = date(2026, 10, 1)
DAY2 = date(2026, 10, 2)
DAY3 = date(2026, 10, 3)


def make_op(**overrides):
    fields = {
        'reason': 'r',
        'op': FactOpType.ADD,
        'target': None,
        'nickname': '@alice',
        'kind': FactKind.HABIT,
        'text': 'играет в CS',
        'self_stated': False,
    }
    return FactOp(**{**fields, **overrides})


def mock_facts_llm(mocker, ops=None):
    mock_llm = MagicMock()
    mock_ainvoke = AsyncMock(return_value=FactOps(ops=ops or []))
    mock_llm.__or__.return_value.with_retry.return_value.ainvoke = mock_ainvoke
    mocker.patch('src.facts.processors.ai.get_facts_model', return_value=mock_llm)
    mocker.patch('src.facts.processors.prompt_manager.get_prompt', return_value='p')
    return mock_ainvoke


async def insert_fact(
    nickname='alice',
    kind=FactKind.HABIT,
    status=FactStatus.CANDIDATE,
    text='играет в CS',
    sightings=(DAY1,),
    last_seen_at=None,
):
    now = last_seen_at or datetime.now(UTC)
    result = await mongo.facts.insert_one({
        'nickname': nickname,
        'kind': kind.value,
        'status': status.value,
        'text': text,
        'sightings': [d.isoformat() for d in sightings],
        'created_at': now,
        'last_seen_at': now,
    })
    return await get_fact_by_id(str(result.inserted_id))


@pytest.fixture(autouse=True)
def embeddings(mocker):
    client = mocker.patch('src.facts.handlers.facts_embedding_client')
    client.search_facts = AsyncMock(return_value=[])
    client.save_fact = AsyncMock()
    client.delete_fact = AsyncMock()
    return client


# --- repository ---


async def test_create_fact_persists_fields():
    fact = await create_fact('alice', FactKind.BIO, 'живёт в Алматы', FactStatus.CANDIDATE, DAY1)

    assert fact.id is not None
    assert fact.nickname == 'alice'
    assert fact.kind == FactKind.BIO
    assert fact.status == FactStatus.CANDIDATE
    assert fact.text == 'живёт в Алматы'
    assert fact.sightings == [DAY1]
    assert isinstance(fact.created_at, datetime)
    assert isinstance(fact.last_seen_at, datetime)
    assert isinstance(fact, UserFact)


async def test_get_facts_orders_confirmed_first_then_most_recent():
    now = datetime.now(UTC)
    await insert_fact(text='old candidate', last_seen_at=now - timedelta(days=3))
    await insert_fact(text='new candidate', last_seen_at=now)
    await insert_fact(
        text='old confirmed', status=FactStatus.CONFIRMED, last_seen_at=now - timedelta(days=9)
    )
    await insert_fact(
        text='new confirmed', status=FactStatus.CONFIRMED, last_seen_at=now - timedelta(days=1)
    )

    facts = await get_facts('alice')

    assert [f.text for f in facts] == [
        'new confirmed',
        'old confirmed',
        'new candidate',
        'old candidate',
    ]


async def test_get_facts_filters_by_status_and_limit():
    await insert_fact(text='a', status=FactStatus.CONFIRMED)
    await insert_fact(text='b', status=FactStatus.CONFIRMED)
    await insert_fact(text='c')

    confirmed = await get_facts('alice', status=FactStatus.CONFIRMED)
    limited = await get_facts('alice', limit=1)

    assert {f.text for f in confirmed} == {'a', 'b'}
    assert len(limited) == 1


async def test_get_facts_isolates_by_nickname_and_unknown_is_empty():
    await insert_fact(nickname='alice')
    await insert_fact(nickname='bob')

    assert [f.nickname for f in await get_facts('alice')] == ['alice']
    assert await get_facts('nobody') == []


async def test_get_fact_by_id_returns_fact_or_none():
    fact = await insert_fact()

    assert (await get_fact_by_id(fact.id)).text == fact.text
    assert await get_fact_by_id(str(ObjectId())) is None


async def test_add_sighting_dedupes_days_and_stamps_last_seen():
    fact = await insert_fact(last_seen_at=datetime.now(UTC) - timedelta(days=5))

    same_day = await add_sighting(fact.id, DAY1)
    next_day = await add_sighting(fact.id, DAY2)

    assert same_day.sightings == [DAY1]
    assert next_day.sightings == [DAY1, DAY2]
    assert next_day.last_seen_at > fact.last_seen_at


async def test_replace_fact_overwrites_and_resets_sightings():
    fact = await insert_fact(sightings=(DAY1, DAY2), status=FactStatus.CONFIRMED)

    updated = await replace_fact(fact.id, FactKind.BIO, 'новый текст', FactStatus.CANDIDATE, DAY3)

    assert updated.text == 'новый текст'
    assert updated.kind == FactKind.BIO
    assert updated.status == FactStatus.CANDIDATE
    assert updated.sightings == [DAY3]


async def test_delete_fact_removes_row():
    fact = await insert_fact()

    await delete_fact(fact.id)

    assert await get_fact_by_id(fact.id) is None


async def test_list_overflow_returns_least_recently_seen_beyond_keep():
    now = datetime.now(UTC)
    for i in range(4):
        await insert_fact(text=f'fact {i}', last_seen_at=now - timedelta(days=i))
    await insert_fact(
        text='confirmed', status=FactStatus.CONFIRMED, last_seen_at=now - timedelta(days=30)
    )

    overflow = await list_overflow('alice', FactStatus.CANDIDATE, keep=2)

    assert {f.text for f in overflow} == {'fact 2', 'fact 3'}


async def test_find_expired_candidates_only_returns_old_candidates():
    old = datetime.now(UTC) - timedelta(days=40)
    await insert_fact(text='old candidate', last_seen_at=old)
    await insert_fact(text='old confirmed', status=FactStatus.CONFIRMED, last_seen_at=old)
    await insert_fact(text='fresh candidate')

    expired = await find_expired_candidates(datetime.now(UTC) - timedelta(days=30))

    assert [f.text for f in expired] == ['old candidate']


async def test_ensure_indexes_creates_nickname_status_index():
    await mongo.ensure_indexes()

    indexes = await mongo.facts.index_information()
    keys = [tuple(i['key']) for i in indexes.values()]
    assert (('nickname', 1), ('status', 1)) in keys


def test_user_fact_has_no_confidence():
    assert 'confidence' not in UserFact.model_fields


# --- processor ---


async def test_number_facts_numbers_across_nicknames():
    a1 = UserFact(_id='1', nickname='a', kind='bio', status='confirmed', text='x')
    a2 = UserFact(_id='2', nickname='a', kind='habit', status='candidate', text='y')
    b1 = UserFact(_id='3', nickname='b', kind='joke', status='confirmed', text='z')

    numbered = number_facts({'a': [a1, a2], 'b': [b1]})

    assert numbered == {'f1': a1, 'f2': a2, 'f3': b1}


def test_render_existing_facts_groups_by_nick_without_status():
    a1 = UserFact(_id='1', nickname='a', kind='bio', status='confirmed', text='живёт в Алматы')
    b1 = UserFact(_id='3', nickname='b', kind='joke', status='candidate', text='вечно опаздывает')

    rendered = render_existing_facts({'a': [a1], 'empty': [], 'b': [b1]})

    assert rendered == '@a:\nf1 [bio] живёт в Алматы\n@b:\nf2 [joke] вечно опаздывает'


def test_render_existing_facts_empty_block_has_placeholder():
    assert render_existing_facts({'a': []}) == '(пока нет)'


async def test_extract_facts_returns_ops_and_renders_prompt(mocker):
    ops = [make_op(), make_op(nickname='@bob')]
    mock_facts_llm(mocker, ops=ops)
    get_prompt = mocker.patch('src.facts.processors.prompt_manager.get_prompt', return_value='p')
    fact = UserFact(
        _id='1', nickname='alice', kind='bio', status='confirmed', text='живёт в Алматы'
    )

    result = await extract_facts([make_message(nickname='alice', text='привет')], {'alice': [fact]})

    assert result == ops
    kwargs = get_prompt.call_args.kwargs
    assert get_prompt.call_args.args == ('facts',)
    assert kwargs['version'] == 'v3'
    assert kwargs['facts'] == '@alice:\nf1 [bio] живёт в Алматы'
    assert kwargs['messages'] == 'alice: привет'
    assert kwargs['bot_nickname'] == settings.BOT_NICKNAME


async def test_extract_facts_uses_v3_model(mocker):
    mock_facts_llm(mocker)
    get_model = mocker.patch('src.facts.processors.ai.get_facts_model')
    get_model.return_value.__or__.return_value.with_retry.return_value.ainvoke = AsyncMock(
        return_value=FactOps(ops=[])
    )

    await extract_facts([make_message()], {})

    assert get_model.call_args.kwargs == {'version': 'v3'}


async def test_extract_facts_input_shows_bot_lines_by_tagged_nickname(mocker):
    mock_facts_llm(mocker)
    get_prompt = mocker.patch('src.facts.processors.prompt_manager.get_prompt', return_value='p')
    bot_line = make_message(
        role=UserRole.AI, text='реплика', nickname=f'{settings.BOT_NICKNAME}[whyzzzy]'
    )

    await extract_facts([bot_line], {})

    assert get_prompt.call_args.kwargs['messages'] == f'{settings.BOT_NICKNAME}[whyzzzy]: реплика'


async def test_extract_facts_raises_on_llm_failure(mocker):
    mock_llm = MagicMock()
    mock_llm.__or__.return_value.with_retry.return_value.ainvoke = AsyncMock(
        side_effect=Exception('LLM failure')
    )
    mocker.patch('src.facts.processors.ai.get_facts_model', return_value=mock_llm)
    mocker.patch('src.facts.processors.prompt_manager.get_prompt', return_value='p')

    with pytest.raises(Exception, match='LLM failure'):
        await extract_facts([make_message()], {})


def test_v3_prompt_renders_with_real_template():
    from src.prompt_manager import prompt_manager

    rendered = prompt_manager.get_prompt(
        'facts', version='v3', facts='@a:\nf1 [bio] x', messages='m', bot_nickname='BotName'
    )

    assert 'Бот (BotName)' in rendered
    assert '@a:\nf1 [bio] x' in rendered
    assert '{{' not in rendered


# --- participants ---


def test_is_bot_nickname_covers_bare_and_tagged_forms():
    assert is_bot_nickname(settings.BOT_NICKNAME)
    assert is_bot_nickname(f'{settings.BOT_NICKNAME}[whyzzzy]')
    assert not is_bot_nickname('alice')
    assert not is_bot_nickname(f'{settings.BOT_NICKNAME}x')


def test_window_participants_collects_authors_replies_mentions_without_bot():
    messages = [
        make_message(nickname='alice', text='привет @carol'),
        make_message(
            nickname='bob',
            text='ок',
            role=UserRole.USER,
        ),
        make_message(nickname=f'{settings.BOT_NICKNAME}[cat]', role=UserRole.AI, text='мяу @dave'),
    ]
    messages[1].reply = MessageReply(nickname='erin', text='x')

    participants = window_participants(messages)

    assert participants == ['alice', 'bob', 'carol', 'dave', 'erin']


def test_window_participants_drops_bot_mentions_and_replies():
    message = make_message(nickname='alice', text=f'@{settings.BOT_NICKNAME} эй')
    message.reply = MessageReply(nickname=f'{settings.BOT_NICKNAME}[cat]', text='x')

    assert window_participants([message]) == ['alice']


# --- load_existing ---


async def test_load_existing_returns_confirmed_plus_nearest_candidates(embeddings):
    confirmed = await insert_fact(text='confirmed', status=FactStatus.CONFIRMED)
    candidate = await insert_fact(text='candidate')
    embeddings.search_facts.return_value = [FactsSearchResult(candidate, 0.4)]

    existing = await load_existing([make_message(nickname='alice', text='играю в кс')])

    assert existing == {'alice': [confirmed, candidate]}
    kwargs = embeddings.search_facts.call_args.kwargs
    assert embeddings.search_facts.call_args.args == ('alice', 'играю в кс')
    assert kwargs['status'] == FactStatus.CANDIDATE
    assert kwargs['limit'] == settings.FACTS_PROMPT_CANDIDATES


async def test_load_existing_skips_candidate_search_without_own_messages(embeddings):
    await insert_fact(nickname='carol', text='confirmed', status=FactStatus.CONFIRMED)

    existing = await load_existing([make_message(nickname='alice', text='@carol привет')])

    assert [f.text for f in existing['carol']] == ['confirmed']
    searched = [c.args[0] for c in embeddings.search_facts.call_args_list]
    assert searched == ['alice']


# --- apply_op ---


async def test_add_creates_candidate(embeddings):
    nick = await apply_op(make_op(), {}, DAY1)

    facts = await get_facts('alice')
    assert nick == 'alice'
    assert len(facts) == 1
    assert facts[0].status == FactStatus.CANDIDATE
    assert facts[0].sightings == [DAY1]
    assert embeddings.save_fact.call_args.args[0].id == facts[0].id


async def test_add_self_stated_bio_is_confirmed():
    op = make_op(kind=FactKind.BIO, self_stated=True, text='живёт в Алматы')

    await apply_op(op, {}, DAY1)

    facts = await get_facts('alice')
    assert facts[0].status == FactStatus.CONFIRMED


async def test_add_self_stated_habit_stays_candidate():
    await apply_op(make_op(self_stated=True), {}, DAY1)

    assert (await get_facts('alice'))[0].status == FactStatus.CANDIDATE


async def test_add_strips_at_prefix():
    await apply_op(make_op(nickname='@alice'), {}, DAY1)

    assert len(await get_facts('alice')) == 1


async def test_add_vector_match_becomes_confirm(embeddings, mocker):
    fact = await insert_fact(sightings=(DAY1,))
    embeddings.search_facts.return_value = [FactsSearchResult(fact, 0.8)]
    logger = mocker.patch('src.facts.handlers.logger')

    await apply_op(make_op(text='катает в кс'), {}, DAY2)

    facts = await get_facts('alice')
    assert len(facts) == 1
    assert facts[0].sightings == [DAY1, DAY2]
    assert facts[0].status == FactStatus.CONFIRMED
    assert embeddings.search_facts.call_args.kwargs['score_threshold'] == 0.6
    extra = logger.info.call_args.kwargs['extra']
    assert extra['fallback'] == 'vector'
    assert extra['outcome'] == 'promoted'


async def test_confirm_same_day_does_not_add_sighting():
    fact = await insert_fact(sightings=(DAY1,))

    await apply_op(make_op(op=FactOpType.CONFIRM, target='f1'), {'f1': fact}, DAY1)

    stored = await get_fact_by_id(fact.id)
    assert stored.sightings == [DAY1]
    assert stored.status == FactStatus.CANDIDATE


async def test_confirm_on_second_day_promotes_habit_but_not_joke(embeddings):
    habit = await insert_fact(text='habit', sightings=(DAY1,))
    joke = await insert_fact(text='joke', kind=FactKind.JOKE, sightings=(DAY1,))

    await apply_op(make_op(op=FactOpType.CONFIRM, target='f1'), {'f1': habit}, DAY2)
    await apply_op(
        make_op(op=FactOpType.CONFIRM, target='f1', kind=FactKind.JOKE), {'f1': joke}, DAY2
    )

    assert (await get_fact_by_id(habit.id)).status == FactStatus.CONFIRMED
    assert (await get_fact_by_id(joke.id)).status == FactStatus.CANDIDATE
    saved = [c.args[0] for c in embeddings.save_fact.call_args_list]
    assert [f.id for f in saved] == [habit.id]
    assert saved[0].status == FactStatus.CONFIRMED


async def test_confirm_joke_promotes_on_third_day():
    joke = await insert_fact(kind=FactKind.JOKE, sightings=(DAY1, DAY2))

    await apply_op(
        make_op(op=FactOpType.CONFIRM, target='f1', kind=FactKind.JOKE), {'f1': joke}, DAY3
    )

    assert (await get_fact_by_id(joke.id)).status == FactStatus.CONFIRMED


async def test_confirm_self_stated_bio_promotes_immediately():
    fact = await insert_fact(kind=FactKind.BIO, sightings=(DAY1,))
    op = make_op(op=FactOpType.CONFIRM, target='f1', kind=FactKind.BIO, self_stated=True)

    await apply_op(op, {'f1': fact}, DAY1)

    assert (await get_fact_by_id(fact.id)).status == FactStatus.CONFIRMED


async def test_replace_resets_sightings_and_reembeds(embeddings):
    fact = await insert_fact(
        kind=FactKind.BIO,
        status=FactStatus.CONFIRMED,
        text='живёт в Алматы',
        sightings=(DAY1, DAY2),
    )
    op = make_op(op=FactOpType.REPLACE, target='f1', kind=FactKind.BIO, text='живёт в Астане')

    await apply_op(op, {'f1': fact}, DAY3)

    stored = await get_fact_by_id(fact.id)
    assert stored.text == 'живёт в Астане'
    assert stored.sightings == [DAY3]
    assert stored.status == FactStatus.CANDIDATE
    saved = embeddings.save_fact.call_args.args[0]
    assert saved.id == fact.id
    assert saved.text == 'живёт в Астане'


async def test_replace_self_stated_bio_stays_confirmed():
    fact = await insert_fact(kind=FactKind.BIO, status=FactStatus.CONFIRMED)
    op = make_op(
        op=FactOpType.REPLACE, target='f1', kind=FactKind.BIO, self_stated=True, text='новое'
    )

    await apply_op(op, {'f1': fact}, DAY2)

    assert (await get_fact_by_id(fact.id)).status == FactStatus.CONFIRMED


@pytest.mark.parametrize('operation', [FactOpType.CONFIRM, FactOpType.REPLACE])
async def test_unknown_target_is_dropped_and_logged(operation, mocker, embeddings):
    logger = mocker.patch('src.facts.handlers.logger')

    nick = await apply_op(make_op(op=operation, target='f9'), {}, DAY1)

    assert nick is None
    assert await get_facts('alice') == []
    assert embeddings.save_fact.call_count == 0
    extra = logger.info.call_args.kwargs['extra']
    assert extra['event'] == 'FACT_OP'
    assert extra['outcome'] == 'invalid_target'


async def test_target_belonging_to_another_nickname_is_dropped(mocker):
    bobs = await insert_fact(nickname='bob', sightings=(DAY1,))
    logger = mocker.patch('src.facts.handlers.logger')

    nick = await apply_op(
        make_op(op=FactOpType.CONFIRM, target='f1', nickname='@alice'), {'f1': bobs}, DAY2
    )

    assert nick is None
    assert (await get_fact_by_id(bobs.id)).sightings == [DAY1]
    assert logger.info.call_args.kwargs['extra']['outcome'] == 'invalid_target'


async def test_confirm_without_target_is_invalid(mocker):
    logger = mocker.patch('src.facts.handlers.logger')

    nick = await apply_op(make_op(op=FactOpType.CONFIRM, target=None), {}, DAY1)

    assert nick is None
    assert logger.info.call_args.kwargs['extra']['outcome'] == 'invalid_target'


@pytest.mark.parametrize('bot', [settings.BOT_NICKNAME, f'{settings.BOT_NICKNAME}[cat]'])
async def test_bot_ops_are_dropped(bot, mocker, embeddings):
    logger = mocker.patch('src.facts.handlers.logger')

    nick = await apply_op(make_op(nickname=f'@{bot}'), {}, DAY1)

    assert nick is None
    assert await mongo.facts.count_documents({}) == 0
    assert embeddings.search_facts.call_count == 0
    assert logger.info.call_args.kwargs['extra']['outcome'] == 'bot_dropped'


async def test_fact_op_event_carries_all_fields(mocker):
    logger = mocker.patch('src.facts.handlers.logger')

    await apply_op(make_op(reason='потому что'), {}, DAY1)

    extra = logger.info.call_args.kwargs['extra']
    assert extra == {
        'event': 'FACT_OP',
        'op': 'add',
        'kind': 'habit',
        'nickname': 'alice',
        'outcome': 'created',
        'reason': 'потому что',
        'fallback': None,
    }


# --- caps ---


async def test_caps_evict_least_recently_seen_and_delete_points(monkeypatch, embeddings):
    monkeypatch.setattr(settings, 'FACTS_CONFIRMED_CAP', 2)
    monkeypatch.setattr(settings, 'FACTS_CANDIDATE_CAP', 1)
    now = datetime.now(UTC)
    kept_confirmed = [
        await insert_fact(
            text=f'c{i}', status=FactStatus.CONFIRMED, last_seen_at=now - timedelta(days=i)
        )
        for i in range(2)
    ]
    evicted_confirmed = await insert_fact(
        text='c-old', status=FactStatus.CONFIRMED, last_seen_at=now - timedelta(days=9)
    )
    kept_candidate = await insert_fact(text='n0', last_seen_at=now)
    evicted_candidate = await insert_fact(text='n-old', last_seen_at=now - timedelta(days=5))
    other = await insert_fact(
        nickname='bob', text='untouched', last_seen_at=now - timedelta(days=99)
    )

    await enforce_caps({'alice'})

    remaining = {f.id for f in await get_facts('alice')}
    assert remaining == {f.id for f in kept_confirmed} | {kept_candidate.id}
    assert await get_fact_by_id(other.id) is not None
    deleted = {c.args[0] for c in embeddings.delete_fact.call_args_list}
    assert deleted == {evicted_confirmed.id, evicted_candidate.id}


# --- update_user_facts (handler) ---


@freeze_time('2026-10-02 12:00')
async def test_update_user_facts_applies_ops_and_logs_run(mocker, embeddings):
    existing = await insert_fact(nickname='alice', sightings=(DAY1,), text='играет в CS')
    mock_facts_llm(
        mocker,
        ops=[
            make_op(op=FactOpType.ADD, nickname='@bob', text='любит кофе'),
            make_op(op=FactOpType.CONFIRM, target='f1', nickname='@alice'),
        ],
    )
    logger = mocker.patch('src.facts.handlers.logger')
    messages = [
        make_message(nickname='alice', text='кс?'),
        make_message(nickname='bob', text='кофе'),
    ]

    async def search(nickname, text, **kwargs):
        is_prompt_lookup = kwargs.get('status') == FactStatus.CANDIDATE
        return [FactsSearchResult(existing, 0.5)] if is_prompt_lookup else []

    embeddings.search_facts.side_effect = search

    await update_user_facts(messages)

    assert (await get_fact_by_id(existing.id)).sightings == [DAY1, DAY2]
    assert [f.text for f in await get_facts('bob')] == ['любит кофе']
    run_event = logger.info.call_args.kwargs['extra']
    assert run_event['event'] == 'FACT_EXTRACT'
    assert run_event['outcome'] == 'ok'
    assert run_event['count'] == 2


async def test_update_user_facts_passes_existing_to_extractor(mocker):
    confirmed = await insert_fact(nickname='alice', status=FactStatus.CONFIRMED)
    extract = mocker.patch('src.facts.handlers.extract_facts', AsyncMock(return_value=[]))
    messages = [make_message(nickname='alice', text='привет')]

    await update_user_facts(messages)

    assert extract.call_args.args == (messages, {'alice': [confirmed]})


async def test_update_user_facts_empty_result_writes_nothing(mocker):
    mock_facts_llm(mocker, ops=[])

    await update_user_facts([make_message()])

    assert await mongo.facts.count_documents({}) == 0


async def test_update_user_facts_logs_error_on_exception(mocker):
    mock_llm = MagicMock()
    mock_llm.__or__.return_value.with_retry.return_value.ainvoke = AsyncMock(
        side_effect=Exception('LLM failure')
    )
    mocker.patch('src.facts.processors.ai.get_facts_model', return_value=mock_llm)
    mocker.patch('src.facts.processors.prompt_manager.get_prompt', return_value='p')
    mock_logger = mocker.patch('src.facts.handlers.logger')

    await update_user_facts([make_message()])

    assert mock_logger.error.call_count == 1
    assert 'Error extracting facts from messages' in mock_logger.error.call_args[0][0]


# --- facts_for_reply ---


def test_select_fact_users_orders_author_reply_to_then_mentions():
    target = make_message(nickname='alice', text='эй @carol и @bob')
    target.reply = MessageReply(nickname='bob', text='x')

    assert select_fact_users(target, []) == ['alice', 'bob', 'carol']


def test_select_fact_users_excludes_bot_in_both_forms():
    target = make_message(
        nickname='alice', text=f'@{settings.BOT_NICKNAME} @{settings.BOT_NICKNAME}[cat]'
    )
    target.reply = MessageReply(nickname=f'{settings.BOT_NICKNAME}[cat]', text='x')

    assert select_fact_users(target, []) == ['alice']


def test_select_fact_users_without_target_takes_recent_user_authors_newest_first():
    messages = [
        make_message(nickname='alice'),
        make_message(nickname='bob'),
        make_message(nickname=f'{settings.BOT_NICKNAME}[cat]', role=UserRole.AI),
        make_message(nickname='alice'),
        make_message(nickname='carol'),
    ]

    assert select_fact_users(None, messages) == ['carol', 'alice', 'bob']


async def test_facts_for_reply_returns_only_confirmed_facts():
    await insert_fact('alice', text='живёт в Алматы', status=FactStatus.CONFIRMED)
    await insert_fact('alice', text='может быть врёт', status=FactStatus.CANDIDATE)

    result = await facts_for_reply(make_message(nickname='alice'), [])

    assert [f.text for f in result['alice']] == ['живёт в Алматы']


async def test_facts_for_reply_skips_people_without_facts_and_does_not_spend_the_cap(mocker):
    mocker.patch.object(settings, 'FACTS_INJECT_MAX_USERS', 1)
    await insert_fact('carol', status=FactStatus.CONFIRMED)
    target = make_message(nickname='alice', text='@bob @carol')

    result = await facts_for_reply(target, [])

    assert list(result) == ['carol']


async def test_facts_for_reply_respects_the_cap(mocker):
    mocker.patch.object(settings, 'FACTS_INJECT_MAX_USERS', 2)
    for nickname in ('alice', 'bob', 'carol'):
        await insert_fact(nickname, status=FactStatus.CONFIRMED)
    target = make_message(nickname='alice', text='@bob @carol')

    result = await facts_for_reply(target, [])

    assert list(result) == ['alice', 'bob']


async def test_facts_for_reply_orders_by_kind_then_last_seen_desc():
    old = datetime.now(UTC) - timedelta(days=5)
    new = datetime.now(UTC)
    confirmed = FactStatus.CONFIRMED
    await insert_fact('alice', FactKind.JOKE, confirmed, 'шутка', last_seen_at=new)
    await insert_fact('alice', FactKind.HABIT, confirmed, 'привычка старая', last_seen_at=old)
    await insert_fact('alice', FactKind.BIO, confirmed, 'био', last_seen_at=old)
    await insert_fact('alice', FactKind.HABIT, confirmed, 'привычка новая', last_seen_at=new)

    result = await facts_for_reply(make_message(nickname='alice'), [])

    texts = [f.text for f in result['alice']]
    assert texts == ['био', 'привычка новая', 'привычка старая', 'шутка']


async def test_facts_for_reply_empty_when_nobody_has_confirmed_facts():
    await insert_fact('alice', status=FactStatus.CANDIDATE)

    assert await facts_for_reply(make_message(nickname='alice'), []) == {}


def test_format_facts_one_line_per_person_with_joke_prefix():
    facts = {
        'alice': [
            UserFact(
                nickname='alice',
                kind=FactKind.BIO,
                status=FactStatus.CONFIRMED,
                text='живёт в Алматы',
            ),
            UserFact(
                nickname='alice',
                kind=FactKind.JOKE,
                status=FactStatus.CONFIRMED,
                text='встречается с bob',
            ),
        ],
        'bob': [
            UserFact(
                nickname='bob', kind=FactKind.HABIT, status=FactStatus.CONFIRMED, text='бегает'
            )
        ],
    }

    assert format_facts(facts) == ('@alice: живёт в Алматы; шутка: встречается с bob\n@bob: бегает')


def test_format_facts_is_none_when_empty():
    assert format_facts({}) is None
    assert format_facts(None) is None
