import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, call

from telegram.constants import ChatAction

from src import settings
from src.initiative import handlers
from src.initiative.models import InitiativeVerdict
from src.initiative.repository import get_last_initiative_run
from src.messages.repository import save_message
from src.messages.models import Message, UserRole


def make_message(chat_id=222, role=UserRole.USER, text='hi', nickname='user1', created_at=None):
    message = Message(chat_id=chat_id, role=role, text=text, nickname=nickname)
    message.created_at = created_at or datetime.now(timezone.utc)
    return message


# --- run_initiative_checks: kill switch gates everything, up front ---

async def test_run_initiative_checks_noop_when_checks_disabled(mocker):
    mocker.patch.object(settings, 'INITIATIVE_CHECKS_ENABLED', False)
    mock_get_run = mocker.patch(
        'src.initiative.handlers.get_last_initiative_run', new_callable=AsyncMock
    )

    await handlers.run_initiative_checks(222)

    assert mock_get_run.call_count == 0


# --- run_initiative_checks: pre_check gates the watermark save ---

async def test_run_initiative_checks_does_not_save_watermark_when_pre_check_fails(mocker):
    # Regression: save_initiative_run used to run before pre_check, so a chat that
    # keeps failing pre_check (cooldown, gap, too few messages) had its watermark
    # reset on every single message — the "since last run" count could never grow
    # past ~1, and the feature could never trigger again.
    mocker.patch.object(settings, 'INITIATIVE_CHECKS_ENABLED', True)
    mocker.patch('src.initiative.handlers.get_last_initiative_run', AsyncMock(return_value=None))
    mocker.patch(
        'src.initiative.handlers.fetch_last_messages', AsyncMock(return_value=[make_message()])
    )
    mock_save_run = mocker.patch(
        'src.initiative.handlers.save_initiative_run', new_callable=AsyncMock
    )
    mocker.patch('src.initiative.handlers.pre_check', AsyncMock(return_value=False))
    mock_evaluate = mocker.patch(
        'src.initiative.handlers.evaluate_initiative', new_callable=AsyncMock
    )

    await handlers.run_initiative_checks(222)

    assert mock_save_run.call_count == 0
    assert mock_evaluate.call_count == 0


async def test_concurrent_checks_claim_the_same_window_only_once(mocker):
    # Two messages arriving together spawn two `run_followups` tasks. Without a
    # lock over the watermark's read-then-write both read the same watermark, claim
    # the same window and the bot answers it twice.
    mocker.patch.object(settings, 'INITIATIVE_CHECKS_ENABLED', True)
    mocker.patch.object(settings, 'INITIATIVE_ENABLED', False)
    mocker.patch.object(settings, 'INITIATIVE_TRIGGER_SIZE', 1)
    mocker.patch.object(settings, 'INITIATIVE_COOLDOWN_MINUTES', 0)
    mocker.patch.object(settings, 'INITIATIVE_MIN_GAP_MESSAGES', 0)
    for _ in range(3):
        await save_message(make_message())
    mocker.patch('src.initiative.handlers.get_chat_character', new_callable=AsyncMock)
    mock_evaluate = mocker.patch(
        'src.initiative.handlers.evaluate_initiative',
        AsyncMock(return_value=InitiativeVerdict(target_message=None, score=0.0, reason='r')),
    )

    await asyncio.gather(
        handlers.run_initiative_checks(222),
        handlers.run_initiative_checks(222),
    )

    # The second claim re-reads the freshly saved watermark and finds nothing pending.
    assert mock_evaluate.call_count == 1


# --- run_initiative_checks: character memory + low score ---

