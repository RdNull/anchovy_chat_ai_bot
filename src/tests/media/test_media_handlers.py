import asyncio
import base64
import io
from datetime import datetime, timedelta, UTC
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

from src import settings
from src.media import (
    create_media_description,
    get_media_description_by_media_id,
    get_recent_sticker_ids,
    get_sendable_file_id,
    sticker_corpus_size,
    update_media_description_status,
    wait_for_media_ready,
)
from src.media.download import _parse_animation_file, _parse_image_file, get_message_media
from src.media.handlers import (
    _current_describers,
    _generate_media_description,
    handle_media_message,
)
from src.media.repository import parse_media_description
from src.media.processors import animation as animation_processor
from src.media.processors import image as image_processor
from src.media.processors import sticker as sticker_processor
from src.media.models import (
    AnimationDetectionData,
    ImageDetectionData,
    MediaDescriptionData,
    MediaDetectionData,
)
from src.messages.models import (
    Message,
    MessageMedia,
    MessageMediaStatus,
    MessageMediaTypes,
    UserRole,
)
from src.messages.repository import get_message_media_data
from src.mongo import media_descriptions, messages


async def insert_carrier(unique_id, file_id, created_at, chat_id=123, role=UserRole.USER):
    await messages.insert_one({
        'chat_id': chat_id,
        'role': role.value,
        'text': None,
        'nickname': 'someone',
        'media_id': file_id,
        'media_unique_id': unique_id,
        'created_at': created_at,
    })


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
            status=MessageMediaStatus.PENDING,
        ),
    )


async def test_handle_media_message_new_image(mocker, sample_message, mock_context):
    # Mock get_message_media
    mocker.patch(
        'src.media.handlers.get_message_media',
        return_value=ImageDetectionData(content='base64content', format='jpg'),
    )

    # Mock _generate_media_description
    mocker.patch(
        'src.media.handlers._generate_media_description',
        return_value=MediaDescriptionData(description='A cute cat', ocr_text='CAT'),
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
        media_id='unique_id_123', description='Cached description', status=MessageMediaStatus.READY
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
        status=MessageMediaStatus.READY,
    )

    mocker.patch(
        'src.media.handlers.get_message_media',
        return_value=ImageDetectionData(content='base64content', format='jpg'),
    )

    # Mocking content_hash for the detection data
    mocker.patch(
        'src.media.models.ImageDetectionData.content_hash',
        new_callable=mocker.PropertyMock,
        return_value=content_hash,
    )

    # Mock _generate_media_description to ensure it's NOT called
    mock_gen = mocker.patch('src.media.handlers._generate_media_description')

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
        ),
    )
    # Should return early without error
    await handle_media_message(message, mock_context)
    assert mock_context.bot.get_file.call_count == 0


async def test_handle_media_message_generate_returns_none(mocker, sample_message, mock_context):
    mocker.patch(
        'src.media.handlers.get_message_media',
        return_value=ImageDetectionData(content='base64content', format='jpg'),
    )
    mocker.patch('src.media.handlers._generate_media_description', return_value=None)

    # Should complete without raising; description record is created but not finalised
    await handle_media_message(sample_message, mock_context)

    desc = await get_media_description_by_media_id('unique_id_123')
    assert desc is not None


# --- status writes & staleness ---


async def test_update_media_description_status_persists_in_mongo():
    """Regression for the `_id` no-op: every sibling write wraps `ObjectId(...)`,
    this one didn't, so `update_one` matched zero documents and silently no-op'd.
    """
    created = await create_media_description(media_id='uid_status', description='d')

    await update_media_description_status(created.id, MessageMediaStatus.ERROR)

    read_back = await get_media_description_by_media_id('uid_status')
    assert read_back.status == MessageMediaStatus.ERROR


async def test_wait_for_media_ready_stops_once_error_is_written():
    """End-to-end through the real repository: before the `_id` fix this write
    never landed, `is_finished` never became true, and this polled to the deadline
    every time. Real I/O throughout — mocking `asyncio.sleep` here would patch the
    module attribute process-wide and stall Motor's own background monitoring.
    """
    created = await create_media_description(
        media_id='uid_err',
        status=MessageMediaStatus.PROCESSING,
    )
    await update_media_description_status(created.id, MessageMediaStatus.ERROR)

    started = asyncio.get_event_loop().time()
    await wait_for_media_ready(['uid_err'], timeout=5.0)
    elapsed = asyncio.get_event_loop().time() - started

    assert elapsed < 1.0  # resolved on the first check; a real poll sleeps 0.5s


async def test_handle_media_message_skips_a_fresh_processing_row(
    mocker, sample_message, mock_context
):
    await create_media_description(
        media_id='unique_id_123',
        status=MessageMediaStatus.PROCESSING,
    )
    mock_get_media = mocker.patch('src.media.handlers.get_message_media')

    await handle_media_message(sample_message, mock_context)

    assert mock_get_media.call_count == 0


