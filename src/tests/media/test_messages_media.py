import base64
import io
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Sticker

from src.messages.media import (
    create_media_description, get_media_description_by_media_id, handle_media_message,
)
from src.messages.repository import get_message_media_data
from src.mongo import media_descriptions
from src.messages.media.download import _parse_animation_file, _parse_image_file, get_message_media
from src.messages.media.pipeline import _generate_media_description, wait_for_media_ready
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


# --- sticker metadata persistence ---

async def test_create_media_description_persists_sticker_fields():
    created = await create_media_description(
        media_id='sticker_uid',
        type=MessageMediaTypes.IMAGE,
        is_sticker=True,
        sticker_emoji='🔥',
        sticker_set='hotpack',
    )

    assert created.is_sticker is True
    assert created.sticker_emoji == '🔥'
    assert created.sticker_set == 'hotpack'

    read_back = await get_media_description_by_media_id('sticker_uid')
    assert read_back.is_sticker is True
    assert read_back.sticker_emoji == '🔥'
    assert read_back.sticker_set == 'hotpack'


async def test_parse_media_description_on_legacy_row_without_sticker_fields():
    # Every row written before this unit has none of the three keys.
    await media_descriptions.insert_one({
        'hash': None,
        'description': 'a cat',
        'ocr_text': None,
        'media_id': 'legacy_uid',
        'type': MessageMediaTypes.IMAGE.value,
        'status': MessageMediaStatus.READY.value,
    })

    parsed = await get_media_description_by_media_id('legacy_uid')

    assert parsed.is_sticker is False
    assert parsed.sticker_emoji is None
    assert parsed.sticker_set is None


async def test_get_message_media_data_hydrates_sticker_flag_from_stored_row():
    # `_parse_media` reads history back with no PTB object, so without hydration every
    # persisted sticker would come back looking like a photo.
    await create_media_description(
        media_id='sticker_uid',
        type=MessageMediaTypes.IMAGE,
        status=MessageMediaStatus.READY,
        description='a dancing cat',
        is_sticker=True,
        sticker_emoji='💃',
        sticker_set='cats',
    )

    media = await get_message_media_data('sendable_fid', 'sticker_uid', None)

    assert media.is_sticker is True
    assert media.sticker_emoji == '💃'
    assert media.sticker_set == 'cats'


async def test_get_message_media_data_live_parse_wins_over_stored_row():
    await create_media_description(
        media_id='sticker_uid',
        type=MessageMediaTypes.IMAGE,
        status=MessageMediaStatus.READY,
        description='a dancing cat',
        is_sticker=True,
        sticker_emoji='stale',
        sticker_set='stale_pack',
    )
    sticker = Sticker(
        file_id='sendable_fid',
        file_unique_id='sticker_uid',
        width=512,
        height=512,
        is_animated=False,
        is_video=False,
        type=Sticker.REGULAR,
        emoji='fresh',
        set_name='fresh_pack',
    )

    media = await get_message_media_data('sendable_fid', 'sticker_uid', sticker)

    assert media.sticker_emoji == 'fresh'
    assert media.sticker_set == 'fresh_pack'


async def test_handle_media_message_passes_sticker_fields_through(mocker, mock_context):
    mocker.patch('src.messages.media.pipeline.get_message_media', return_value=ImageDetectionData(
        content='base64content',
        format='webp',
    ))
    mocker.patch(
        'src.messages.media.pipeline._generate_media_description',
        return_value=MediaDescriptionData(description='a dancing cat', ocr_text=None),
    )
    message = Message(
        chat_id=123,
        nickname='testuser',
        role=UserRole.USER,
        media=MessageMedia(
            media_id='sendable_fid',
            unique_id='sticker_uid',
            type=MessageMediaTypes.IMAGE,
            status=MessageMediaStatus.PENDING,
            is_sticker=True,
            sticker_emoji='💃',
            sticker_set='cats',
        ),
    )

    await handle_media_message(message, mock_context)

    stored = await get_media_description_by_media_id('sticker_uid')
    assert stored.is_sticker is True
    assert stored.sticker_emoji == '💃'
    assert stored.sticker_set == 'cats'


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


# --- wait_for_media_ready ---

async def test_wait_for_media_ready_empty_list(mocker):
    mock_get = mocker.patch('src.messages.media.pipeline.get_media_description_by_media_id')

    await wait_for_media_ready([], timeout=5.0)

    assert mock_get.call_count == 0


async def test_wait_for_media_ready_already_finished(mocker):
    ready_desc = MagicMock()
    ready_desc.status.is_finished = True
    mocker.patch(
        'src.messages.media.pipeline.get_media_description_by_media_id',
        return_value=ready_desc,
    )
    mock_sleep = mocker.patch('src.messages.media.pipeline.asyncio.sleep', new_callable=AsyncMock)

    await wait_for_media_ready(['uid1'], timeout=5.0)

    assert mock_sleep.call_count == 0


async def test_wait_for_media_ready_polls_until_ready(mocker):
    pending_desc = MagicMock()
    pending_desc.status.is_finished = False
    ready_desc = MagicMock()
    ready_desc.status.is_finished = True
    mocker.patch(
        'src.messages.media.pipeline.get_media_description_by_media_id',
        side_effect=[pending_desc, ready_desc],
    )
    mock_sleep = mocker.patch('src.messages.media.pipeline.asyncio.sleep', new_callable=AsyncMock)

    await wait_for_media_ready(['uid1'], timeout=5.0)

    assert mock_sleep.call_count == 1


async def test_wait_for_media_ready_treats_none_as_not_ready(mocker):
    ready_desc = MagicMock()
    ready_desc.status.is_finished = True
    mocker.patch(
        'src.messages.media.pipeline.get_media_description_by_media_id',
        side_effect=[None, ready_desc],
    )
    mock_sleep = mocker.patch('src.messages.media.pipeline.asyncio.sleep', new_callable=AsyncMock)

    await wait_for_media_ready(['uid1'], timeout=5.0)

    assert mock_sleep.call_count == 1


async def test_wait_for_media_ready_times_out(mocker):
    mock_get = mocker.patch('src.messages.media.pipeline.get_media_description_by_media_id')
    mock_sleep = mocker.patch('src.messages.media.pipeline.asyncio.sleep', new_callable=AsyncMock)
    mock_logger = mocker.patch('src.messages.media.pipeline.logger')

    await wait_for_media_ready(['uid1'], timeout=-1.0)

    assert mock_logger.warning.call_count == 1
    assert mock_get.call_count == 0
    assert mock_sleep.call_count == 0


async def test_wait_for_media_ready_multiple_ids_waits_for_all(mocker):
    pending_desc = MagicMock()
    pending_desc.status.is_finished = False
    ready_desc = MagicMock()
    ready_desc.status.is_finished = True

    results = {'uid1': [ready_desc], 'uid2': [pending_desc, ready_desc]}

    def get_by_uid(uid):
        return results[uid].pop(0)

    mocker.patch(
        'src.messages.media.pipeline.get_media_description_by_media_id',
        side_effect=get_by_uid,
    )
    mock_sleep = mocker.patch('src.messages.media.pipeline.asyncio.sleep', new_callable=AsyncMock)

    await wait_for_media_ready(['uid1', 'uid2'], timeout=5.0)

    assert mock_sleep.call_count == 1
