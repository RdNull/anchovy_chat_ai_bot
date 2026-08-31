import asyncio
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, call

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src import settings
from src.characters.character import _MAX_LOOP_DEPTH, Character
from src.characters.rate_limit import SlidingWindowRateLimiter
from src.memory.models import ChatState, MemoryData, StructuredMemory
from src.models import Message, UserRole
from src.tools import ToolFailure, ToolRegistry


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
        new=AsyncMock(return_value=(ToolMessage(tool_call_id='tc1', content=''), None))
    )
    replier = make_replier()

    await make_character().respond(replier, make_user_message(), last_messages=[])

    assert mock_langsmith.tags == ['test-version']


async def test_respond_calls_answer_tool(mocker):
    llm = mock_chat_llm(mocker, [answer_tool_call(text='привет!')])
    user_msg = make_user_message()
    mock_execute = mocker.patch.object(
        ToolRegistry, 'execute',
        new=AsyncMock(return_value=(ToolMessage(tool_call_id='tc1', content=''), None))
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
                        new=AsyncMock(return_value=(ToolMessage(tool_call_id='tc1', content=''), None)))
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
        new=AsyncMock(return_value=(ToolMessage(tool_call_id='tc_search', content='[]'), None)),
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
        new=AsyncMock(return_value=(ToolMessage(tool_call_id='tc1', content=''), None))
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
        new=AsyncMock(return_value=(ToolMessage(tool_call_id='tc1', content=''), None))
    )
    replier = make_replier()

    await make_character().respond(replier, make_user_message(), last_messages=[])

    assert 'multiple_response_called' in mock_langsmith.tags


# --- sticker tool binding ---

def captured_registry(mocker):
    """Captures the ToolRegistry the loop is built with, without running the loop."""
    captured = {}
    real_init = ToolRegistry.__init__

    def spy(self, context_tools, direct_tools, context):
        real_init(self, context_tools, direct_tools, context)
        captured['registry'] = self

    mocker.patch.object(ToolRegistry, '__init__', spy)
    return captured


async def respond_and_capture(mocker, corpus_size, enabled):
    mock_chat_llm(mocker, [answer_tool_call()])
    mocker.patch.object(ToolRegistry, 'execute', new=execute_returning(None))
    mocker.patch.object(settings, 'ENABLE_STICKER_REPLIES', enabled)
    mocker.patch.object(settings, 'STICKER_MIN_CORPUS', 5)
    mock_size = mocker.patch(
        'src.characters.character.sticker_corpus_size', return_value=corpus_size,
    )
    captured = captured_registry(mocker)

    await make_character().respond(make_replier(), make_user_message(), last_messages=[])

    return captured['registry'], mock_size


def tool_names(registry):
    return {t.name for t in registry.tools}


async def test_sticker_tools_not_bound_below_the_corpus_threshold(mocker):
    registry, _ = await respond_and_capture(mocker, corpus_size=4, enabled=True)

    names = tool_names(registry)
    assert 'find_stickers' not in names
    assert 'send_sticker' not in names


async def test_sticker_tools_bound_at_the_corpus_threshold(mocker):
    registry, _ = await respond_and_capture(mocker, corpus_size=5, enabled=True)

    # Both or neither: send_sticker alone gives the model an id it can only hallucinate.
    assert 'find_stickers' in {t.name for t in registry.context_tools}
    assert 'send_sticker' in {t.name for t in registry.direct_tools}


async def test_sticker_tools_not_bound_when_the_flag_is_off(mocker):
    registry, mock_size = await respond_and_capture(mocker, corpus_size=500, enabled=False)

    names = tool_names(registry)
    assert 'find_stickers' not in names
    assert 'send_sticker' not in names
    # The flag is checked first, so a disabled installation never pays for the count.
    assert mock_size.call_count == 0


async def test_corpus_size_is_queried_once_per_respond(mocker):
    _, mock_size = await respond_and_capture(mocker, corpus_size=5, enabled=True)

    assert mock_size.call_count == 1


async def test_base_tools_are_always_bound(mocker):
    registry, _ = await respond_and_capture(mocker, corpus_size=0, enabled=False)

    assert tool_names(registry) == {
        'search_messages', 'get_user_facts', 'search_web', 'answer_text', 'set_reaction',
    }


