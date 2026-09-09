from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.characters.character import Character
from src.initiative.models import InitiativeDecision
from src.initiative.processors import evaluate_initiative
from src.memory.models import ChatState, MemoryData, StructuredMemory
from src.models import Message, UserRole


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
    messages = [make_message(text=f'msg{i}') for i in range(3)]
    mock_initiative_llm(mocker, InitiativeDecision(score=0.6, target_index=1, reason='r'))

    result = await evaluate_initiative(make_character(), messages)

    assert result.target_message is messages[0]


async def test_evaluate_initiative_resolves_last_message_from_index_len(mocker):
    # Regression: the old `<` bound made the true last message unreachable.
    messages = [make_message(text=f'msg{i}') for i in range(3)]
    mock_initiative_llm(mocker, InitiativeDecision(score=0.6, target_index=3, reason='r'))

    result = await evaluate_initiative(make_character(), messages)

    assert result.target_message is messages[2]


async def test_evaluate_initiative_out_of_range_target_index_yields_no_target(mocker):
    messages = [make_message()]
    mock_initiative_llm(mocker, InitiativeDecision(score=0.6, target_index=5, reason='r'))

    result = await evaluate_initiative(make_character(), messages)

    assert result.target_message is None


async def test_evaluate_initiative_null_target_index_means_reply_to_chat(mocker):
    messages = [make_message()]
    mock_initiative_llm(
        mocker, InitiativeDecision(score=0.3, target_index=None, reason='нечего сказать')
    )

    result = await evaluate_initiative(make_character(), messages)

    assert result.target_message is None
    assert result.score == 0.3
    assert result.reason == 'нечего сказать'


# --- LLM failure ---

async def test_evaluate_initiative_handles_llm_error(mocker):
    llm = MagicMock()
    llm.with_structured_output.return_value.ainvoke = AsyncMock(side_effect=RuntimeError('boom'))
    mocker.patch('src.initiative.processors.ai.get_initiative_model', return_value=llm)

    result = await evaluate_initiative(make_character(), [make_message()])

    assert result.target_message is None
    assert result.score == 0
    assert result.reason == 'Initiative evaluation error'


async def test_evaluate_initiative_handles_empty_response(mocker):
    mock_initiative_llm(mocker, None)

    result = await evaluate_initiative(make_character(), [make_message()])

    assert result.target_message is None
    assert result.score == 0
    assert result.reason == 'Initiative evaluation empty response'


# --- character.memory handling ---

async def test_evaluate_initiative_without_memory_does_not_crash(mocker):
    llm = mock_initiative_llm(mocker, InitiativeDecision(score=0.1, target_index=None, reason='r'))

    result = await evaluate_initiative(make_character(memory=None), [make_message()])

    assert result.score == 0.1
    assert 'ПАМЯТЬ' not in rendered_system_prompt(llm)


async def test_evaluate_initiative_with_memory_includes_it_in_the_prompt(mocker):
    memory = MemoryData(
        chat_id=1,
        created_at=datetime.now(timezone.utc),
        content=StructuredMemory(state=ChatState(running_jokes=['стартер про пиццу'])),
    )
    llm = mock_initiative_llm(mocker, InitiativeDecision(score=0.1, target_index=None, reason='r'))

    await evaluate_initiative(make_character(memory=memory), [make_message()])

    assert 'стартер про пиццу' in rendered_system_prompt(llm)


# --- message rendering ---

async def test_evaluate_initiative_renders_bot_messages_with_their_nickname(mocker):
    # response_format (used for LangChain role-tagged history in character.py) strips
    # the nickname; here everything is flattened into one numbered list, so the bot's
    # own turns need ai_format too or the model can't tell whose line is whose.
    messages = [Message(chat_id=1, role=UserRole.AI, text='моя реплика', nickname='shizoded(anchovy)')]
    llm = mock_initiative_llm(mocker, InitiativeDecision(score=0.1, target_index=None, reason='r'))

    await evaluate_initiative(make_character(), messages)

    assert 'shizoded(anchovy)' in rendered_system_prompt(llm)


async def test_evaluate_initiative_numbers_messages_from_one(mocker):
    messages = [make_message(text='first'), make_message(text='second')]
    llm = mock_initiative_llm(mocker, InitiativeDecision(score=0.1, target_index=None, reason='r'))

    await evaluate_initiative(make_character(), messages)

    prompt = rendered_system_prompt(llm)
    assert '#1 ▸' in prompt
    assert '#2 ▸' in prompt
