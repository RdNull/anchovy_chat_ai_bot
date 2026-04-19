from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from langchain_core.messages import ToolMessage

from src.characters.tools import get_user_facts, save_user_fact, search_messages
from src.models import Message, RelatedMessagesData, UserFact, UserRole
from src.tools import ToolContext, ToolRegistry


async def test_search_messages_tool(mocker):
    # Setup context
    context = ToolContext(chat_id=123)
    search_messages.metadata = {'context': context}

    # Mock message
    msg = Message(
        chat_id=123,
        nickname='bob',
        role=UserRole.USER,
        text='hello world',
        created_at=datetime.now()
    )

    related = [RelatedMessagesData(messages=[msg], score=0.9)]
    mock_search = mocker.patch(
        'src.characters.tools.messages_embeddings_client.search',
        AsyncMock(return_value=related)
    )

    # Execute
    result = await search_messages.ainvoke({'search_query': 'test query', 'limit': 2})

    # Assert
    assert len(result) == 1
    assert result[0]['score'] == 0.9
    assert 'bob: hello world' in result[0]['messages']
    assert mock_search.call_count == 1
    assert mock_search.call_args == call(123, 'test query', limit=2)


async def test_search_messages_tool_limit_validation(mocker):
    context = ToolContext(chat_id=123)
    search_messages.metadata = {'context': context}

    mock_search = mocker.patch(
        'src.characters.tools.messages_embeddings_client.search',
        AsyncMock(return_value=[])
    )

    # Test high limit
    await search_messages.ainvoke({'search_query': 'test', 'limit': 10})
    assert mock_search.call_args == call(123, 'test', limit=3)

    # Test low limit
    await search_messages.ainvoke({'search_query': 'test', 'limit': -1})
    assert mock_search.call_args == call(123, 'test', limit=3)


async def test_save_user_fact_tool(mocker):
    mock_save = mocker.patch(
        'src.characters.tools.save_fact',
        AsyncMock(return_value=UserFact(nickname='bob', text='likes pizza', confidence=0.8))
    )

    # Execute with @ nickname
    result = await save_user_fact.ainvoke(
        {'nickname': '@bob', 'text': 'likes pizza', 'confidence': 0.8}
    )

    # Assert
    assert result.nickname == 'bob'
    assert result.text == 'likes pizza'
    assert result.confidence == 0.8
    assert mock_save.call_count == 1
    assert mock_save.call_args == call('bob', 'likes pizza', 0.8)


async def test_save_user_fact_tool_invalid_confidence(mocker):
    mock_save = mocker.patch('src.characters.tools.save_fact', AsyncMock())

    # Confidence too low
    result = await save_user_fact.ainvoke({'nickname': 'bob', 'text': 'test', 'confidence': 0.4})
    assert result is None
    assert mock_save.call_count == 0

    # Confidence too high
    result = await save_user_fact.ainvoke({'nickname': 'bob', 'text': 'test', 'confidence': 1.1})
    assert result is None
    assert mock_save.call_count == 0


async def test_get_user_facts_tool(mocker):
    facts = [
        UserFact(nickname='bob', text='likes pizza', confidence=0.9),
        UserFact(nickname='bob', text='is tall', confidence=0.7)
    ]
    mock_get = mocker.patch(
        'src.characters.tools.get_facts',
        AsyncMock(return_value=facts)
    )

    # Execute
    result = await get_user_facts.ainvoke({'nickname': '@bob', 'limit': 10})

    # Assert
    assert result == [
        {'text': 'likes pizza', 'confidence': 0.9},
        {'text': 'is tall', 'confidence': 0.7}
    ]
    assert mock_get.call_count == 1
    assert mock_get.call_args == call('bob', limit=10)


async def test_get_user_facts_tool_limit_validation(mocker):
    mock_get = mocker.patch('src.characters.tools.get_facts', AsyncMock(return_value=[]))

    # High limit
    await get_user_facts.ainvoke({'nickname': 'bob', 'limit': 25})
    assert mock_get.call_args == call('bob', limit=5)

    # Low limit
    await get_user_facts.ainvoke({'nickname': 'bob', 'limit': -1})
    assert mock_get.call_args == call('bob', limit=5)


async def test_tool_registry_execute_success(mocker):
    # Setup
    mock_tool = MagicMock()
    mock_tool.name = "test_tool"
    mock_tool.ainvoke = AsyncMock(return_value="tool result")

    context = ToolContext(chat_id=123)
    registry = ToolRegistry(tools=[mock_tool], context=context)

    tool_call = {
        "name": "test_tool",
        "args": {"arg1": "val1"},
        "id": "call_123"
    }

    # Execute
    result = await registry.execute(tool_call)

    # Assert
    assert isinstance(result, ToolMessage)
    assert result.content == "tool result"
    assert result.tool_call_id == "call_123"
    assert mock_tool.metadata == {'context': context}
    assert mock_tool.ainvoke.call_count == 1
    assert mock_tool.ainvoke.call_args == call({"arg1": "val1"})


async def test_tool_registry_execute_unknown_tool():
    # Setup
    context = ToolContext(chat_id=123)
    registry = ToolRegistry(tools=[], context=context)

    tool_call = {
        "name": "unknown_tool",
        "args": {},
        "id": "call_456"
    }

    # Execute & Assert
    with pytest.raises(ValueError, match="Unknown tool: unknown_tool"):
        await registry.execute(tool_call)


async def test_tool_registry_execute_logging(mocker):
    # Setup
    mock_tool = MagicMock()
    mock_tool.name = "test_tool"
    mock_tool.ainvoke = AsyncMock(return_value="res")

    mock_logger = mocker.patch("src.tools.logger")

    context = ToolContext(chat_id=123)
    registry = ToolRegistry(tools=[mock_tool], context=context)

    tool_call = {
        "name": "test_tool",
        "args": {"p": 1},
        "id": "id1"
    }

    # Execute
    await registry.execute(tool_call)

    # Assert
    assert mock_logger.info.call_count == 1
    assert "Executing tool: test_tool with arguments: {'p': 1}" in mock_logger.info.call_args[0][0]
