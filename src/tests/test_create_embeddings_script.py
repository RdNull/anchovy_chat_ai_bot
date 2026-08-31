from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from src.messages.media import create_media_description
from src.models import Message, MessageMediaStatus, MessageMediaTypes, UserRole
from src.scripts.create_embeddings import create_embeddings
from src.scripts.create_sticker_embeddings import create_sticker_embeddings


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


# --- create_sticker_embeddings ---

async def seed_sticker(unique_id, is_sticker=True, status=MessageMediaStatus.READY):
    await create_media_description(
        media_id=unique_id,
        type=MessageMediaTypes.IMAGE,
        status=status,
        description='кот танцует',
        is_sticker=is_sticker,
        sticker_emoji='🔥',
    )


async def test_create_sticker_embeddings_only_ready_stickers(mocker):
    await seed_sticker('ready_sticker')
    await seed_sticker('pending_sticker', status=MessageMediaStatus.PENDING)
    await seed_sticker('ready_photo', is_sticker=False)

    mock_save = mocker.patch(
        'src.scripts.create_sticker_embeddings.stickers_embedding_client.save_sticker'
    )

    await create_sticker_embeddings(batch_size=10)

    assert mock_save.call_count == 1
    assert mock_save.call_args[0][0].media_id == 'ready_sticker'


async def test_create_sticker_embeddings_rerun_upserts_one_point(mocker):
    # The point id is derived from unique_id, so a second run overwrites rather than
    # duplicating — the bug `create_fact_embeddings.py` has with uuid4().
    await seed_sticker('ready_sticker')

    qdrant = MagicMock(collection_exists=AsyncMock(return_value=True), upsert=AsyncMock())
    mocker.patch(
        'src.scripts.create_sticker_embeddings.stickers_embedding_client.qdrant_client',
        qdrant,
    )
    mocker.patch(
        'src.scripts.create_sticker_embeddings.stickers_embedding_client._get_embedding_vectors',
        AsyncMock(return_value=[0.1] * 8),
    )

    await create_sticker_embeddings(batch_size=10)
    await create_sticker_embeddings(batch_size=10)

    assert qdrant.upsert.call_count == 2
    first = qdrant.upsert.call_args_list[0][1]['points'][0]
    second = qdrant.upsert.call_args_list[1][1]['points'][0]
    assert first.id == second.id


async def test_create_sticker_embeddings_empty_corpus(mocker):
    mock_save = mocker.patch(
        'src.scripts.create_sticker_embeddings.stickers_embedding_client.save_sticker'
    )

    await create_sticker_embeddings(batch_size=10)

    assert mock_save.call_count == 0
