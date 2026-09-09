import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, call

from telegram.constants import ChatAction

from src import settings
from src.initiative import handlers
from src.initiative.models import InitiativeRun, InitiativeVerdict
from src.models import Message, UserRole


def make_message(chat_id=222, role=UserRole.USER, text='hi', nickname='user1'):
    message = Message(chat_id=chat_id, role=role, text=text, nickname=nickname)
    message.created_at = datetime.now(timezone.utc)
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
    mocker.patch('src.initiative.handlers.save_initiative_run', new_callable=AsyncMock)
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

    await handlers.run_initiative_checks(222)
    await asyncio.sleep(0)  # let the created task actually run

    assert mock_run_reply.call_count == 1
    assert mock_run_reply.call_args == call(chat_id=222, character=character, evaluation=evaluation)


async def test_run_initiative_checks_dry_run_when_initiative_disabled(mocker):
    # INITIATIVE_ENABLED off doesn't skip the pipeline — it runs and logs its verdict
    # (dry run), it just doesn't schedule an actual reply.
    mocker.patch.object(settings, 'INITIATIVE_ENABLED', False)
    _mock_full_pass(mocker)
    mock_run_reply = mocker.patch(
        'src.initiative.handlers._run_initiative_reply', new_callable=AsyncMock
    )
    mock_create_task = mocker.patch('src.initiative.handlers.asyncio.create_task')

    await handlers.run_initiative_checks(222)

    assert mock_run_reply.call_count == 0
    assert mock_create_task.call_count == 0


# --- _get_messages: cold start vs. resuming from a watermark ---

async def test_get_messages_anchors_on_newest_when_no_prior_run(mocker):
    mock_fetch = mocker.patch(
        'src.initiative.handlers.fetch_last_messages', new_callable=AsyncMock
    )

    await handlers._get_messages(222, None)

    assert mock_fetch.call_args == call(
        222, size=settings.INITIATIVE_RUN_MESSAGES_MAX_SIZE, sort_order=-1,
    )


async def test_get_messages_resumes_from_watermark_oldest_first(mocker):
    mock_fetch = mocker.patch(
        'src.initiative.handlers.fetch_last_messages', new_callable=AsyncMock
    )
    watermark = datetime.now(timezone.utc) - timedelta(hours=1)
    last_run = InitiativeRun(chat_id=222, last_message_time=watermark, created_at=watermark)

    await handlers._get_messages(222, last_run)

    assert mock_fetch.call_args == call(
        222, size=settings.INITIATIVE_RUN_MESSAGES_MAX_SIZE, from_date=watermark, sort_order=1,
    )


# --- _run_initiative_reply ---

async def test_run_initiative_reply_builds_replier_targeting_the_evaluated_message(
    mocker, make_bot,
):
    bot = make_bot()
    mocker.patch('src.initiative.handlers.get_bot', return_value=bot)
    mocker.patch('src.initiative.handlers.send_chat_action', new_callable=AsyncMock)
    memory = object()
    mocker.patch('src.initiative.handlers.get_last_memory', AsyncMock(return_value=memory))
    last_messages = [make_message(text='fresh')]
    mocker.patch(
        'src.initiative.handlers.fetch_last_messages', AsyncMock(return_value=last_messages)
    )
    character = MagicMock()
    character.respond = AsyncMock()
    target = make_message(text='target')
    evaluation = InitiativeVerdict(target_message=target, score=0.9, reason='r')

    await handlers._run_initiative_reply(chat_id=222, character=character, evaluation=evaluation)

    assert character.memory is memory
    assert character.respond.call_count == 1
    replier, respond_messages = character.respond.call_args[0]
    assert replier.target_message is target
    assert replier.chat_id == 222
    assert replier.bot is bot
    assert respond_messages == last_messages


async def test_run_initiative_reply_targets_the_chat_when_no_target_message(mocker, make_bot):
    mocker.patch('src.initiative.handlers.get_bot', return_value=make_bot())
    mocker.patch('src.initiative.handlers.send_chat_action', new_callable=AsyncMock)
    mocker.patch('src.initiative.handlers.get_last_memory', AsyncMock(return_value=None))
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
    mocker.patch('src.initiative.handlers.get_last_memory', AsyncMock(return_value=None))
    mocker.patch('src.initiative.handlers.fetch_last_messages', AsyncMock(return_value=[]))
    character = MagicMock()
    character.respond = AsyncMock()
    evaluation = InitiativeVerdict(target_message=None, score=0.9, reason='r')

    await handlers._run_initiative_reply(chat_id=222, character=character, evaluation=evaluation)

    assert mock_typing.call_args == call(222, ChatAction.TYPING)