async def test_run_initiative_checks_passes_memory_to_character_and_stops_on_low_score(mocker):
    # Regression: get_chat_character used to be called with no memory=, so
    # evaluate_initiative crashed on character.memory being None.
    mocker.patch.object(settings, 'INITIATIVE_CHECKS_ENABLED', True)
    mocker.patch('src.initiative.handlers.get_last_initiative_run', AsyncMock(return_value=None))
    newest = make_message()
    mocker.patch('src.initiative.handlers.fetch_last_messages', AsyncMock(return_value=[newest]))
    mock_save_run = mocker.patch('src.initiative.handlers.save_initiative_run', new_callable=AsyncMock)
    mocker.patch('src.initiative.handlers.pre_check', AsyncMock(return_value=True))
    sentinel_memory = object()
    mocker.patch('src.initiative.handlers.get_last_memory', AsyncMock(return_value=sentinel_memory))
    mock_get_character = mocker.patch(
        'src.initiative.handlers.get_chat_character', new_callable=AsyncMock
    )
    mocker.patch('src.initiative.handlers.evaluate_initiative', new_callable=AsyncMock)
    mocker.patch('src.initiative.handlers.decide', AsyncMock(return_value=False))
    mock_create_task = mocker.patch('src.initiative.handlers.asyncio.create_task')

    await handlers.run_initiative_checks(222)

    # The watermark is saved once pre_check passes, regardless of the eventual score.
    assert mock_save_run.call_count == 1
    assert mock_save_run.call_args == call(222, last_message_time=newest.created_at)
    assert mock_get_character.call_args == call(chat_id=222, memory=sentinel_memory)
    assert mock_create_task.call_count == 0


# --- run_initiative_checks: happy path ---

def _mock_full_pass(mocker, score=0.9):
    mocker.patch.object(settings, 'INITIATIVE_CHECKS_ENABLED', True)
    mocker.patch('src.initiative.handlers.get_last_initiative_run', AsyncMock(return_value=None))
    mocker.patch(
        'src.initiative.handlers.fetch_last_messages', AsyncMock(return_value=[make_message()])
    )
    mocker.patch(
        'src.initiative.handlers.save_initiative_run', AsyncMock(return_value='run-id')
    )
    mocker.patch('src.initiative.handlers.mark_initiative_replied', new_callable=AsyncMock)
    mocker.patch('src.initiative.handlers.pre_check', AsyncMock(return_value=True))
    mocker.patch('src.initiative.handlers.get_last_memory', AsyncMock(return_value=None))
    character = MagicMock()
    mocker.patch('src.initiative.handlers.get_chat_character', AsyncMock(return_value=character))
    evaluation = InitiativeVerdict(target_message=None, score=score, reason='r')
    mocker.patch(
        'src.initiative.handlers.evaluate_initiative', AsyncMock(return_value=evaluation)
    )
    mocker.patch('src.initiative.handlers.decide', AsyncMock(return_value=True))
    return character, evaluation


async def test_run_initiative_checks_schedules_reply_on_full_pass(mocker):
    mocker.patch.object(settings, 'INITIATIVE_ENABLED', True)
    character, evaluation = _mock_full_pass(mocker)
    mock_run_reply = mocker.patch(
        'src.initiative.handlers._run_initiative_reply', new_callable=AsyncMock
    )
    mock_mark_replied = handlers.mark_initiative_replied

    await handlers.run_initiative_checks(222)
    await asyncio.sleep(0)  # let the created task actually run

    assert mock_run_reply.call_count == 1
    assert mock_run_reply.call_args == call(chat_id=222, character=character, evaluation=evaluation)
    # Stamped before the task is spawned — the send path reserves the day's slot at
    # decision time, using the run id the claim returned.
    assert mock_mark_replied.call_count == 1
    assert mock_mark_replied.call_args == call('run-id')


async def test_run_initiative_checks_dry_run_when_initiative_disabled(mocker):
    # INITIATIVE_ENABLED off doesn't skip the pipeline — it runs and logs its verdict
    # (dry run), it just doesn't schedule an actual reply, and it doesn't stamp a send
    # that never happened.
    mocker.patch.object(settings, 'INITIATIVE_ENABLED', False)
    _mock_full_pass(mocker)
    mock_run_reply = mocker.patch(
        'src.initiative.handlers._run_initiative_reply', new_callable=AsyncMock
    )
    mock_create_task = mocker.patch('src.initiative.handlers.asyncio.create_task')

    await handlers.run_initiative_checks(222)

    assert mock_run_reply.call_count == 0
    assert mock_create_task.call_count == 0
    assert handlers.mark_initiative_replied.call_count == 0


# --- _get_messages: cold start vs. resuming from a watermark ---

async def test_get_messages_anchors_on_newest_when_no_prior_run(mocker):
    mock_fetch = mocker.patch(
        'src.initiative.handlers.fetch_last_messages', new_callable=AsyncMock
    )

    await handlers._get_messages(222, None)

    # With no watermark there is nothing to fetch context from, so only one call.
    assert mock_fetch.call_args_list == [
        call(222, size=settings.INITIATIVE_RUN_MESSAGES_MAX_SIZE, from_date=None, sort_order=-1),
    ]


