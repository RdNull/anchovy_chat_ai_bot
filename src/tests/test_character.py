import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src import settings
from src.characters.character import Character
from src.models import MemoryData, Message, StructuredMemory, UserRole
from src.tools import ToolRegistry


def make_character():
    return Character(
        code='test',
        display_name='Test',
        name='test',
        description='A test character',
        style_prompt='Говори только по-русски и коротко.',
    )


def make_user_message(chat_id=1, text='hello'):
    return Message(chat_id=chat_id, role=UserRole.USER, text=text, nickname='user1')


def mock_chat_llm(mocker, responses):
    """MagicMock base so bind_tools() is sync; ainvoke is AsyncMock."""
    llm = MagicMock()
    llm.bind_tools.return_value = llm
    llm.ainvoke = AsyncMock(side_effect=responses)
    mocker.patch('src.characters.character.ai.get_model', return_value=llm)
    return llm


# --- respond() ---

async def test_respond_returns_llm_text(mocker):
    llm = mock_chat_llm(mocker, [AIMessage(content='привет!')])
    user_msg = make_user_message()

    result = await make_character().respond(user_msg, last_messages=[])

    assert result == 'привет!'
    llm.ainvoke.assert_called_once()
    msgs = llm.ainvoke.call_args[0][0]
    assert len(msgs) == 2  # SystemMessage + HumanMessage
    assert isinstance(msgs[0], SystemMessage)
    assert isinstance(msgs[1], HumanMessage)
    assert msgs[1].content == user_msg.ai_format


async def test_respond_with_history(mocker):
    llm = mock_chat_llm(mocker, [AIMessage(content='ответ')])
    history = [
        Message(chat_id=1, role=UserRole.USER, text='раньше', nickname='user1'),
        Message(chat_id=1, role=UserRole.AI, text='ок', nickname='bot'),
    ]
    user_msg = make_user_message()

    result = await make_character().respond(user_msg, last_messages=history)

    assert result == 'ответ'
    msgs = llm.ainvoke.call_args[0][0]
    # SystemMessage + HumanMessage(раньше) + AIMessage(ок) + HumanMessage(current)
    assert len(msgs) == 4
    assert isinstance(msgs[1], HumanMessage)
    assert isinstance(msgs[2], AIMessage)
    assert isinstance(msgs[3], HumanMessage)


async def test_respond_executes_tool_call_and_recurses(mocker):
    tool_call = {
        'id': 'tc1',
        'name': 'search_messages',
        'args': {'search_query': 'test', 'limit': 3},
    }
    llm = mock_chat_llm(mocker, [
        AIMessage(content='', tool_calls=[tool_call]),
        AIMessage(content='final answer'),
    ])
    mock_execute = mocker.patch.object(
        ToolRegistry,
        'execute',
        new=AsyncMock(return_value=ToolMessage(tool_call_id='tc1', content='[]')),
    )

    result = await make_character().respond(make_user_message(), last_messages=[])

    assert result == 'final answer'
    assert llm.ainvoke.call_count == 2
    mock_execute.assert_called_once()
    # Second ainvoke receives the accumulated messages including tool result
    second_msgs = llm.ainvoke.call_args[0][0]
    assert len(second_msgs) == 4  # SystemMessage, HumanMessage, AIMessage(tool), ToolMessage
    assert isinstance(second_msgs[-1], ToolMessage)


async def test_respond_timeout_returns_fallback(mocker):
    async def slow(*_args, **_kwargs):
        await asyncio.sleep(10)

    llm = MagicMock()
    llm.bind_tools.return_value = llm
    llm.ainvoke = AsyncMock(side_effect=slow)
    mocker.patch('src.characters.character.ai.get_model', return_value=llm)
    mocker.patch.object(settings, 'AI_TIMEOUT', 0.01)

    result = await make_character().respond(make_user_message(), last_messages=[])

    assert result == 'Чё-то я призадумался и забыл, че хотел сказать...'


async def test_respond_exception_returns_fallback(mocker):
    llm = MagicMock()
    llm.bind_tools.return_value = llm
    llm.ainvoke = AsyncMock(side_effect=RuntimeError('boom'))
    mocker.patch('src.characters.character.ai.get_model', return_value=llm)

    result = await make_character().respond(make_user_message(), last_messages=[])

    assert result == 'Голова чё-то разболелась, давай потом...'


# --- system_message ---

def test_system_message_contains_style_prompt():
    character = make_character()
    msg = character.system_message

    assert isinstance(msg, SystemMessage)
    assert 'Говори только по-русски и коротко.' in msg.content


def test_system_message_without_memory_has_no_memory_section():
    character = make_character()
    character.memory = None

    assert 'ПАМЯТЬ' not in character.system_message.content


def test_system_message_with_memory_includes_memory_section():
    character = make_character()
    character.memory = MemoryData(
        chat_id=1,
        created_at=datetime.now(timezone.utc),
        content=StructuredMemory(constraints=['test constraint']),
    )

    assert 'ПАМЯТЬ' in character.system_message.content
    assert 'test constraint' in character.system_message.content
