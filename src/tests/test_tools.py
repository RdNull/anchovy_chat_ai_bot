from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from langchain_core.messages import ToolMessage

from src.characters.tools import get_user_facts, search_messages
from src.characters.tools.answer import answer_text, set_reaction
from src.characters.tools.context import search_messages as search_messages_direct
from src.models import Message, RelatedMessagesData, UserFact, UserRole
from src.tools import ToolContext, ToolRegistry


def make_context(chat_id=123):
    return ToolContext(chat_id=chat_id, replier=MagicMock())


async def test_search_messages_tool(mocker):
    context = make_context()
    search_messages.metadata = {'context': context}

    msg = Message(
        chat_id=123,
        nickname='bob',
        role=UserRole.USER,
        text='hello world',
        created_at=datetime.now()
    )

    related = [RelatedMessagesData(messages=[msg], score=0.9)]
    mock_search = mocker.patch(
        'src.characters.tools.context.messages_embeddings_client.search',
        AsyncMock(return_value=related)
    )

    result = await search_messages.ainvoke({'search_query': 'test query', 'limit': 2})

    assert len(result) == 1
    assert result[0]['score'] == 0.9
    assert 'bob: hello world' in result[0]['messages']
    assert mock_search.call_count == 1
    assert mock_search.call_args == call(123, 'test query', limit=2)


async def test_search_messages_tool_limit_validation(mocker):
    context = make_context()
    search_messages.metadata = {'context': context}

    mock_search = mocker.patch(
        'src.characters.tools.context.messages_embeddings_client.search',
        AsyncMock(return_value=[])
    )

    await search_messages.ainvoke({'search_query': 'test', 'limit': 10})
    assert mock_search.call_args == call(123, 'test', limit=3)

    await search_messages.ainvoke({'search_query': 'test', 'limit': -1})
    assert mock_search.call_args == call(123, 'test', limit=3)


async def test_get_user_facts_tool(mocker):
    facts = [
        UserFact(nickname='bob', text='likes pizza', confidence=0.9),
        UserFact(nickname='bob', text='is tall', confidence=0.7)
    ]
    mock_get = mocker.patch(
        'src.characters.tools.context.get_facts',
        AsyncMock(return_value=facts)
    )

    result = await get_user_facts.ainvoke({'nickname': '@bob', 'limit': 10})

    assert result == [
        {'text': 'likes pizza', 'confidence': 0.9},
        {'text': 'is tall', 'confidence': 0.7}
    ]
    assert mock_get.call_count == 1
    assert mock_get.call_args == call('bob', limit=10)


async def test_get_user_facts_tool_limit_validation(mocker):
    mock_get = mocker.patch('src.characters.tools.context.get_facts', AsyncMock(return_value=[]))

    await get_user_facts.ainvoke({'nickname': 'bob', 'limit': 25})
    assert mock_get.call_args == call('bob', limit=5)

    await get_user_facts.ainvoke({'nickname': 'bob', 'limit': -1})
    assert mock_get.call_args == call('bob', limit=5)


async def test_tool_registry_execute_success(mocker):
    mock_tool = MagicMock()
    mock_tool.name = 'test_tool'
    mock_tool.ainvoke = AsyncMock(return_value='tool result')

    context = make_context()
    registry = ToolRegistry(context_tools=[mock_tool], direct_tools=[], context=context)

    tool_call = {
        'name': 'test_tool',
        'args': {'arg1': 'val1'},
        'id': 'call_123'
    }

    result = await registry.execute(tool_call)

    assert isinstance(result, ToolMessage)
    assert result.content == 'tool result'
    assert result.tool_call_id == 'call_123'
    assert mock_tool.metadata == {'context': context}
    assert mock_tool.ainvoke.call_count == 1
    assert mock_tool.ainvoke.call_args == call({'arg1': 'val1'})


async def test_tool_registry_execute_unknown_tool():
    context = make_context()
    registry = ToolRegistry(context_tools=[], direct_tools=[], context=context)

    tool_call = {
        'name': 'unknown_tool',
        'args': {},
        'id': 'call_456'
    }

    with pytest.raises(ValueError, match='Unknown tool: unknown_tool'):
        await registry.execute(tool_call)


async def test_tool_registry_execute_logging(mocker):
    mock_tool = MagicMock()
    mock_tool.name = 'test_tool'
    mock_tool.ainvoke = AsyncMock(return_value='res')

    mock_logger = mocker.patch('src.tools.logger')

    context = make_context()
    registry = ToolRegistry(context_tools=[mock_tool], direct_tools=[], context=context)

    tool_call = {
        'name': 'test_tool',
        'args': {'p': 1},
        'id': 'id1'
    }

    await registry.execute(tool_call)

    assert mock_logger.info.call_count == 1
    assert "Executing tool: test_tool with arguments: {'p': 1}" in mock_logger.info.call_args[0][0]


async def test_tool_registry_is_return_direct_for_direct_tool():
    context = make_context()
    registry = ToolRegistry(context_tools=[], direct_tools=[answer_text], context=context)

    tool_call = {'name': 'answer_text', 'args': {'text': 'hi'}, 'id': 'tc1'}
    assert registry.is_return_direct(tool_call) is True


async def test_tool_registry_is_return_direct_for_context_tool():
    context = make_context()
    registry = ToolRegistry(context_tools=[search_messages_direct], direct_tools=[],
                            context=context)

    tool_call = {'name': 'search_messages', 'args': {'search_query': 'x', 'limit': 1}, 'id': 'tc2'}
    assert registry.is_return_direct(tool_call) is False


async def test_answer_text_tool_calls_replier():
    mock_replier = MagicMock()
    mock_replier.reply_message = AsyncMock()
    context = ToolContext(chat_id=1, replier=mock_replier)
    answer_text.metadata = {'context': context}

    await answer_text.ainvoke({'text': 'hello'})

    assert mock_replier.reply_message.call_count == 1
    assert mock_replier.reply_message.call_args[0][0] == 'hello'


async def test_answer_text_tool_skips_empty_text():
    mock_replier = MagicMock()
    mock_replier.reply_message = AsyncMock()
    context = ToolContext(chat_id=1, replier=mock_replier)
    answer_text.metadata = {'context': context}

    await answer_text.ainvoke({'text': ''})

    assert mock_replier.reply_message.call_count == 0


async def test_set_reaction_tool_calls_replier():
    mock_replier = MagicMock()
    mock_replier.reply_reaction = AsyncMock()
    context = ToolContext(chat_id=1, replier=mock_replier)
    set_reaction.metadata = {'context': context}

    await set_reaction.ainvoke({'emoji': '🤡'})

    assert mock_replier.reply_reaction.call_count == 1
    assert mock_replier.reply_reaction.call_args[0][0] == '🤡'
    assert mock_replier.reply_reaction.call_args[1]['is_big'] is True
