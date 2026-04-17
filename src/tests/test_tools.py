from datetime import datetime
from unittest.mock import AsyncMock, call

from src.characters.tools import get_user_facts, save_user_fact, search_messages
from src.models import Message, RelatedMessagesData, UserFact, UserRole
from src.tools import ToolContext


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
    assert result == [{'score': 0.9, 'messages': 'bob: hello world'}]
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