async def test_handle_media_message_retries_a_stale_processing_row(
    mocker,
    sample_message,
    mock_context,
):
    """A crash between the PROCESSING write and the describe call must not leave a
    row that is polled forever — past the staleness window it is retried instead.
    """
    stale = (datetime.now(UTC) - timedelta(minutes=10)).timestamp()
    await media_descriptions.insert_one({
        'hash': None,
        'description': None,
        'ocr_text': None,
        'media_id': 'unique_id_123',
        'type': MessageMediaTypes.IMAGE.value,
        'status': MessageMediaStatus.PROCESSING.value,
        'sticker_emoji': None,
        'updated_at': stale,
    })
    mocker.patch.object(settings, 'MEDIA_PROCESSING_STALE_MINUTES', 5)
    mocker.patch(
        'src.media.handlers.get_message_media',
        return_value=ImageDetectionData(
            content='base64content',
            format='jpg',
        ),
    )
    mocker.patch(
        'src.media.handlers._generate_media_description',
        return_value=MediaDescriptionData(description='retried', ocr_text=None),
    )

    await handle_media_message(sample_message, mock_context)

    desc = await get_media_description_by_media_id('unique_id_123')
    assert desc.status == MessageMediaStatus.READY
    assert desc.description == 'retried'


async def test_handle_media_message_retries_a_processing_row_without_updated_at(
    mocker,
    sample_message,
    mock_context,
):
    """A row written before `updated_at` existed has no way to prove it's fresh, so
    it must be treated as stale rather than polled forever.
    """
    await media_descriptions.insert_one({
        'hash': None,
        'description': None,
        'ocr_text': None,
        'media_id': 'unique_id_123',
        'type': MessageMediaTypes.IMAGE.value,
        'status': MessageMediaStatus.PROCESSING.value,
        'sticker_emoji': None,
    })
    mocker.patch(
        'src.media.handlers.get_message_media',
        return_value=ImageDetectionData(
            content='base64content',
            format='jpg',
        ),
    )
    mocker.patch(
        'src.media.handlers._generate_media_description',
        return_value=MediaDescriptionData(description='retried legacy', ocr_text=None),
    )

    await handle_media_message(sample_message, mock_context)

    desc = await get_media_description_by_media_id('unique_id_123')
    assert desc.status == MessageMediaStatus.READY
    assert desc.description == 'retried legacy'


# --- sticker metadata persistence ---


async def test_create_media_description_persists_the_sticker_type_and_emoji():
    created = await create_media_description(
        media_id='sticker_uid',
        type=MessageMediaTypes.STICKER,
        sticker_emoji='🔥',
    )

    assert created.type == MessageMediaTypes.STICKER
    assert created.sticker_emoji == '🔥'

    read_back = await get_media_description_by_media_id('sticker_uid')
    assert read_back.type == MessageMediaTypes.STICKER
    assert read_back.sticker_emoji == '🔥'


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

    assert parsed.type == MessageMediaTypes.IMAGE
    assert parsed.sticker_emoji is None


async def test_get_message_media_data_reads_the_sticker_type_from_the_row():
    # `_parse_media` reads history back with no PTB object, so the row is the only
    # source: without this every persisted sticker would come back looking like a photo.
    await create_media_description(
        media_id='sticker_uid',
        type=MessageMediaTypes.STICKER,
        status=MessageMediaStatus.READY,
        description='a dancing cat',
        sticker_emoji='💃',
    )

    media = await get_message_media_data('sendable_fid', 'sticker_uid')

    assert media.type == MessageMediaTypes.STICKER
    assert media.sticker_emoji == '💃'


async def test_get_message_media_data_defaults_when_no_row_exists():
    media = await get_message_media_data('sendable_fid', 'never_described')

    assert media.type is None
    assert media.sticker_emoji is None
    assert media.status == MessageMediaStatus.PENDING


def sticker_message():
    return Message(
        chat_id=123,
        nickname='testuser',
        role=UserRole.USER,
        media=MessageMedia(
            media_id='sendable_fid',
            unique_id='sticker_uid',
            type=MessageMediaTypes.STICKER,
            status=MessageMediaStatus.PENDING,
            sticker_emoji='💃',
        ),
    )


async def test_handle_media_message_passes_sticker_fields_through(mocker, mock_context):
    mocker.patch(
        'src.media.handlers.get_message_media',
        return_value=ImageDetectionData(
            content='base64content',
            format='webp',
        ),
    )
    mocker.patch(
        'src.media.handlers._generate_media_description',
        return_value=MediaDescriptionData(description='a dancing cat', ocr_text=None),
    )
    mocker.patch('src.media.handlers.stickers_embedding_client.save_sticker')

    await handle_media_message(sticker_message(), mock_context)

    stored = await get_media_description_by_media_id('sticker_uid')
    assert stored.type == MessageMediaTypes.STICKER
    assert stored.sticker_emoji == '💃'