async def test_get_messages_resumes_from_watermark_and_also_reads_context(mocker):
    mock_fetch = mocker.patch(
        'src.initiative.handlers.fetch_last_messages', AsyncMock(return_value=[])
    )
    watermark = datetime.now(timezone.utc) - timedelta(hours=1)

    await handlers._get_messages(222, watermark)

    assert mock_fetch.call_args_list == [
        call(222, size=settings.INITIATIVE_RUN_MESSAGES_MAX_SIZE, from_date=watermark, sort_order=-1),
        call(
            222, size=settings.INITIATIVE_CONTEXT_SIZE,
            to_date=watermark, to_date_inclusive=True, sort_order=-1,
        ),
    ]


async def test_get_messages_takes_the_newest_of_a_backlog_past_the_cap(mocker):
    # Regression: an oldest-first read after a cooldown-blocked stretch judged the
    # conversation from an hour ago rather than the one happening now.
    mocker.patch.object(settings, 'INITIATIVE_RUN_MESSAGES_MAX_SIZE', 3)
    watermark = datetime.now(timezone.utc) - timedelta(hours=1)
    for i in range(1, 6):
        await save_message(make_message(text=f'm{i}'))

    messages = await handlers._get_messages(222, watermark)

    assert [m.text for m in messages] == ['m3', 'm4', 'm5']


# --- _split_window: the gap cut, then re-partitioned around the watermark ---

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def at(minutes: float, text='hi') -> Message:
    return make_message(text=text, created_at=BASE + timedelta(minutes=minutes))


def test_split_window_cut_inside_candidate_region_empties_context():
    watermark = BASE
    old_context = at(-5, 'old')
    early_candidate = at(1, 'c1')
    late_candidate = at(1 + 30, 'c2')  # 30min gap past the default 15min threshold

    context, candidates = handlers._split_window(
        222, [old_context, early_candidate, late_candidate], watermark,
    )

    assert context == []
    assert candidates == [late_candidate]


def test_split_window_cut_inside_context_region_trims_context_only():
    watermark = BASE
    ancient_context = at(-120, 'ancient')
    recent_context = at(-2, 'recent')
    candidate = at(1, 'c1')

    context, candidates = handlers._split_window(
        222, [ancient_context, recent_context, candidate], watermark,
    )

    assert context == [recent_context]
    assert candidates == [candidate]


def test_split_window_cold_start_has_no_context_but_still_splits():
    old = at(0, 'old')
    new = at(30, 'new')

    context, candidates = handlers._split_window(222, [old, new], None)

    assert context == []
    assert candidates == [new]


def test_split_window_no_cut_keeps_everything_partitioned_by_watermark():
    watermark = BASE
    context_message = at(-2, 'ctx')
    candidate = at(1, 'c1')

    context, candidates = handlers._split_window(222, [context_message, candidate], watermark)

    assert context == [context_message]
    assert candidates == [candidate]


# --- _claim_window: the split runs before pre_check ---

async def test_claim_window_does_not_advance_watermark_when_pre_check_fails_after_the_cut(mocker):
    mocker.patch.object(settings, 'INITIATIVE_CHECKS_ENABLED', True)
    watermark = datetime.now(timezone.utc) - timedelta(hours=1)
    mocker.patch(
        'src.initiative.handlers.get_last_initiative_run',
        AsyncMock(return_value=MagicMock(last_message_time=watermark)),
    )
    old_candidate = make_message(text='old', created_at=watermark + timedelta(minutes=1))
    new_candidate = make_message(
        text='new', created_at=watermark + timedelta(minutes=1) + timedelta(minutes=30),
    )
    mocker.patch(
        'src.initiative.handlers.fetch_last_messages',
        AsyncMock(side_effect=[[old_candidate, new_candidate], []]),
    )
    mocker.patch('src.initiative.handlers.pre_check', AsyncMock(return_value=False))
    mock_save_run = mocker.patch(
        'src.initiative.handlers.save_initiative_run', new_callable=AsyncMock
    )

    claim = await handlers._claim_window(222)

    assert claim.context == []
    assert claim.candidates == []
    assert claim.run_id is None
    assert mock_save_run.call_count == 0


