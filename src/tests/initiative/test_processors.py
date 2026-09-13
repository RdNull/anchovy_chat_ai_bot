import logging
import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.characters.character import Character
from src.initiative.models import InitiativeDecision
from src.initiative.processors import evaluate_initiative
from src.memory.models import ChatState, MemoryData, StructuredMemory
from src.models import Message, UserRole

_TIMESTAMP_PATTERN = re.compile(r'\d{2}-\d{2}-\d{2} \d{2}:\d{2}')


def make_character(memory=None):
    character = Character(
        code='test', display_name='Test', name='test',
        description='A test character', style_prompt='Говори коротко.',
    )
    character.memory = memory
    return character


def make_message(chat_id=1, role=UserRole.USER, text='hi', nickname='user1'):
    return Message(chat_id=chat_id, role=role, text=text, nickname=nickname)


def mock_initiative_llm(mocker, decision: InitiativeDecision):
    llm = MagicMock()
    llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=decision)
    mocker.patch('src.initiative.processors.ai.get_initiative_model', return_value=llm)
    return llm


def rendered_system_prompt(llm) -> str:
    return llm.with_structured_output.return_value.ainvoke.call_args[0][0][0].content


# --- target_index resolution (1-based, matching the `#N` labels shown to the model) ---

async def test_evaluate_initiative_resolves_first_message_from_index_one(mocker):
    candidates = [make_message(text=f'msg{i}') for i in range(3)]
    mock_initiative_llm(mocker, InitiativeDecision(score=0.6, target_index=1, reason='r'))

    result = await evaluate_initiative(make_character(), [], candidates)

    assert result.target_message is candidates[0]


async def test_evaluate_initiative_resolves_last_message_from_index_len(mocker):
    # Regression: the old `<` bound made the true last message unreachable.
    candidates = [make_message(text=f'msg{i}') for i in range(3)]
    mock_initiative_llm(mocker, InitiativeDecision(score=0.6, target_index=3, reason='r'))

    result = await evaluate_initiative(make_character(), [], candidates)

    assert result.target_message is candidates[2]


async def test_evaluate_initiative_out_of_range_target_index_yields_no_target(mocker):
    candidates = [make_message()]
    mock_initiative_llm(mocker, InitiativeDecision(score=0.6, target_index=5, reason='r'))

    result = await evaluate_initiative(make_character(), [], candidates)

    assert result.target_message is None


async def test_evaluate_initiative_zero_target_index_means_no_target(mocker):
    # `0` is a natural 'no target' for a model shown a 1-based list, so the field
    # carries no `gt` bound: a stray index resolves to None instead of raising in
    # validation and turning a good score into 'Initiative evaluation error'.
    candidates = [make_message()]
    mock_initiative_llm(mocker, InitiativeDecision(score=0.7, target_index=0, reason='r'))

    result = await evaluate_initiative(make_character(), [], candidates)

    assert result.target_message is None
    assert result.score == 0.7


async def test_evaluate_initiative_negative_target_index_means_no_target(mocker):
    candidates = [make_message()]
    mock_initiative_llm(mocker, InitiativeDecision(score=0.7, target_index=-1, reason='r'))

    result = await evaluate_initiative(make_character(), [], candidates)

    assert result.target_message is None
    assert result.score == 0.7


async def test_evaluate_initiative_null_target_index_means_reply_to_chat(mocker):
    candidates = [make_message()]
    mock_initiative_llm(
        mocker, InitiativeDecision(score=0.3, target_index=None, reason='нечего сказать')
    )

    result = await evaluate_initiative(make_character(), [], candidates)

    assert result.target_message is None
    assert result.score == 0.3
    assert result.reason == 'нечего сказать'


async def test_evaluate_initiative_target_index_never_addresses_context(mocker):
    # target_index is resolved against candidates only — a context message cannot
    # be targeted no matter what index the model returns.
    context = [make_message(text='before')]
    candidates = [make_message(text='after')]
    mock_initiative_llm(mocker, InitiativeDecision(score=0.6, target_index=1, reason='r'))

    result = await evaluate_initiative(make_character(), context, candidates)

    assert result.target_message is candidates[0]


# --- LLM failure ---

async def test_evaluate_initiative_handles_llm_error(mocker):
    llm = MagicMock()
    llm.with_structured_output.return_value.ainvoke = AsyncMock(side_effect=RuntimeError('boom'))
    mocker.patch('src.initiative.processors.ai.get_initiative_model', return_value=llm)

    result = await evaluate_initiative(make_character(), [], [make_message()])

    assert result.target_message is None
    assert result.score == 0
    assert result.reason == 'Initiative evaluation error'


async def test_evaluate_initiative_handles_empty_response(mocker):
    mock_initiative_llm(mocker, None)

    result = await evaluate_initiative(make_character(), [], [make_message()])

    assert result.target_message is None
    assert result.score == 0
    assert result.reason == 'Initiative evaluation empty response'


# --- character.memory handling ---