async def test_handle_media_message_indexes_a_ready_sticker(mocker, mock_context):
    mocker.patch(
        'src.media.handlers.get_message_media',
        return_value=ImageDetectionData(
            content='base64content',
            format='webp',
        ),
    )
    mocker.patch(
        'src.media.handlers._generate_media_description',
        return_value=MediaDescriptionData(description='кот танцует', ocr_text=None),
    )
    mock_save = mocker.patch('src.media.handlers.stickers_embedding_client.save_sticker')
    mocker.patch.object(settings, 'ENABLE_STICKER_REPLIES', False)

    await handle_media_message(sticker_message(), mock_context)

    # Indexing is not gated on the flag: the corpus has to accumulate while it is off.
    assert mock_save.call_count == 1
    assert mock_save.call_args[0][0].media_id == 'sticker_uid'
    assert mock_save.call_args[0][0].status == MessageMediaStatus.READY


async def test_handle_media_message_does_not_index_a_photo(mocker, sample_message, mock_context):
    mocker.patch(
        'src.media.handlers.get_message_media',
        return_value=ImageDetectionData(
            content='base64content',
            format='jpg',
        ),
    )
    mocker.patch(
        'src.media.handlers._generate_media_description',
        return_value=MediaDescriptionData(description='a screenshot', ocr_text=None),
    )
    mock_save = mocker.patch('src.media.handlers.stickers_embedding_client.save_sticker')

    await handle_media_message(sample_message, mock_context)

    assert mock_save.call_count == 0


async def test_handle_media_message_backfills_a_legacy_sticker_row(mocker, mock_context):
    # The row predates the sticker unit, so it is typed `image` and already READY —
    # which means handle_media_message returns early and would never retype it. The
    # backfill runs before that return, so re-sighting a known sticker is what fills
    # the index. Without it the corpus could only grow from never-seen stickers.
    await create_media_description(
        media_id='sticker_uid',
        type=MessageMediaTypes.IMAGE,
        status=MessageMediaStatus.READY,
        description='кот танцует',
    )
    mock_save = mocker.patch('src.media.handlers.stickers_embedding_client.save_sticker')
    mock_download = mocker.patch('src.media.handlers.get_message_media')

    await handle_media_message(sticker_message(), mock_context)

    stored = await get_media_description_by_media_id('sticker_uid')
    assert stored.type == MessageMediaTypes.STICKER
    assert stored.sticker_emoji == '💃'
    assert mock_save.call_count == 1
    # Still an early return: the description was already there, nothing is re-described.
    assert mock_download.call_count == 0


async def test_handle_media_message_backfill_is_once_not_every_sighting(mocker, mock_context):
    await create_media_description(
        media_id='sticker_uid',
        type=MessageMediaTypes.IMAGE,
        status=MessageMediaStatus.READY,
        description='кот танцует',
    )
    mock_save = mocker.patch('src.media.handlers.stickers_embedding_client.save_sticker')
    mocker.patch('src.media.handlers.get_message_media')

    await handle_media_message(sticker_message(), mock_context)
    await handle_media_message(sticker_message(), mock_context)
    await handle_media_message(sticker_message(), mock_context)

    assert mock_save.call_count == 1


async def test_handle_media_message_does_not_backfill_a_photo(mocker, sample_message, mock_context):
    await create_media_description(
        media_id='unique_id_123',
        type=MessageMediaTypes.IMAGE,
        status=MessageMediaStatus.READY,
        description='a screenshot',
    )
    mock_save = mocker.patch('src.media.handlers.stickers_embedding_client.save_sticker')
    mocker.patch('src.media.handlers.get_message_media')

    await handle_media_message(sample_message, mock_context)

    stored = await get_media_description_by_media_id('unique_id_123')
    assert stored.type == MessageMediaTypes.IMAGE
    assert mock_save.call_count == 0


async def test_handle_media_message_backfill_defers_indexing_until_ready(mocker, mock_context):
    # A legacy row that never got described: retype it now, but the end-of-pipeline
    # hook is what indexes it once the description lands.
    await create_media_description(
        media_id='sticker_uid',
        type=MessageMediaTypes.IMAGE,
        status=MessageMediaStatus.PENDING,
    )
    mocker.patch(
        'src.media.handlers.get_message_media',
        return_value=ImageDetectionData(
            content='base64content',
            format='webp',
        ),
    )
    mocker.patch(
        'src.media.handlers._generate_media_description',
        return_value=MediaDescriptionData(description='кот танцует', ocr_text=None),
    )
    mock_save = mocker.patch('src.media.handlers.stickers_embedding_client.save_sticker')

    await handle_media_message(sticker_message(), mock_context)

    stored = await get_media_description_by_media_id('sticker_uid')
    assert stored.type == MessageMediaTypes.STICKER
    assert stored.status == MessageMediaStatus.READY
    assert mock_save.call_count == 1


# --- get_sendable_file_id / corpus helpers ---


async def test_get_sendable_file_id_returns_the_file_id_not_the_unique_id():
    # The point of the whole step: `media_descriptions.media_id` holds a
    # `file_unique_id`, which Telegram refuses in a send. Only `messages.media_id`
    # is sendable.
    await insert_carrier('sticker_uid', 'SENDABLE_FILE_ID', created_at=100.0)

    file_id = await get_sendable_file_id('sticker_uid')

    assert file_id == 'SENDABLE_FILE_ID'
    assert file_id != 'sticker_uid'


