from unittest.mock import AsyncMock, MagicMock

import pytest

from src.messages.media import (
    create_media_description, get_media_description_by_media_id,
    handle_media_message,
)
from src.models import (
    ImageDetectionData, MediaDescriptionData, Message, MessageMedia, MessageMediaStatus,
    MessageMediaTypes, UserRole,
)


@pytest.fixture
def mock_context():
    context = MagicMock()
    context.bot.get_file = AsyncMock()
    return context


@pytest.fixture
def sample_message():
    return Message(
        chat_id=123,
        nickname='testuser',
        role=UserRole.USER,
        media=MessageMedia(
            media_id='file_id_123',
            unique_id='unique_id_123',
            type=MessageMediaTypes.IMAGE,
            status=MessageMediaStatus.PENDING
        )
    )


async def test_handle_media_message_new_image(mocker, sample_message, mock_context):
    # Mock _get_message_media
    mocker.patch('src.messages.media._get_message_media', return_value=ImageDetectionData(
        content='base64content',
        format='jpg'
    ))

    # Mock _generate_media_description
    mocker.patch(
        'src.messages.media._generate_media_description',
        return_value=MediaDescriptionData(
            description='A cute cat',
            ocr_text='CAT'
        )
    )

    # Run
    await handle_media_message(sample_message, mock_context)

    # Verify it was saved to DB
    desc = await get_media_description_by_media_id('unique_id_123')
    assert desc is not None
    assert desc.description == 'A cute cat'
    assert desc.ocr_text == 'CAT'
    assert desc.status == MessageMediaStatus.READY.value
    assert desc.media_id == 'unique_id_123'
    assert desc.type == MessageMediaTypes.IMAGE.value


async def test_handle_media_message_cache_hit_by_id(mocker, sample_message, mock_context):
    # Pre-create a description
    await create_media_description(
        media_id='unique_id_123',
        description='Cached description',
        status=MessageMediaStatus.READY
    )

    mock = mocker.patch.object(mock_context.bot, 'get_file')

    await handle_media_message(sample_message, mock_context)

    # get_file should NOT be called because it's in cache
    assert mock.call_count == 0


async def test_handle_media_message_cache_hit_by_hash(mocker, sample_message, mock_context):
    content_hash = 'some_hash'
    # Pre-create a description with same hash but different media_id

    await create_media_description(
        media_id='other_unique_id',
        content_hash=content_hash,
        description='Hash-cached description',
        status=MessageMediaStatus.READY
    )

    mocker.patch('src.messages.media._get_message_media', return_value=ImageDetectionData(
        content='base64content',
        format='jpg'
    ))

    # Mocking content_hash for the detection data
    mocker.patch(
        'src.models.ImageDetectionData.content_hash',
        new_callable=mocker.PropertyMock,
        return_value=content_hash
    )

    # Mock _generate_media_description to ensure it's NOT called
    mock_gen = mocker.patch('src.messages.media._generate_media_description')

    await handle_media_message(sample_message, mock_context)

    assert mock_gen.call_count == 0
