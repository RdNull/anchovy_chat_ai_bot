from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from src.memory.repository import get_last_memory, save_memory
from src.messages.repository import (
    get_history, save_message, register_chat,
)
from src.models import (
    Decision, Fact, Message, OpenLoop, ParticipantInfo,
    StructuredMemory, Topic, UserRole,
)


async def test_save_and_get_last_memory():
    memory = StructuredMemory(
        facts=[Fact(text='the sky is blue', confidence=0.9)],
        decisions=[Decision(text='use Python', status='done')],
        topics=[Topic(name='testing', status='active')],
        open_loops=[OpenLoop(text='add more tests', priority='high')],
        participants={'alice': ParticipantInfo(facts=['likes coffee'])},
        constraints=['no mocking DB'],
        preferences=['fast tests'],
    )
    await save_memory(1, memory)

    result = await get_last_memory(1)
    assert result.chat_id == 1
    assert isinstance(result.created_at, datetime)
    assert result.content.facts[0].text == 'the sky is blue'
    assert result.content.facts[0].confidence == 0.9
    assert result.content.decisions[0].text == 'use Python'
    assert result.content.decisions[0].status == 'done'
    assert result.content.topics[0].name == 'testing'
    assert result.content.open_loops[0].text == 'add more tests'
    assert result.content.open_loops[0].priority == 'high'
    assert result.content.participants['alice'].facts == ['likes coffee']
    assert result.content.constraints == ['no mocking DB']
    assert result.content.preferences == ['fast tests']


async def test_get_last_memory_empty():
    result = await get_last_memory(1)
    assert result is None


async def test_get_last_memory_returns_newest():
    await save_memory(1, StructuredMemory(constraints=['first']))
    await save_memory(1, StructuredMemory(constraints=['second']))

    result = await get_last_memory(1)
    assert result.content.constraints == ['second']


async def test_save_message_db_error(mocker):
    # Mocking mongo.messages in src.messages.history
    mock_mongo = mocker.patch("src.messages.repository.mongo")
    mock_mongo.messages.insert_one = AsyncMock(side_effect=Exception("DB error"))
    mock_logger = mocker.patch("src.messages.repository.logger")

    msg = Message(chat_id=123, nickname="n", role=UserRole.USER, text="t")
    # repository.py doesn't have a try-except for insert_one, so it will raise
    with pytest.raises(Exception, match="DB error"):
        await save_message(msg)


async def test_get_history_db_error(mocker):
    mock_mongo = mocker.patch("src.messages.repository.mongo")
    mock_mongo.messages.find.side_effect = Exception("DB find error")
    mock_logger = mocker.patch("src.messages.repository.logger")

    with pytest.raises(Exception, match="DB find error"):
        await get_history(123)


async def test_register_chat_db_error(mocker):
    mock_mongo = mocker.patch("src.messages.repository.mongo")
    mock_mongo.chats.update_one = AsyncMock(side_effect=Exception("DB update error"))
    mock_logger = mocker.patch("src.messages.repository.logger")

    with pytest.raises(Exception, match="DB update error"):
        await register_chat(123)