async def test_evaluate_initiative_without_memory_does_not_crash(mocker):
    llm = mock_initiative_llm(mocker, InitiativeDecision(score=0.1, target_index=None, reason='r'))

    result = await evaluate_initiative(make_character(memory=None), [], [make_message()])

    assert result.score == 0.1
    assert 'ПАМЯТЬ' not in rendered_system_prompt(llm)


async def test_evaluate_initiative_with_memory_includes_it_in_the_prompt(mocker):
    memory = MemoryData(
        chat_id=1,
        created_at=datetime.now(timezone.utc),
        content=StructuredMemory(state=ChatState(running_jokes=['стартер про пиццу'])),
    )
    llm = mock_initiative_llm(mocker, InitiativeDecision(score=0.1, target_index=None, reason='r'))

    await evaluate_initiative(make_character(memory=memory), [], [make_message()])

    assert 'стартер про пиццу' in rendered_system_prompt(llm)


# --- message rendering ---

async def test_evaluate_initiative_renders_bot_messages_with_their_nickname(mocker):
    # response_format (used for LangChain role-tagged history in character.py) strips
    # the nickname; here everything is flattened into a list, so the bot's own turns
    # need ai_format too or the model can't tell whose line is whose.
    candidates = [Message(chat_id=1, role=UserRole.AI, text='моя реплика', nickname='shizoded(anchovy)')]
    llm = mock_initiative_llm(mocker, InitiativeDecision(score=0.1, target_index=None, reason='r'))

    await evaluate_initiative(make_character(), [], candidates)

    assert 'shizoded(anchovy)' in rendered_system_prompt(llm)


async def test_evaluate_initiative_numbers_candidates_from_one(mocker):
    candidates = [make_message(text='first'), make_message(text='second')]
    llm = mock_initiative_llm(mocker, InitiativeDecision(score=0.1, target_index=None, reason='r'))

    await evaluate_initiative(make_character(), [], candidates)

    prompt = rendered_system_prompt(llm)
    assert '#1 ▸' in prompt
    assert '#2 ▸' in prompt


# --- two-block window: context is unnumbered, candidates are #1..#N ---

async def test_evaluate_initiative_renders_unnumbered_context_and_numbered_candidates(mocker):
    context = [make_message(text='раньше')]
    candidates = [make_message(text='теперь')]
    llm = mock_initiative_llm(mocker, InitiativeDecision(score=0.1, target_index=None, reason='r'))

    await evaluate_initiative(make_character(), context, candidates)

    prompt = rendered_system_prompt(llm)
    assert 'РАНЕЕ В РАЗГОВОРЕ' in prompt
    assert 'НОВЫЕ СООБЩЕНИЯ' in prompt
    context_line = next(line for line in prompt.splitlines() if 'раньше' in line)
    assert '#' not in context_line
    candidate_line = next(line for line in prompt.splitlines() if 'теперь' in line)
    assert '#1 ▸' in candidate_line


async def test_evaluate_initiative_empty_context_drops_the_block_and_its_label(mocker):
    candidates = [make_message(text='теперь')]
    llm = mock_initiative_llm(mocker, InitiativeDecision(score=0.1, target_index=None, reason='r'))

    await evaluate_initiative(make_character(), [], candidates)

    prompt = rendered_system_prompt(llm)
    assert 'РАНЕЕ В РАЗГОВОРЕ' not in prompt


# --- current time in the header ---

async def test_evaluate_initiative_includes_current_time_in_the_header(mocker):
    llm = mock_initiative_llm(mocker, InitiativeDecision(score=0.1, target_index=None, reason='r'))

    await evaluate_initiative(make_character(), [], [make_message()])

    prompt = rendered_system_prompt(llm)
    assert _TIMESTAMP_PATTERN.search(prompt.splitlines()[0])


# --- out-of-range target_index gets its own log line, distinct from a genuine null ---

async def test_evaluate_initiative_out_of_range_target_index_logs_a_warning(mocker, caplog):
    mock_initiative_llm(mocker, InitiativeDecision(score=0.6, target_index=5, reason='r'))

    with caplog.at_level(logging.WARNING, logger='bot'):
        await evaluate_initiative(make_character(), [], [make_message()])

    assert any('out of range' in record.message for record in caplog.records)


async def test_evaluate_initiative_null_target_index_does_not_log_a_warning(mocker, caplog):
    mock_initiative_llm(mocker, InitiativeDecision(score=0.3, target_index=None, reason='r'))

    with caplog.at_level(logging.WARNING, logger='bot'):
        await evaluate_initiative(make_character(), [], [make_message()])

    assert not any('out of range' in record.message for record in caplog.records)


async def test_evaluate_initiative_zero_target_index_does_not_log_a_warning(mocker, caplog):
    # 0 is the documented no-target encoding, not a stray coercion.
    mock_initiative_llm(mocker, InitiativeDecision(score=0.3, target_index=0, reason='r'))

    with caplog.at_level(logging.WARNING, logger='bot'):
        await evaluate_initiative(make_character(), [], [make_message()])

    assert not any('out of range' in record.message for record in caplog.records)
