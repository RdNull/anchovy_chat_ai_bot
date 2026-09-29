import logging
from datetime import datetime, timedelta, UTC

from src import settings
from src.initiative.models import InitiativeVerdict
from src.initiative.policies import collapse_repeats, decide, pre_check, split_at_gap
from src.initiative.repository import mark_initiative_replied, save_initiative_run
from src.messages.repository import save_message
from src.messages.models import Message, MessageMedia, MessageMediaTypes, UserRole


def make_message(chat_id=222, role=UserRole.USER, text='hi', nickname='user1', created_at=None):
    return Message(chat_id=chat_id, role=role, text=text, nickname=nickname, created_at=created_at)


BASE = datetime(2026, 1, 1, tzinfo=UTC)


def at(minutes: float, text='hi') -> Message:
    return make_message(text=text, created_at=BASE + timedelta(minutes=minutes))


# --- split_at_gap ---


def test_split_at_gap_empty_input_returns_empty():
    assert split_at_gap([], 15) == []


def test_split_at_gap_single_message_is_unchanged():
    message = at(0)

    assert split_at_gap([message], 15) == [message]


def test_split_at_gap_all_gaps_below_threshold_is_unchanged():
    messages = [at(0), at(5), at(10)]

    assert split_at_gap(messages, 15) == messages


def test_split_at_gap_one_large_gap_in_the_middle_returns_the_suffix():
    before = [at(0), at(5)]
    after = [at(30), at(35)]

    assert split_at_gap(before + after, 15) == after


def test_split_at_gap_several_large_gaps_returns_the_suffix_after_the_newest():
    first_thread = [at(0), at(5)]
    second_thread = [at(30), at(35)]
    third_thread = [at(60), at(65)]

    result = split_at_gap(first_thread + second_thread + third_thread, 15)

    assert result == third_thread


def test_split_at_gap_exactly_equal_to_threshold_is_not_cut():
    messages = [at(0), at(15)]

    assert split_at_gap(messages, 15) == messages


# --- pre_check: trigger size ---


async def test_pre_check_fails_when_not_enough_messages(mocker):
    mocker.patch.object(settings, 'INITIATIVE_TRIGGER_SIZE', 5)
    messages = [make_message() for _ in range(4)]

    assert await pre_check(222, messages) is False


async def test_pre_check_passes_the_trigger_size_gate_at_exactly_the_threshold(mocker):
    mocker.patch.object(settings, 'INITIATIVE_TRIGGER_SIZE', 3)
    # Distinct texts: identical consecutive messages now count as one.
    messages = [make_message(text=f'm{i}') for i in range(3)]

    assert await pre_check(222, messages) is True


# --- pre_check: other conditions ---


async def test_pre_check_fails_when_last_message_is_from_ai(mocker):
    mocker.patch.object(settings, 'INITIATIVE_TRIGGER_SIZE', 1)
    await save_message(make_message())
    await save_message(make_message(role=UserRole.AI, text='ответ', nickname='bot'))

    assert await pre_check(222, [make_message()]) is False


async def test_pre_check_reads_the_chats_newest_rather_than_the_window_tail(mocker):
    # Regression: this guard read `messages[-1]`. The window is fetched before the
    # check runs, so a message can land in between — the bot's own reply included —
    # and the window tail is then not what was said last.
    mocker.patch.object(settings, 'INITIATIVE_TRIGGER_SIZE', 1)
    await save_message(make_message(text='старое'))
    await save_message(make_message(role=UserRole.AI, text='ответ', nickname='bot'))
    window_ending_on_a_user_message = [make_message(text='старое')]

    assert await pre_check(222, window_ending_on_a_user_message) is False


async def test_pre_check_ignores_an_ai_message_at_the_window_tail(mocker):
    # The mirror case: a user message landing after the fetch leaves the window ending
    # on a bot turn while the chat has moved on.
    mocker.patch.object(settings, 'INITIATIVE_TRIGGER_SIZE', 1)
    mocker.patch.object(settings, 'INITIATIVE_COOLDOWN_MINUTES', 0)
    mocker.patch.object(settings, 'INITIATIVE_MIN_GAP_MESSAGES', 1)
    await save_message(make_message(role=UserRole.AI, text='ответ', nickname='bot'))
    await save_message(make_message(text='и что'))
    window_ending_on_a_bot_message = [make_message(role=UserRole.AI, nickname='bot')]

    assert await pre_check(222, window_ending_on_a_bot_message) is True


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


