import base64
import io
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.messages.media import (
    create_media_description, get_media_description_by_media_id, handle_media_message,
)
from src.messages.media.download import _parse_animation_file, _parse_image_file, get_message_media
from src.messages.media.pipeline import _generate_media_description
from src.models import (
    AnimationDetectionData, ImageDetectionData, MediaDescriptionData, MediaDetectionData,
    Message, MessageMedia, MessageMediaStatus, MessageMediaTypes, UserRole,
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
    # Mock get_message_media
    mocker.patch('src.messages.media.pipeline.get_message_media', return_value=ImageDetectionData(
        content='base64content',
        format='jpg'
    ))

    # Mock _generate_media_description
    mocker.patch(
        'src.messages.media.pipeline._generate_media_description',
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

    mocker.patch('src.messages.media.pipeline.get_message_media', return_value=ImageDetectionData(
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
    mock_gen = mocker.patch('src.messages.media.pipeline._generate_media_description')

    await handle_media_message(sample_message, mock_context)

    assert mock_gen.call_count == 0


async def test_handle_media_message_skips_when_no_unique_id(mock_context):
    message = Message(
        chat_id=123,
        nickname='testuser',
        role=UserRole.USER,
        media=MessageMedia(
            media_id='file_id_123',
            unique_id='',
            type=MessageMediaTypes.IMAGE,
            status=MessageMediaStatus.PENDING,
        )
    )
    # Should return early without error
    await handle_media_message(message, mock_context)
    assert mock_context.bot.get_file.call_count == 0


async def test_handle_media_message_skips_when_status_ready(mock_context):
    message = Message(
        chat_id=123,
        nickname='testuser',
        role=UserRole.USER,
        media=MessageMedia(
            media_id='file_id_123',
            unique_id='unique_id_ready',
            type=MessageMediaTypes.IMAGE,
            status=MessageMediaStatus.READY,
        )
    )
    await handle_media_message(message, mock_context)
    assert mock_context.bot.get_file.call_count == 0


async def test_handle_media_message_generate_returns_none(mocker, sample_message, mock_context):
    mocker.patch('src.messages.media.pipeline.get_message_media', return_value=ImageDetectionData(
        content='base64content',
        format='jpg'
    ))
    mocker.patch('src.messages.media.pipeline._generate_media_description', return_value=None)

    # Should complete without raising; description record is created but not finalised
    await handle_media_message(sample_message, mock_context)

    desc = await get_media_description_by_media_id('unique_id_123')
    assert desc is not None


# --- _generate_media_description ---

async def test_generate_media_description_image(mocker, sample_message):
    image_data = ImageDetectionData(content='base64content', format='jpg')
    expected = MediaDescriptionData(description='A cat', ocr_text=None)
    mocker.patch('src.messages.media.pipeline.describe_image', return_value=expected)

    result = await _generate_media_description(sample_message, image_data)

    assert result == expected


async def test_generate_media_description_animation(mocker, sample_message):
    animation_data = AnimationDetectionData(content=b'gif_bytes', format='gif')
    expected = MediaDescriptionData(description='Animated cat', ocr_text=None)
    mocker.patch('src.messages.media.pipeline.describe_animation', return_value=expected)

    result = await _generate_media_description(sample_message, animation_data)

    assert result == expected


async def test_generate_media_description_unknown_type(sample_message):
    class UnknownDetectionData(MediaDetectionData):
        format: str = 'xyz'

        @property
        def content_hash(self):
            return 'hash'

    result = await _generate_media_description(sample_message, UnknownDetectionData(format='xyz'))

    assert result is None


# --- get_message_media ---

async def test_get_message_media_image():
    context = MagicMock()
    media_file = MagicMock()
    media_file.file_path = 'photos/file.jpg'
    media_file.download_to_memory = AsyncMock()
    context.bot.get_file = AsyncMock(return_value=media_file)

    raw_bytes = b'fake image bytes'

    async def fill_bytes(buf):
        buf.write(raw_bytes)

    media_file.download_to_memory.side_effect = fill_bytes

    result = await get_message_media('file_id', context)

    assert isinstance(result, ImageDetectionData)
    assert result.format == 'jpg'
    assert result.content == base64.b64encode(raw_bytes).decode('utf-8')


async def test_get_message_media_animation():
    context = MagicMock()
    media_file = MagicMock()
    media_file.file_path = 'animations/file.gif'
    media_file.download_to_memory = AsyncMock()
    context.bot.get_file = AsyncMock(return_value=media_file)

    raw_bytes = b'fake gif bytes'

    async def fill_bytes(buf):
        buf.write(raw_bytes)

    media_file.download_to_memory.side_effect = fill_bytes

    result = await get_message_media('file_id', context)

    assert isinstance(result, AnimationDetectionData)
    assert result.format == 'gif'
    assert result.content == raw_bytes


async def test_get_message_media_unsupported_format():
    context = MagicMock()
    media_file = MagicMock()
    media_file.file_path = 'docs/file.pdf'
    context.bot.get_file = AsyncMock(return_value=media_file)

    result = await get_message_media('file_id', context)

    assert result is None


# --- _parse_image_file ---

def test_parse_image_file():
    raw_bytes = b'image data'
    file_bytes = io.BytesIO(raw_bytes)

    result = _parse_image_file('png', file_bytes)

    assert isinstance(result, ImageDetectionData)
    assert result.format == 'png'
    assert result.content == base64.b64encode(raw_bytes).decode('utf-8')


# --- _parse_animation_file ---

def test_parse_animation_file():
    raw_bytes = b'animation data'
    file_bytes = io.BytesIO(raw_bytes)

    result = _parse_animation_file('tgs', file_bytes)

    assert isinstance(result, AnimationDetectionData)
    assert result.format == 'tgs'
    assert result.content == raw_bytes