async def test_get_sendable_file_id_newest_carrier_wins():
    await insert_carrier('sticker_uid', 'old_file_id', created_at=100.0)
    await insert_carrier('sticker_uid', 'reissued_file_id', created_at=200.0)

    assert await get_sendable_file_id('sticker_uid') == 'reissued_file_id'


async def test_get_sendable_file_id_unknown_id_returns_none():
    assert await get_sendable_file_id('never_seen') is None


async def test_get_recent_sticker_ids_only_bot_messages_with_media():
    await insert_carrier('bot_recent', 'fid1', created_at=300.0, role=UserRole.AI)
    await insert_carrier('user_sent', 'fid2', created_at=200.0, role=UserRole.USER)
    await messages.insert_one({
        'chat_id': 123,
        'role': UserRole.AI.value,
        'text': 'just text',
        'nickname': 'bot',
        'media_id': None,
        'media_unique_id': None,
        'created_at': 250.0,
    })

    recent = await get_recent_sticker_ids(123, limit=10)

    assert recent == {'bot_recent'}


async def test_get_recent_sticker_ids_takes_the_newest_limit():
    for i in range(5):
        await insert_carrier(f'uid{i}', f'fid{i}', created_at=float(i), role=UserRole.AI)

    recent = await get_recent_sticker_ids(123, limit=2)

    assert recent == {'uid4', 'uid3'}


async def test_get_recent_sticker_ids_is_per_chat():
    await insert_carrier('here', 'fid1', created_at=100.0, chat_id=123, role=UserRole.AI)
    await insert_carrier('elsewhere', 'fid2', created_at=200.0, chat_id=999, role=UserRole.AI)

    assert await get_recent_sticker_ids(123, limit=10) == {'here'}


async def test_sticker_corpus_size_counts_only_ready_stickers():
    await create_media_description(
        media_id='ready_sticker',
        type=MessageMediaTypes.STICKER,
        status=MessageMediaStatus.READY,
    )
    await create_media_description(
        media_id='pending_sticker',
        type=MessageMediaTypes.STICKER,
        status=MessageMediaStatus.PENDING,
    )
    await create_media_description(
        media_id='ready_photo',
        type=MessageMediaTypes.IMAGE,
        status=MessageMediaStatus.READY,
    )

    assert await sticker_corpus_size() == 1


# --- _generate_media_description ---


async def test_generate_media_description_image(mocker, sample_message):
    image_data = ImageDetectionData(content='base64content', format='jpg')
    expected = MediaDescriptionData(description='A cat', ocr_text=None)
    mocker.patch('src.media.handlers.describe_image', return_value=expected)

    result = await _generate_media_description(sample_message, image_data, MessageMediaTypes.IMAGE)

    assert result == expected


async def test_generate_media_description_animation(mocker, sample_message):
    animation_data = AnimationDetectionData(content=b'gif_bytes', format='gif')
    expected = MediaDescriptionData(description='Animated cat', ocr_text=None)
    mocker.patch('src.media.handlers.describe_animation', return_value=expected)

    result = await _generate_media_description(
        sample_message, animation_data, MessageMediaTypes.GIF
    )

    assert result == expected


async def test_generate_media_description_unknown_type(sample_message):
    class UnknownDetectionData(MediaDetectionData):
        format: str = 'xyz'

        @property
        def content_hash(self):
            return 'hash'

    unknown = UnknownDetectionData(format='xyz')
    result = await _generate_media_description(sample_message, unknown, MessageMediaTypes.IMAGE)

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
    mock_get = mocker.patch('src.media.repository.get_media_description_by_media_id')

    await wait_for_media_ready([], timeout=5.0)

    assert mock_get.call_count == 0


async def test_wait_for_media_ready_already_finished(mocker):
    ready_desc = MagicMock()
    ready_desc.status.is_finished = True
    mocker.patch(
        'src.media.repository.get_media_description_by_media_id',
        return_value=ready_desc,
    )
    mock_sleep = mocker.patch(
        'src.media.repository.asyncio.sleep',
        new_callable=AsyncMock,
    )

    await wait_for_media_ready(['uid1'], timeout=5.0)

    assert mock_sleep.call_count == 0


async def test_wait_for_media_ready_polls_until_ready(mocker):
    pending_desc = MagicMock()
    pending_desc.status.is_finished = False
    ready_desc = MagicMock()
    ready_desc.status.is_finished = True
    mocker.patch(
        'src.media.repository.get_media_description_by_media_id',
        side_effect=[pending_desc, ready_desc],
    )
    mock_sleep = mocker.patch(
        'src.media.repository.asyncio.sleep',
        new_callable=AsyncMock,
    )

    await wait_for_media_ready(['uid1'], timeout=5.0)

    assert mock_sleep.call_count == 1


