from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, call

import pytest

from src.models import Message, UserRole
from src.scripts.create_embeddings import create_embeddings


@pytest.mark.asyncio
async def test_create_embeddings_loop(mocker):
    chat_id = 999
    start_date = datetime.now(timezone.utc)

    # Mock history returns
    msg1 = Message(
        chat_id=chat_id,
        nickname="u1",
        role=UserRole.USER,
        text="hi",
        created_at=start_date + timedelta(minutes=1)
    )

    # First call returns 20 messages (to trigger loop continuation)
    # Second call returns 0 messages (to stop loop)
    mock_history = mocker.patch("src.scripts.create_embeddings.get_messages", AsyncMock())
    mock_history.side_effect = [
        [msg1] * 20,
        []
    ]

    mock_embeddings = mocker.patch(
        "src.scripts.create_embeddings.messages_embeddings_client.save"
    )
    mock_save_task = mocker.patch("src.scripts.create_embeddings.save_embedding_task")

    await create_embeddings(chat_id, start_date)

    assert mock_history.call_count == 2
    assert mock_embeddings.call_count == 1
    assert mock_save_task.call_count == 1
    assert mock_save_task.call_args == call(chat_id, msg1.created_at)


@pytest.mark.asyncio
async def test_create_embeddings_empty_history(mocker):
    mock_history = mocker.patch(
        "src.scripts.create_embeddings.get_messages",
        AsyncMock(return_value=[])
    )
    mock_embeddings = mocker.patch(
        "src.scripts.create_embeddings.messages_embeddings_client.save"
    )

    await create_embeddings(123, datetime.now(timezone.utc))

    assert mock_history.call_count == 1
    assert mock_embeddings.call_count == 0