# --- pre_check: daily limit (last gate, after the other four) ---


async def _seed_replied_run(chat_id=222):
    run_id = await save_initiative_run(chat_id, last_message_time=datetime.now(UTC))
    await mark_initiative_replied(run_id)


async def test_pre_check_passes_under_the_daily_limit(mocker):
    mocker.patch.object(settings, 'INITIATIVE_TRIGGER_SIZE', 1)
    mocker.patch.object(settings, 'INITIATIVE_DAILY_LIMIT', 3)
    for _ in range(2):
        await _seed_replied_run()

    assert await pre_check(222, [make_message()]) is True


async def test_pre_check_fails_at_the_daily_limit(mocker, caplog):
    mocker.patch.object(settings, 'INITIATIVE_TRIGGER_SIZE', 1)
    mocker.patch.object(settings, 'INITIATIVE_DAILY_LIMIT', 3)
    for _ in range(3):
        await _seed_replied_run()

    with caplog.at_level(logging.INFO, logger='bot'):
        result = await pre_check(222, [make_message()])

    assert result is False
    daily_limit_records = [r for r in caplog.records if getattr(r, 'reason', None) == 'daily_limit']
    assert len(daily_limit_records) == 1
    assert daily_limit_records[0].count == 3
    assert daily_limit_records[0].limit == 3


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


# --- collapse_repeats / repeat-aware trigger gate ---


def sticker(unique_id='s1', nickname='user1'):
    media = MessageMedia(media_id='f', unique_id=unique_id, type=MessageMediaTypes.STICKER)
    return Message(chat_id=222, role=UserRole.USER, nickname=nickname, media=media)


def test_collapse_repeats_merges_identical_stickers_and_keeps_the_last():
    stickers = [sticker() for _ in range(3)]

    groups = collapse_repeats(stickers)

    assert len(groups) == 1
    assert groups[0].count == 3
    assert groups[0].representative is stickers[-1]


def test_collapse_repeats_keeps_the_same_sticker_from_two_authors_apart():
    groups = collapse_repeats([sticker(nickname='a'), sticker(nickname='b')])

    assert [g.count for g in groups] == [1, 1]


def test_collapse_repeats_keeps_different_stickers_from_one_author_apart():
    groups = collapse_repeats([sticker('s1'), sticker('s2')])

    assert [g.count for g in groups] == [1, 1]


def test_collapse_repeats_merges_identical_text():
    groups = collapse_repeats([make_message(text='ха'), make_message(text=' ха ')])

    assert [g.count for g in groups] == [2]


def test_collapse_repeats_merges_the_same_gif_unique_id():
    def gif():
        media = MessageMedia(media_id='f', unique_id='g1', type=MessageMediaTypes.GIF)
        return Message(chat_id=222, role=UserRole.USER, nickname='user1', media=media)

    assert [g.count for g in collapse_repeats([gif(), gif()])] == [2]


def test_collapse_repeats_splits_a_run_interrupted_by_another_author():
    groups = collapse_repeats([
        sticker(),
        sticker(),
        make_message(nickname='other'),
        sticker(),
    ])

    assert [g.count for g in groups] == [2, 1, 1]


def test_collapse_repeats_never_merges_messages_without_text_or_media_id():
    empty = [Message(chat_id=222, role=UserRole.USER, nickname='user1') for _ in range(2)]

    assert [g.count for g in collapse_repeats(empty)] == [1, 1]


async def test_pre_check_counts_identical_stickers_as_one_message(mocker, caplog):
    mocker.patch.object(settings, 'INITIATIVE_TRIGGER_SIZE', 5)

    with caplog.at_level(logging.INFO, logger='bot'):
        result = await pre_check(222, [sticker() for _ in range(16)])

    assert result is False
    records = [r for r in caplog.records if getattr(r, 'reason', None) == 'messages_too_few']
    assert len(records) == 1
    assert records[0].count == 1
    assert records[0].raw_count == 16