async def test_wait_for_media_ready_treats_none_as_not_ready(mocker):
    ready_desc = MagicMock()
    ready_desc.status.is_finished = True
    mocker.patch(
        'src.media.repository.get_media_description_by_media_id',
        side_effect=[None, ready_desc],
    )
    mock_sleep = mocker.patch(
        'src.media.repository.asyncio.sleep',
        new_callable=AsyncMock,
    )

    await wait_for_media_ready(['uid1'], timeout=5.0)

    assert mock_sleep.call_count == 1


async def test_wait_for_media_ready_times_out(mocker):
    mock_get = mocker.patch('src.media.repository.get_media_description_by_media_id')
    mock_sleep = mocker.patch(
        'src.media.repository.asyncio.sleep',
        new_callable=AsyncMock,
    )
    mock_logger = mocker.patch('src.media.repository.logger')

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
        'src.media.repository.get_media_description_by_media_id',
        side_effect=get_by_uid,
    )
    mock_sleep = mocker.patch(
        'src.media.repository.asyncio.sleep',
        new_callable=AsyncMock,
    )

    await wait_for_media_ready(['uid1', 'uid2'], timeout=5.0)

    assert mock_sleep.call_count == 1


# --- describer stamp and re-describe on sighting ---

OLD_DESCRIBER = 'image_describe/v1@google/gemini-2.5-flash-lite'


def redescribe_events(caplog):
    return [r for r in caplog.records if getattr(r, 'event', None) == 'MEDIA_REDESCRIBE']


@pytest.fixture
def redescribe_on(mocker):
    mocker.patch.object(settings, 'MEDIA_REDESCRIBE_ON_SIGHTING', True)


@pytest.fixture
def mock_download(mocker):
    return mocker.patch(
        'src.media.handlers.get_message_media',
        return_value=ImageDetectionData(content='base64content', format='webp'),
    )


@pytest.fixture
def mock_describe(mocker):
    return mocker.patch(
        'src.media.handlers._generate_media_description',
        return_value=MediaDescriptionData(description='новое описание', ocr_text=None),
    )


@pytest.fixture
def mock_save_sticker(mocker):
    return mocker.patch('src.media.handlers.stickers_embedding_client.save_sticker')


async def stale_sticker_row(describer=None, **kwargs):
    row = await create_media_description(
        media_id='sticker_uid',
        content_hash='old_hash',
        type=MessageMediaTypes.STICKER,
        status=MessageMediaStatus.READY,
        description='пиксельные смайлики',
        ocr_text='ЛОЛ',
        sticker_emoji='💃',
    )
    fields = {'describer': describer, **kwargs}
    await media_descriptions.update_one({'_id': ObjectId(row.id)}, {'$set': fields})
    return row


async def raw_row(unique_id='sticker_uid'):
    return await media_descriptions.find_one({'media_id': unique_id})


async def test_new_image_is_stamped_with_the_image_describer(
    mocker, sample_message, mock_context, mock_download, mock_describe
):
    await handle_media_message(sample_message, mock_context)

    stored = await get_media_description_by_media_id('unique_id_123')
    assert stored.describer == image_processor.current_describer()


async def test_new_animation_is_stamped_with_the_animation_describer(
    mocker, sample_message, mock_context, mock_describe
):
    mocker.patch(
        'src.media.handlers.get_message_media',
        return_value=AnimationDetectionData(content=b'gif', format='gif'),
    )

    await handle_media_message(sample_message, mock_context)

    stored = await get_media_description_by_media_id('unique_id_123')
    assert stored.describer == animation_processor.current_describer()


async def test_new_sticker_is_stamped(
    mock_context, mock_download, mock_describe, mock_save_sticker
):
    await handle_media_message(sticker_message(), mock_context)

    stored = await get_media_description_by_media_id('sticker_uid')
    assert stored.describer == sticker_processor.current_describer()


def test_current_describers_are_task_prompt_at_model():
    assert image_processor.current_describer().startswith('image_describe/v1@')
    assert animation_processor.current_describer().startswith('animation_describe/v1@')
    assert sticker_processor.current_describer() == 'sticker_describe/v2@google/gemini-3.8-flash'
    assert _current_describers(MessageMediaTypes.STICKER) == {sticker_processor.current_describer()}
    expected = {image_processor.current_describer(), animation_processor.current_describer()}
    assert _current_describers(MessageMediaTypes.IMAGE) == expected
    assert _current_describers(MessageMediaTypes.GIF) == expected


def test_parse_media_description_without_a_describer_key():
    row = {
        '_id': ObjectId(),
        'description': 'x',
        'ocr_text': None,
        'type': 'image',
        'status': 'ready',
        'media_id': 'uid',
    }

    assert parse_media_description(row).describer is None


async def test_flag_off_a_stale_ready_row_stays_cached(
    mocker, mock_context, mock_download, mock_describe
):
    mocker.patch.object(settings, 'MEDIA_REDESCRIBE_ON_SIGHTING', False)
    await stale_sticker_row(describer=None)

    await handle_media_message(sticker_message(), mock_context)

    assert mock_download.call_count == 0
    assert mock_describe.call_count == 0


async def test_flag_on_a_current_row_stays_cached(
    redescribe_on, mock_context, mock_download, mock_describe
):
    await stale_sticker_row(describer=sticker_processor.current_describer())

    await handle_media_message(sticker_message(), mock_context)

    assert mock_download.call_count == 0
    assert mock_describe.call_count == 0


