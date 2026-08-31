import asyncio
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, call

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src import settings
from src.characters.character import Character
from src.characters.rate_limit import SlidingWindowRateLimiter
from src.memory.models import ChatState, MemoryData, StructuredMemory
from src.models import Message, UserRole
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


def make_replier():
    replier = MagicMock()
    replier.reply_message = AsyncMock()
    replier.reply_reaction = AsyncMock()
    return replier


def mock_chat_llm(mocker, responses):
    """MagicMock base so bind_tools() is sync; ainvoke is AsyncMock."""
    llm = MagicMock()
    llm.bind_tools.return_value = llm
    llm.ainvoke = AsyncMock(side_effect=responses)
    mocker.patch('src.characters.character.ai.get_model', return_value=llm)
    return llm


def answer_tool_call(text='ответ', tc_id='tc1'):
    return AIMessage(content='', tool_calls=[{
        'id': tc_id, 'name': 'answer_text', 'args': {'text': text}, 'type': 'tool_call'
    }])


def search_tool_call(query='test', tc_id='tc2'):
    return AIMessage(content='', tool_calls=[{
        'id': tc_id, 'name': 'search_messages', 'args': {'search_query': query, 'limit': 3},
        'type': 'tool_call'
    }])


# --- respond() ---

async def test_respond_adds_version_tag_to_run_tree(mocker, mock_langsmith):
    mock_chat_llm(mocker, [answer_tool_call()])
    mocker.patch('src.characters.character.random.choice', return_value='test-version')
    mocker.patch.object(
        ToolRegistry, 'execute',
        new=AsyncMock(return_value=ToolMessage(tool_call_id='tc1', content=''))
    )
    replier = make_replier()

    await make_character().respond(replier, make_user_message(), last_messages=[])

    assert mock_langsmith.tags == ['test-version']


async def test_respond_calls_answer_tool(mocker):
    llm = mock_chat_llm(mocker, [answer_tool_call(text='привет!')])
    user_msg = make_user_message()
    mock_execute = mocker.patch.object(
        ToolRegistry, 'execute',
        new=AsyncMock(return_value=ToolMessage(tool_call_id='tc1', content=''))
    )
    replier = make_replier()

    await make_character().respond(replier, user_msg, last_messages=[])

    assert llm.ainvoke.call_count == 1
    msgs = llm.ainvoke.call_args[0][0]
    # ainvoke was called with [System, Human] (2 msgs); response is appended after → 3 total
    assert len(msgs) == 3
    assert isinstance(msgs[0], SystemMessage)
    assert isinstance(msgs[1], HumanMessage)
    assert msgs[1].content == user_msg.embedding_text
    assert isinstance(msgs[2], AIMessage)  # the answer_text tool_call response
    assert mock_execute.call_count == 1


async def test_respond_with_history(mocker):
    llm = mock_chat_llm(mocker, [answer_tool_call(text='ответ')])
    history = [
        Message(chat_id=1, role=UserRole.USER, text='раньше', nickname='user1'),
        Message(chat_id=1, role=UserRole.AI, text='ок', nickname='bot'),
    ]
    user_msg = make_user_message(text='last message')
    mocker.patch.object(ToolRegistry, 'execute',
                        new=AsyncMock(return_value=ToolMessage(tool_call_id='tc1', content='')))
    replier = make_replier()

    await make_character().respond(replier, user_msg, last_messages=history)

    msgs = llm.ainvoke.call_args[0][0]
    # ainvoke called with [System, Human(раньше), AI(ок), Human(current)] (4 msgs);
    # response appended after → 5 total
    assert len(msgs) == 5
    assert isinstance(msgs[1], HumanMessage)
    assert isinstance(msgs[2], AIMessage)
    assert isinstance(msgs[3], HumanMessage)
    assert isinstance(msgs[4], AIMessage)  # the answer_text tool_call response


async def test_respond_executes_context_tool_then_direct_tool(mocker):
    search_call = search_tool_call(tc_id='tc_search')
    answer_call = answer_tool_call(text='final answer', tc_id='tc_answer')
    llm = mock_chat_llm(mocker, [search_call, answer_call])
    mock_execute = mocker.patch.object(
        ToolRegistry,
        'execute',
        new=AsyncMock(return_value=ToolMessage(tool_call_id='tc_search', content='[]')),
    )
    replier = make_replier()

    await make_character().respond(replier, make_user_message(), last_messages=[])

    assert llm.ainvoke.call_count == 2
    assert mock_execute.call_count == 2
    # Both call_args_list entries share the same mutable list reference.
    # Final state: [System, Human, AIMsg(search), ToolMsg(search), AIMsg(answer)] = 5
    second_msgs = llm.ainvoke.call_args_list[1][0][0]
    assert len(second_msgs) == 5
    assert isinstance(second_msgs[2], AIMessage)  # search_messages tool_call
    assert isinstance(second_msgs[3], ToolMessage)  # search result
    assert isinstance(second_msgs[4], AIMessage)  # answer_text tool_call


