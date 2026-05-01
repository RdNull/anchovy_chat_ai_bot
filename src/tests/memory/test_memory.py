from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from src.memory.models import ChatState, ParticipantInfo, RecentItem, StructuredMemory
from src.memory.repository import get_last_memory, save_memory
from src.messages.repository import get_messages, register_chat, save_message
from src.models import Message, UserRole


async def test_save_and_get_last_memory():
    memory = StructuredMemory(
        participants={
            '@alice': ParticipantInfo(
                traits=['likes coffee', 'punctual'],
                recent=[RecentItem(text='talked about testing', last_seen_at='26-05-01 10:00')],
            )
        },
        state=ChatState(
            active_topics=['testing', 'deployment'],
            open_questions=['when is the release?'],
            running_jokes=['the server is always down'],
        ),
    )
    await save_memory(1, memory)

    result = await get_last_memory(1)
    assert result.chat_id == 1
    assert isinstance(result.created_at, datetime)
    assert result.content.participants['@alice'].traits == ['likes coffee', 'punctual']
    assert result.content.participants['@alice'].recent[0].text == 'talked about testing'
    assert result.content.participants['@alice'].recent[0].last_seen_at == '26-05-01 10:00'
    assert result.content.state.active_topics == ['testing', 'deployment']
    assert result.content.state.open_questions == ['when is the release?']
    assert result.content.state.running_jokes == ['the server is always down']


async def test_get_last_memory_empty():
    result = await get_last_memory(1)
    assert result is None


async def test_get_last_memory_returns_newest():
    await save_memory(1, StructuredMemory(state=ChatState(active_topics=['first'])))
    await save_memory(1, StructuredMemory(state=ChatState(active_topics=['second'])))

    result = await get_last_memory(1)
    assert result.content.state.active_topics == ['second']


async def test_save_and_get_empty_memory():
    memory = StructuredMemory()
    await save_memory(1, memory)

    result = await get_last_memory(1)
    assert result.content.participants == {}
    assert result.content.state.active_topics == []
    assert result.content.state.open_questions == []
    assert result.content.state.running_jokes == []


async def test_save_message_db_error(mocker):
    mock_mongo = mocker.patch('src.messages.repository.mongo')
    mock_mongo.messages.insert_one = AsyncMock(side_effect=Exception('DB error'))
    mocker.patch('src.messages.repository.logger')

    msg = Message(chat_id=123, nickname='n', role=UserRole.USER, text='t')
    with pytest.raises(Exception, match='DB error'):
        await save_message(msg)


async def test_get_messages_db_error(mocker):
    mock_mongo = mocker.patch('src.messages.repository.mongo')
    mock_mongo.messages.find.side_effect = Exception('DB find error')
    mocker.patch('src.messages.repository.logger')

    with pytest.raises(Exception, match='DB find error'):
        await get_messages(123)


async def test_register_chat_db_error(mocker):
    mock_mongo = mocker.patch('src.messages.repository.mongo')
    mock_mongo.chats.update_one = AsyncMock(side_effect=Exception('DB update error'))
    mocker.patch('src.messages.repository.logger')

    with pytest.raises(Exception, match='DB update error'):
        await register_chat(123)