@pytest.mark.parametrize('old', [None, OLD_DESCRIBER])
async def test_flag_on_a_stale_row_is_replaced_whole(
    old, redescribe_on, mock_context, mock_download, mock_describe, mock_save_sticker
):
    await stale_sticker_row(describer=old)

    await handle_media_message(sticker_message(), mock_context)

    stored = await get_media_description_by_media_id('sticker_uid')
    assert stored.description == 'новое описание'
    assert stored.ocr_text is None  # the old OCR must not survive next to a new description
    assert stored.describer == sticker_processor.current_describer()
    assert stored.status == MessageMediaStatus.READY
    assert 'redescribe_started_at' not in await raw_row()


async def test_flag_on_status_is_ready_throughout_a_redescribe(
    mocker, redescribe_on, mock_context, mock_download, mock_save_sticker
):
    await stale_sticker_row()
    seen = []

    async def describe(message, data, row_type):
        seen.append((await get_media_description_by_media_id('sticker_uid')).status)
        return MediaDescriptionData(description='новое', ocr_text=None)

    mocker.patch('src.media.handlers._generate_media_description', side_effect=describe)

    await handle_media_message(sticker_message(), mock_context)
    final = await get_media_description_by_media_id('sticker_uid')

    assert seen == [MessageMediaStatus.READY]
    assert final.status == MessageMediaStatus.READY
    assert not final.status.is_pending


async def test_flag_on_a_failed_describe_keeps_the_old_row_and_keeps_the_claim(
    redescribe_on, mock_context, mock_download, mock_describe, mock_save_sticker, caplog
):
    await stale_sticker_row(describer=OLD_DESCRIBER)
    mock_describe.return_value = None

    with caplog.at_level('INFO'):
        await handle_media_message(sticker_message(), mock_context)

    stored = await get_media_description_by_media_id('sticker_uid')
    assert stored.description == 'пиксельные смайлики'
    assert stored.ocr_text == 'ЛОЛ'
    assert stored.describer == OLD_DESCRIBER
    assert stored.status == MessageMediaStatus.READY
    assert (await raw_row())['redescribe_started_at'] is not None
    assert mock_save_sticker.call_count == 0
    assert [r.outcome for r in redescribe_events(caplog)] == ['error']


async def test_flag_on_a_failed_download_keeps_the_old_row_and_keeps_the_claim(
    redescribe_on, mock_context, mock_download, mock_describe, caplog
):
    await stale_sticker_row(describer=OLD_DESCRIBER)
    mock_download.return_value = None

    with caplog.at_level('INFO'):
        await handle_media_message(sticker_message(), mock_context)

    stored = await get_media_description_by_media_id('sticker_uid')
    assert stored.description == 'пиксельные смайлики'
    assert stored.describer == OLD_DESCRIBER
    assert (await raw_row())['redescribe_started_at'] is not None
    assert mock_describe.call_count == 0
    assert [r.outcome for r in redescribe_events(caplog)] == ['error']


async def test_flag_on_concurrent_sightings_describe_once(
    mocker, redescribe_on, mock_context, mock_download, mock_save_sticker, caplog
):
    await stale_sticker_row()

    async def slow_describe(message, data, row_type):
        await asyncio.sleep(0.2)
        return MediaDescriptionData(description='новое', ocr_text=None)

    mock_describe = mocker.patch(
        'src.media.handlers._generate_media_description', side_effect=slow_describe
    )

    with caplog.at_level('INFO'):
        await asyncio.gather(*[
            handle_media_message(sticker_message(), mock_context) for _ in range(5)
        ])

    assert mock_describe.call_count == 1
    assert mock_download.call_count == 1
    outcomes = sorted(r.outcome for r in redescribe_events(caplog))
    assert outcomes == ['claimed_elsewhere'] * 4 + ['ok']


async def test_flag_on_a_fresh_claim_blocks_an_attempt(
    redescribe_on, mock_context, mock_download, mock_describe
):
    now = datetime.now(UTC).timestamp()
    await stale_sticker_row(redescribe_started_at=now)

    await handle_media_message(sticker_message(), mock_context)

    assert mock_describe.call_count == 0


async def test_flag_on_an_expired_claim_does_not_block_an_attempt(
    redescribe_on, mock_context, mock_download, mock_describe, mock_save_sticker
):
    long_ago = (
        datetime.now(UTC) - timedelta(minutes=settings.MEDIA_PROCESSING_STALE_MINUTES + 1)
    ).timestamp()
    await stale_sticker_row(redescribe_started_at=long_ago)

    await handle_media_message(sticker_message(), mock_context)

    assert mock_describe.call_count == 1