async def test_respond_timeout_calls_replier_fallback(mocker):
    async def slow(*_args, **_kwargs):
        await asyncio.sleep(10)

    llm = MagicMock()
    llm.bind_tools.return_value = llm
    llm.ainvoke = AsyncMock(side_effect=slow)
    mocker.patch('src.characters.character.ai.get_model', return_value=llm)
    mocker.patch.object(settings, 'AI_TIMEOUT', 0.01)
    replier = make_replier()

    await make_character().respond(replier, make_user_message(), last_messages=[])

    assert replier.reply_message.call_count == 1
    error_text = replier.reply_message.call_args[0][0]
    assert error_text == 'Чё-то я призадумался и забыл, че хотел сказать...'


async def test_respond_exception_calls_replier_fallback(mocker):
    llm = MagicMock()
    llm.bind_tools.return_value = llm
    llm.ainvoke = AsyncMock(side_effect=RuntimeError('boom'))
    mocker.patch('src.characters.character.ai.get_model', return_value=llm)
    replier = make_replier()

    await make_character().respond(replier, make_user_message(), last_messages=[])

    assert replier.reply_message.call_count == 1
    assert replier.reply_message.call_args[0][0] == 'Голова чё-то разболелась, давай потом...'


async def test_respond_rate_limited_returns_silently(mocker):
    mocker.patch('src.characters.rate_limit.SlidingWindowRateLimiter.is_exceeded', return_value=True)
    replier = make_replier()

    await make_character().respond(replier, make_user_message(), last_messages=[])

    assert replier.reply_message.call_count == 0


async def test_respond_not_rate_limited_proceeds(mocker):
    mocker.patch('src.characters.rate_limit.SlidingWindowRateLimiter.is_exceeded', return_value=False)
    mock_chat_llm(mocker, [answer_tool_call(text='ответ')])
    mock_execute = mocker.patch.object(
        ToolRegistry, 'execute',
        new=AsyncMock(return_value=ToolMessage(tool_call_id='tc1', content=''))
    )
    replier = make_replier()

    await make_character().respond(replier, make_user_message(), last_messages=[])

    assert mock_execute.call_count == 1


async def test_respond_no_tool_calls_stays_silent(mocker):
    mock_chat_llm(mocker, [AIMessage(content='ignoring tools')])
    mock_warning = mocker.patch('src.characters.character.logger.warning')
    replier = make_replier()

    await make_character().respond(replier, make_user_message(), last_messages=[])

    # No tool_calls means intended silence — nothing is sent, just a warning logged
    assert replier.reply_message.call_count == 0
    assert replier.reply_reaction.call_count == 0
    assert mock_warning.call_args_list == [call('Tool requirement was ignored')]


async def test_respond_multiple_direct_tools_tags_langsmith(mocker, mock_langsmith):
    both_calls = AIMessage(content='', tool_calls=[
        {'id': 'tc1', 'name': 'answer_text', 'args': {'text': 'hi'}, 'type': 'tool_call'},
        {'id': 'tc2', 'name': 'set_reaction', 'args': {'emoji': '🤡'}, 'type': 'tool_call'},
    ])
    mock_chat_llm(mocker, [both_calls])
    mocker.patch.object(
        ToolRegistry,
        'execute',
        new=AsyncMock(return_value=ToolMessage(tool_call_id='tc1', content=''))
    )
    replier = make_replier()

    await make_character().respond(replier, make_user_message(), last_messages=[])

    assert 'multiple_response_called' in mock_langsmith.tags


# --- rate limiting ---

def test_rate_limiter_allows_calls_under_limit():
    rl = SlidingWindowRateLimiter(rate_limit=3)
    assert not rl.is_exceeded(chat_id=1)
    assert not rl.is_exceeded(chat_id=1)
    assert not rl.is_exceeded(chat_id=1)


def test_rate_limiter_blocks_when_limit_reached(mocker):
    mocker.patch.object(settings, 'CHAT_RATE_LIMIT', 2)
    rl = SlidingWindowRateLimiter()
    rl.is_exceeded(1)
    rl.is_exceeded(1)
    assert rl.is_exceeded(1)


def test_rate_limiter_independent_per_chat(mocker):
    mocker.patch.object(settings, 'CHAT_RATE_LIMIT', 1)
    rl = SlidingWindowRateLimiter()
    rl.is_exceeded(1)
    assert rl.is_exceeded(1)
    assert not rl.is_exceeded(2)


def test_rate_limiter_allows_after_window_expires(mocker):
    mocker.patch.object(settings, 'CHAT_RATE_LIMIT', 1)
    rl = SlidingWindowRateLimiter()
    rl._call_times[1].append(time.monotonic() - 61)
    assert not rl.is_exceeded(1)


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
        content=StructuredMemory(
            state=ChatState(open_questions=['oppa'])
        )
    )

    assert 'ПАМЯТЬ' in character.system_message.content
    assert 'oppa' in character.system_message.content
