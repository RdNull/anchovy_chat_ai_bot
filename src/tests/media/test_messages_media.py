import base64
import io
from unittest.mock import AsyncMock, MagicMock

import pytest
from src import settings

from src.messages.media import (
    create_media_description, get_media_description_by_media_id, get_recent_sticker_ids,
    get_sendable_file_id, handle_media_message, sticker_corpus_size,
)
from src.messages.repository import get_message_media_data
from src.mongo import media_descriptions, messages
from src.messages.media.download import _parse_animation_file, _parse_image_file, get_message_media
from src.messages.media.pipeline import _generate_media_description, wait_for_media_ready
from src.models import (
    AnimationDetectionData, ImageDetectionData, MediaDescriptionData, MediaDetectionData,
    Message, MessageMedia, MessageMediaStatus, MessageMediaTypes, UserRole,
)


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
    mocker.patch('src.messages.media.pipeline.get_message_media', return_value=ImageDetectionData(
        content='base64content',
        format='webp',
    ))
    mocker.patch(
        'src.messages.media.pipeline._generate_media_description',
        return_value=MediaDescriptionData(description='a dancing cat', ocr_text=None),
    )
    mocker.patch('src.messages.media.pipeline.stickers_embedding_client.save_sticker')

    await handle_media_message(sticker_message(), mock_context)

    stored = await get_media_description_by_media_id('sticker_uid')
    assert stored.type == MessageMediaTypes.STICKER
    assert stored.sticker_emoji == '💃'


async def test_handle_media_message_indexes_a_ready_sticker(mocker, mock_context):
    mocker.patch('src.messages.media.pipeline.get_message_media', return_value=ImageDetectionData(
        content='base64content', format='webp',
    ))
    mocker.patch(
        'src.messages.media.pipeline._generate_media_description',
        return_value=MediaDescriptionData(description='кот танцует', ocr_text=None),
    )
    mock_save = mocker.patch(
        'src.messages.media.pipeline.stickers_embedding_client.save_sticker'
    )
    mocker.patch.object(settings, 'ENABLE_STICKER_REPLIES', False)

    await handle_media_message(sticker_message(), mock_context)

    # Indexing is not gated on the flag: the corpus has to accumulate while it is off.
    assert mock_save.call_count == 1
    assert mock_save.call_args[0][0].media_id == 'sticker_uid'
    assert mock_save.call_args[0][0].status == MessageMediaStatus.READY


async def test_handle_media_message_does_not_index_a_photo(mocker, sample_message, mock_context):
    mocker.patch('src.messages.media.pipeline.get_message_media', return_value=ImageDetectionData(
        content='base64content', format='jpg',
    ))
    mocker.patch(
        'src.messages.media.pipeline._generate_media_description',
        return_value=MediaDescriptionData(description='a screenshot', ocr_text=None),
    )
    mock_save = mocker.patch(
        'src.messages.media.pipeline.stickers_embedding_client.save_sticker'
    )

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
    mock_save = mocker.patch(
        'src.messages.media.pipeline.stickers_embedding_client.save_sticker'
    )
    mock_download = mocker.patch('src.messages.media.pipeline.get_message_media')

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
    mock_save = mocker.patch(
        'src.messages.media.pipeline.stickers_embedding_client.save_sticker'
    )
    mocker.patch('src.messages.media.pipeline.get_message_media')

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
    mock_save = mocker.patch(
        'src.messages.media.pipeline.stickers_embedding_client.save_sticker'
    )
    mocker.patch('src.messages.media.pipeline.get_message_media')

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
    mocker.patch('src.messages.media.pipeline.get_message_media', return_value=ImageDetectionData(
        content='base64content', format='webp',
    ))
    mocker.patch(
        'src.messages.media.pipeline._generate_media_description',
        return_value=MediaDescriptionData(description='кот танцует', ocr_text=None),
    )
    mock_save = mocker.patch(
        'src.messages.media.pipeline.stickers_embedding_client.save_sticker'
    )

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
        'chat_id': 123, 'role': UserRole.AI.value, 'text': 'just text',
        'nickname': 'bot', 'media_id': None, 'media_unique_id': None,
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
        media_id='ready_sticker', type=MessageMediaTypes.STICKER,
        status=MessageMediaStatus.READY,
    )
    await create_media_description(
        media_id='pending_sticker', type=MessageMediaTypes.STICKER,
        status=MessageMediaStatus.PENDING,
    )
    await create_media_description(
        media_id='ready_photo', type=MessageMediaTypes.IMAGE,
        status=MessageMediaStatus.READY,
    )

    assert await sticker_corpus_size() == 1


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