async def test_flag_on_a_redescribed_sticker_is_saved_to_the_index_once(
    redescribe_on, mock_context, mock_download, mock_describe, mock_save_sticker
):
    await stale_sticker_row()

    await handle_media_message(sticker_message(), mock_context)
    await handle_media_message(sticker_message(), mock_context)  # now current: cached

    assert mock_save_sticker.call_count == 1
    saved = mock_save_sticker.call_args[0][0]
    assert saved.media_id == 'sticker_uid'
    assert saved.description == 'новое описание'
    assert await media_descriptions.count_documents({'media_id': 'sticker_uid'}) == 1


async def test_flag_on_a_redescribed_photo_is_not_indexed(
    redescribe_on, sample_message, mock_context, mock_download, mock_describe, mock_save_sticker
):
    row = await create_media_description(
        media_id='unique_id_123', status=MessageMediaStatus.READY, description='old'
    )

    await handle_media_message(sample_message, mock_context)

    stored = await get_media_description_by_media_id('unique_id_123')
    assert stored.id == row.id
    assert stored.description == 'новое описание'
    assert mock_save_sticker.call_count == 0


async def test_flag_on_hash_path_redescribes_a_stale_row(
    redescribe_on, sample_message, mock_context, mock_download, mock_describe
):
    content_hash = mock_download.return_value.content_hash
    await create_media_description(
        media_id='other_uid',
        content_hash=content_hash,
        description='old',
        status=MessageMediaStatus.READY,
    )

    await handle_media_message(sample_message, mock_context)

    stored = await get_media_description_by_media_id('other_uid')
    assert stored.description == 'новое описание'
    assert stored.describer == image_processor.current_describer()
    assert mock_describe.call_count == 1
    assert await get_media_description_by_media_id('unique_id_123') is None


async def test_flag_on_hash_path_keeps_a_current_row_cached(
    redescribe_on, sample_message, mock_context, mock_download, mock_describe
):
    content_hash = mock_download.return_value.content_hash
    row = await create_media_description(
        media_id='other_uid',
        content_hash=content_hash,
        description='old',
        status=MessageMediaStatus.READY,
    )
    await media_descriptions.update_one(
        {'_id': ObjectId(row.id)}, {'$set': {'describer': image_processor.current_describer()}}
    )

    await handle_media_message(sample_message, mock_context)

    assert mock_describe.call_count == 0
    assert (await get_media_description_by_media_id('other_uid')).description == 'old'


async def test_flag_off_hash_path_keeps_a_stale_row_cached(
    mocker, sample_message, mock_context, mock_download, mock_describe
):
    mocker.patch.object(settings, 'MEDIA_REDESCRIBE_ON_SIGHTING', False)
    await create_media_description(
        media_id='other_uid',
        content_hash=mock_download.return_value.content_hash,
        description='old',
        status=MessageMediaStatus.READY,
    )

    await handle_media_message(sample_message, mock_context)

    assert mock_describe.call_count == 0


async def test_redescribe_event_carries_the_documented_fields(
    redescribe_on, mock_context, mock_download, mock_describe, mock_save_sticker, caplog
):
    await stale_sticker_row(describer=OLD_DESCRIBER)

    with caplog.at_level('INFO'):
        await handle_media_message(sticker_message(), mock_context)

    [record] = redescribe_events(caplog)
    assert record.unique_id == 'sticker_uid'
    assert record.kind == 'sticker'
    assert record.old_describer == OLD_DESCRIBER
    assert record.new_describer == sticker_processor.current_describer()
    assert record.outcome == 'ok'
    assert record.elapsed_ms >= 0


# --- spec 014: sticker route, per-type staleness, cooldown ---


@pytest.fixture
def route(mocker):
    result = MediaDescriptionData(description='новое описание', ocr_text=None)
    return {
        'sticker': mocker.patch('src.media.handlers.describe_sticker', return_value=result),
        'image': mocker.patch('src.media.handlers.describe_image', return_value=result),
        'animation': mocker.patch('src.media.handlers.describe_animation', return_value=result),
    }


def called(route):
    return {name: mock.call_count for name, mock in route.items() if mock.call_count}


async def test_new_static_sticker_goes_to_describe_sticker(
    mock_context, mock_download, route, mock_save_sticker
):
    await handle_media_message(sticker_message(), mock_context)

    stored = await get_media_description_by_media_id('sticker_uid')
    assert called(route) == {'sticker': 1}
    assert stored.describer == sticker_processor.current_describer()


async def test_new_animated_sticker_goes_to_describe_sticker(
    mocker, mock_context, route, mock_save_sticker
):
    mocker.patch(
        'src.media.handlers.get_message_media',
        return_value=AnimationDetectionData(content=b'webm', format='webm'),
    )

    await handle_media_message(sticker_message(), mock_context)

    stored = await get_media_description_by_media_id('sticker_uid')
    assert called(route) == {'sticker': 1}
    assert stored.type == MessageMediaTypes.STICKER
    assert stored.describer == sticker_processor.current_describer()


async def test_new_photo_still_goes_to_describe_image(
    sample_message, mock_context, mock_download, route
):
    await handle_media_message(sample_message, mock_context)

    stored = await get_media_description_by_media_id('unique_id_123')
    assert called(route) == {'image': 1}
    assert stored.describer == image_processor.current_describer()


