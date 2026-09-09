from src import settings
from src.initiative.models import InitiativeVerdict
from src.initiative.policies import decide, pre_check
from src.messages.repository import save_message
from src.models import Message, UserRole


def make_message(chat_id=222, role=UserRole.USER, text='hi', nickname='user1'):
    return Message(chat_id=chat_id, role=role, text=text, nickname=nickname)


# --- pre_check: trigger size ---

async def test_pre_check_fails_when_not_enough_messages(mocker):
    mocker.patch.object(settings, 'INITIATIVE_TRIGGER_SIZE', 5)
    messages = [make_message() for _ in range(4)]

    assert await pre_check(222, messages) is False


async def test_pre_check_passes_the_trigger_size_gate_at_exactly_the_threshold(mocker):
    mocker.patch.object(settings, 'INITIATIVE_TRIGGER_SIZE', 3)
    messages = [make_message() for _ in range(3)]

    assert await pre_check(222, messages) is True


# --- pre_check: other conditions ---

async def test_pre_check_fails_when_last_message_is_from_ai(mocker):
    mocker.patch.object(settings, 'INITIATIVE_TRIGGER_SIZE', 1)
    messages = [make_message(), make_message(role=UserRole.AI, nickname='bot')]

    assert await pre_check(222, messages) is False


async def test_pre_check_passes_when_no_bot_message_ever(mocker):
    mocker.patch.object(settings, 'INITIATIVE_TRIGGER_SIZE', 1)
    messages = [make_message()]

    assert await pre_check(222, messages) is True


async def test_pre_check_fails_within_cooldown(mocker):
    # Pin the cooldown explicitly — a local .env override (e.g. while manually
    # testing initiative live) can zero it out, which would make this pass for the
    # wrong reason (falling through to the gap check instead).
    mocker.patch.object(settings, 'INITIATIVE_TRIGGER_SIZE', 1)
    mocker.patch.object(settings, 'INITIATIVE_COOLDOWN_MINUTES', 60)
    await save_message(make_message(role=UserRole.AI, text='ok', nickname='bot'))
    messages = [make_message()]

    assert await pre_check(222, messages) is False


async def test_pre_check_fails_when_user_gap_too_small(mocker):
    mocker.patch.object(settings, 'INITIATIVE_TRIGGER_SIZE', 1)
    mocker.patch.object(settings, 'INITIATIVE_COOLDOWN_MINUTES', 0)
    mocker.patch.object(settings, 'INITIATIVE_MIN_GAP_MESSAGES', 5)
    await save_message(make_message(role=UserRole.AI, text='ok', nickname='bot'))
    for _ in range(2):
        await save_message(make_message())
    messages = [make_message()]

    assert await pre_check(222, messages) is False


async def test_pre_check_passes_when_cooldown_and_gap_satisfied(mocker):
    mocker.patch.object(settings, 'INITIATIVE_TRIGGER_SIZE', 1)
    mocker.patch.object(settings, 'INITIATIVE_COOLDOWN_MINUTES', 0)
    mocker.patch.object(settings, 'INITIATIVE_MIN_GAP_MESSAGES', 2)
    await save_message(make_message(role=UserRole.AI, text='ok', nickname='bot'))
    for _ in range(3):
        await save_message(make_message())
    messages = [make_message()]

    assert await pre_check(222, messages) is True


# --- decide ---

async def test_decide_true_when_score_meets_threshold(mocker):
    mocker.patch.object(settings, 'INITIATIVE_SCORE_THRESHOLD', 0.5)
    evaluation = InitiativeVerdict(target_message=None, score=0.5, reason='x')

    assert await decide(evaluation) is True


async def test_decide_false_when_score_below_threshold(mocker):
    mocker.patch.object(settings, 'INITIATIVE_SCORE_THRESHOLD', 0.5)
    evaluation = InitiativeVerdict(target_message=None, score=0.49, reason='x')

    assert await decide(evaluation) is False


async def test_decide_false_when_score_out_of_bounds():
    # InitiativeVerdict.score carries no pydantic bound (unlike InitiativeDecision.score),
    # so this guards a value built by a future caller rather than dead code today.
    evaluation = InitiativeVerdict(target_message=None, score=1.5, reason='x')

    assert await decide(evaluation) is False