# --- direct-tool failure recovery ---

def execute_returning(*results):
    """Patches ToolRegistry.execute to hand back (ToolMessage, raw) pairs in order."""
    return AsyncMock(side_effect=[
        (ToolMessage(tool_call_id=f'tc{i}', content=str(r)), r)
        for i, r in enumerate(results)
    ])


async def test_direct_tool_returning_none_terminates_immediately(mocker):
    # Regression guard on the existing behaviour: success still ends the turn.
    llm = mock_chat_llm(mocker, [answer_tool_call(), answer_tool_call()])
    mocker.patch.object(ToolRegistry, 'execute', new=execute_returning(None))

    await make_character().respond(make_replier(), make_user_message(), last_messages=[])

    assert llm.ainvoke.call_count == 1


async def test_direct_tool_failure_gives_the_model_another_turn(mocker):
    llm = mock_chat_llm(mocker, [answer_tool_call(tc_id='first'), answer_tool_call(tc_id='second')])
    mocker.patch.object(
        ToolRegistry, 'execute', new=execute_returning(ToolFailure('стикер недоступен'), None),
    )

    await make_character().respond(make_replier(), make_user_message(), last_messages=[])

    assert llm.ainvoke.call_count == 2
    # The failure is fed back as a ToolMessage so the model can pick something else.
    final_msgs = llm.ainvoke.call_args_list[-1][0][0]
    assert isinstance(final_msgs[3], ToolMessage)
    assert 'стикер недоступен' in final_msgs[3].content


async def test_direct_tool_failure_then_success_terminates(mocker):
    llm = mock_chat_llm(mocker, [answer_tool_call(), answer_tool_call(), answer_tool_call()])
    mocker.patch.object(
        ToolRegistry, 'execute', new=execute_returning(ToolFailure('boom'), None),
    )

    await make_character().respond(make_replier(), make_user_message(), last_messages=[])

    assert llm.ainvoke.call_count == 2


async def test_direct_tool_failing_every_turn_stops_at_the_depth_cap(mocker):
    # Without _MAX_LOOP_DEPTH this recurses forever: the depth>5 branch only terminated
    # because a direct tool always returned.
    llm = mock_chat_llm(mocker, [answer_tool_call() for _ in range(50)])
    mocker.patch.object(
        ToolRegistry, 'execute',
        new=AsyncMock(return_value=(ToolMessage(tool_call_id='tc1', content='fail'),
                                    ToolFailure('всегда падает'))),
    )
    mock_error = mocker.patch('src.characters.character.logger.error')

    await make_character().respond(make_replier(), make_user_message(), last_messages=[])

    assert llm.ainvoke.call_count == _MAX_LOOP_DEPTH
    assert 'hard depth cap hit' in mock_error.call_args[0][0]


async def test_failed_direct_tool_falls_through_to_the_next_in_the_batch(mocker):
    # Two direct tools in one response: the first fails, so the loop keeps going and the
    # second delivers. The turn ends there and is still tagged as a multi-direct answer,
    # because one of them did answer.
    both_calls = AIMessage(content='', tool_calls=[
        {'id': 'tc1', 'name': 'answer_text', 'args': {'text': 'hi'}, 'type': 'tool_call'},
        {'id': 'tc2', 'name': 'set_reaction', 'args': {'emoji': '🤡'}, 'type': 'tool_call'},
    ])
    llm = mock_chat_llm(mocker, [both_calls, answer_tool_call()])
    mock_execute = mocker.patch.object(
        ToolRegistry, 'execute', new=execute_returning(ToolFailure('boom'), None),
    )

    await make_character().respond(make_replier(), make_user_message(), last_messages=[])

    assert mock_execute.call_count == 2
    assert llm.ainvoke.call_count == 1


async def test_context_tool_result_is_unaffected_by_the_tuple_return(mocker):
    llm = mock_chat_llm(mocker, [search_tool_call(), answer_tool_call()])
    mocker.patch.object(ToolRegistry, 'execute', new=execute_returning([], None))

    await make_character().respond(make_replier(), make_user_message(), last_messages=[])

    assert llm.ainvoke.call_count == 2
    msgs = llm.ainvoke.call_args_list[-1][0][0]
    assert isinstance(msgs[3], ToolMessage)


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