async def test_claim_window_returns_the_claimed_run_id_on_success(mocker):
    # The send path needs this id to stamp `replied_at` on the exact run it claimed.
    mocker.patch.object(settings, 'INITIATIVE_CHECKS_ENABLED', True)
    mocker.patch.object(settings, 'INITIATIVE_TRIGGER_SIZE', 1)
    mocker.patch('src.initiative.handlers.get_last_initiative_run', AsyncMock(return_value=None))
    mocker.patch(
        'src.initiative.handlers.fetch_last_messages', AsyncMock(return_value=[make_message()])
    )

    claim = await handlers._claim_window(222)

    assert claim.candidates != []
    assert claim.run_id is not None
    saved = await get_last_initiative_run(222)
    assert saved.id == claim.run_id


# --- _run_initiative_reply ---

async def test_run_initiative_reply_builds_replier_targeting_the_evaluated_message(
    mocker, make_bot,
):
    bot = make_bot()
    mocker.patch('src.initiative.handlers.get_bot', return_value=bot)
    mocker.patch('src.initiative.handlers.send_chat_action', new_callable=AsyncMock)
    mock_get_memory = mocker.patch(
        'src.initiative.handlers.get_last_memory', new_callable=AsyncMock
    )
    last_messages = [make_message(text='fresh')]
    mocker.patch(
        'src.initiative.handlers.fetch_last_messages', AsyncMock(return_value=last_messages)
    )
    character = MagicMock()
    character.respond = AsyncMock()
    own_memory = object()
    character.memory = own_memory
    target = make_message(text='target')
    evaluation = InitiativeVerdict(target_message=target, score=0.9, reason='r')

    await handlers._run_initiative_reply(chat_id=222, character=character, evaluation=evaluation)

    # `get_character` hands out a shared singleton, so a detached task re-stamping
    # `.memory` could land this chat's snapshot in another chat's in-flight reply.
    # The snapshot is passed in by `run_initiative_checks` instead.
    assert mock_get_memory.call_count == 0
    assert character.memory is own_memory
    assert character.respond.call_count == 1
    replier, respond_messages = character.respond.call_args[0]
    assert replier.target_message is target
    assert replier.chat_id == 222
    assert replier.bot is bot
    assert respond_messages == last_messages


async def test_run_initiative_reply_targets_the_chat_when_no_target_message(mocker, make_bot):
    mocker.patch('src.initiative.handlers.get_bot', return_value=make_bot())
    mocker.patch('src.initiative.handlers.send_chat_action', new_callable=AsyncMock)
    mocker.patch('src.initiative.handlers.fetch_last_messages', AsyncMock(return_value=[]))
    character = MagicMock()
    character.respond = AsyncMock()
    evaluation = InitiativeVerdict(target_message=None, score=0.9, reason='r')

    await handlers._run_initiative_reply(chat_id=222, character=character, evaluation=evaluation)

    replier = character.respond.call_args[0][0]
    assert replier.target_message is None


async def test_run_initiative_reply_sends_typing_action(mocker, make_bot):
    mocker.patch('src.initiative.handlers.get_bot', return_value=make_bot())
    mock_typing = mocker.patch('src.initiative.handlers.send_chat_action', new_callable=AsyncMock)
    mocker.patch('src.initiative.handlers.fetch_last_messages', AsyncMock(return_value=[]))
    character = MagicMock()
    character.respond = AsyncMock()
    evaluation = InitiativeVerdict(target_message=None, score=0.9, reason='r')

    await handlers._run_initiative_reply(chat_id=222, character=character, evaluation=evaluation)

    assert mock_typing.call_args == call(222, ChatAction.TYPING)


# --- _with_target: the evaluation window is wider than the reply window ---

def test_with_target_prepends_a_target_that_fell_out_of_the_reply_window():
    # INITIATIVE_RUN_MESSAGES_MAX_SIZE > LAST_MESSAGES_SIZE, so a target picked from
    # the older half of the evaluation window is absent from the messages the
    # character answers — it would get no `[TARGET]` marker while the reply still
    # quotes that message.
    target = make_message(text='target')
    target.id = 'target-id'
    window = [make_message(text='fresh')]
    window[0].id = 'fresh-id'

    result = handlers._with_target(window, target)

    assert result == [target, window[0]]


def test_with_target_leaves_the_window_alone_when_the_target_is_already_in_it():
    target = make_message(text='target')
    target.id = 'target-id'
    window = [target]

    assert handlers._with_target(window, target) is window


def test_with_target_leaves_the_window_alone_without_a_target():
    window = [make_message()]

    assert handlers._with_target(window, None) is window