async def test_new_gif_still_goes_to_describe_animation(
    mocker, sample_message, mock_context, route
):
    mocker.patch(
        'src.media.handlers.get_message_media',
        return_value=AnimationDetectionData(content=b'gif', format='gif'),
    )

    await handle_media_message(sample_message, mock_context)

    stored = await get_media_description_by_media_id('unique_id_123')
    assert called(route) == {'animation': 1}
    assert stored.describer == animation_processor.current_describer()


@pytest.mark.parametrize('old', [OLD_DESCRIBER, animation_processor.current_describer(), None])
async def test_flag_on_a_sticker_row_with_another_stamp_goes_to_describe_sticker(
    old, redescribe_on, mock_context, mock_download, route, mock_save_sticker
):
    await stale_sticker_row(describer=old)

    await handle_media_message(sticker_message(), mock_context)

    stored = await get_media_description_by_media_id('sticker_uid')
    assert called(route) == {'sticker': 1}
    assert stored.describer == sticker_processor.current_describer()


async def test_flag_on_a_sticker_row_with_the_sticker_stamp_is_skipped(
    redescribe_on, mock_context, mock_download, route
):
    await stale_sticker_row(describer=sticker_processor.current_describer())

    await handle_media_message(sticker_message(), mock_context)

    assert called(route) == {}
    assert mock_download.call_count == 0


async def test_flag_on_an_image_row_with_the_image_stamp_is_skipped(
    redescribe_on, sample_message, mock_context, mock_download, route
):
    row = await create_media_description(
        media_id='unique_id_123', status=MessageMediaStatus.READY, description='old'
    )
    await media_descriptions.update_one(
        {'_id': ObjectId(row.id)}, {'$set': {'describer': image_processor.current_describer()}}
    )

    await handle_media_message(sample_message, mock_context)

    assert called(route) == {}


async def test_flag_on_an_image_row_without_a_stamp_goes_to_describe_image(
    redescribe_on, sample_message, mock_context, mock_download, route
):
    await create_media_description(
        media_id='unique_id_123', status=MessageMediaStatus.READY, description='old'
    )

    await handle_media_message(sample_message, mock_context)

    stored = await get_media_description_by_media_id('unique_id_123')
    assert called(route) == {'image': 1}
    assert stored.describer == image_processor.current_describer()


async def test_flag_on_a_hash_found_photo_row_for_a_sticker_message_keeps_its_own_describer(
    redescribe_on, mock_context, mock_download, route, mock_save_sticker
):
    await create_media_description(
        media_id='other_uid',
        content_hash=mock_download.return_value.content_hash,
        status=MessageMediaStatus.READY,
        description='old',
    )

    await handle_media_message(sticker_message(), mock_context)
    stored = await get_media_description_by_media_id('other_uid')
    assert called(route) == {'image': 1}
    assert stored.type == MessageMediaTypes.IMAGE
    assert stored.describer == image_processor.current_describer()

    await handle_media_message(sticker_message(), mock_context)  # not stale on the next sighting

    assert called(route) == {'image': 1}


async def test_flag_on_a_failed_redescribe_blocks_the_next_sighting_inside_the_window(
    redescribe_on, mock_context, mock_download, route, mock_save_sticker, caplog
):
    await stale_sticker_row(describer=OLD_DESCRIBER)
    route['sticker'].return_value = None

    with caplog.at_level('INFO'):
        await handle_media_message(sticker_message(), mock_context)
        await handle_media_message(sticker_message(), mock_context)

    stored = await get_media_description_by_media_id('sticker_uid')
    assert route['sticker'].call_count == 1
    assert stored.description == 'пиксельные смайлики'
    assert [r.outcome for r in redescribe_events(caplog)] == ['error', 'claimed_elsewhere']


async def test_flag_on_a_failed_redescribe_is_retried_after_the_window(
    redescribe_on, mock_context, mock_download, route, mock_save_sticker
):
    await stale_sticker_row(describer=OLD_DESCRIBER)
    route['sticker'].return_value = None
    await handle_media_message(sticker_message(), mock_context)
    long_ago = (
        datetime.now(UTC) - timedelta(minutes=settings.MEDIA_PROCESSING_STALE_MINUTES + 1)
    ).timestamp()
    await media_descriptions.update_one(
        {'media_id': 'sticker_uid'}, {'$set': {'redescribe_started_at': long_ago}}
    )

    await handle_media_message(sticker_message(), mock_context)

    assert route['sticker'].call_count == 2


async def test_flag_on_a_redescribe_stamps_updated_at_on_a_row_that_had_none(
    redescribe_on, mock_context, mock_download, mock_describe, mock_save_sticker
):
    row = await stale_sticker_row(describer=OLD_DESCRIBER)
    await media_descriptions.update_one({'_id': ObjectId(row.id)}, {'$unset': {'updated_at': ''}})
    assert 'updated_at' not in await raw_row()
    before = datetime.now(UTC).timestamp()

    await handle_media_message(sticker_message(), mock_context)

    stored = await raw_row()
    assert stored['updated_at'] >= before
    assert stored['status'] == MessageMediaStatus.READY.value
