import time
from datetime import datetime, timedelta, timezone

from telegram.ext import ContextTypes

from src import settings
from src.embeddings.stickers import stickers_embedding_client
from src.logs import elapsed_ms, event, logger
from src.media.download import get_message_media
from src.media.models import (
    AnimationDetectionData, ImageDetectionData, MediaDescription, MediaDescriptionData,
    MediaDetectionData,
)
from src.media.processors.animation import describe_animation
from src.media.processors.image import describe_image
from src.media.repository import (
    create_media_description,
    get_media_description_by_media_id,
    get_media_descriptions_by_hash,
    mark_as_sticker,
    update_media_description,
    update_media_description_status,
)
from src.messages.models import Message, MessageMediaStatus, MessageMediaTypes


async def handle_media_message(message: Message, context: ContextTypes.DEFAULT_TYPE):
    if not message.media.unique_id:
        return

    # Fetched before the skip check, because the backfill below has to run even on
    # media that needs no description generating — that is the whole point of it.
    media_description = await get_media_description_by_media_id(message.media.unique_id)
    if media_description:
        media_description = await _backfill_sticker(message, media_description)

    if media_description and _skip_media_description_generation(media_description):
        logger.info(
            'Media description found (cached)',
            extra=event('MEDIA_DESCRIPTION_CACHED', unique_id=message.media.unique_id),
        )
        logger.debug(
            'Cached media description text',
            extra=event('MEDIA_DESCRIPTION_TEXT', description=media_description.description),
        )
        return

    media_detection_data = await get_message_media(message.media.media_id, context)
    if not media_detection_data:
        logger.warning(
            'Failed to get media data',
            extra=event('MEDIA_FETCH', outcome='error', message_id=message.id),
        )
        return

    content_hash = media_detection_data.content_hash
    if not media_description:
        if media_description := await get_media_descriptions_by_hash(content_hash):
            if _skip_media_description_generation(media_description):
                logger.info(
                    'Media description found (cached)',
                    extra=event('MEDIA_DESCRIPTION_CACHED', content_hash=content_hash),
                )
                logger.debug(
                    'Cached media description text',
                    extra=event(
                        'MEDIA_DESCRIPTION_TEXT', description=media_description.description,
                    ),
                )
                return

    if not media_description:
        media_description = await create_media_description(
            media_id=message.media.unique_id,
            type=_stored_type(message, media_detection_data),
            content_hash=content_hash,
            sticker_emoji=message.media.sticker_emoji,
        )

    await update_media_description_status(media_description.id, MessageMediaStatus.PROCESSING)
    image_description = await _generate_media_description(message, media_detection_data)

    if not image_description:
        logger.warning(
            'Failed to generate media description',
            extra=event('MEDIA_DESCRIBE', outcome='error', message_id=message.id),
        )
        await update_media_description_status(media_description.id, MessageMediaStatus.ERROR)
        return

    updated = await update_media_description(
        description_id=media_description.id,
        content_hash=content_hash,
        description=image_description.description,
        ocr_text=image_description.ocr_text,
        status=MessageMediaStatus.READY,
    )
    # Deliberately not gated on ENABLE_STICKER_REPLIES: the flag gates the tools, and
    # the corpus has to accumulate while it is off so there is something there to
    # search when it is flipped on.
    if updated and updated.type == MessageMediaTypes.STICKER:
        await stickers_embedding_client.save_sticker(updated)


def _stored_type(
    message: Message, media_detection_data: MediaDetectionData,
) -> MessageMediaTypes:
    """The label the row carries, which is not the decoder that produced it.

    `media_detection_data.type` is IMAGE or GIF because it was picked from the file
    extension to choose a parser. A sticker keeps its own label instead: nothing
    downstream needs to know whether it arrived as a `.webp` or a `.tgs`.
    """
    if message.media.type == MessageMediaTypes.STICKER:
        return MessageMediaTypes.STICKER

    return media_detection_data.type


async def _backfill_sticker(
    message: Message, media_description: MediaDescription,
) -> MediaDescription:
    """Retypes and indexes a sticker whose row predates the sticker unit.

    This runs before the early return, and that placement is the whole point.
    `handle_media_message` returns as soon as it finds a READY description, so a
    sticker the group has sent before would never re-enter the marking path: the
    corpus could only ever grow from stickers nobody had ever sent, which is far too
    slow to be useful. Re-sighting a known sticker is the common case, so that is
    what fills the index — no migration, no backfill script.
    """
    if message.media.type != MessageMediaTypes.STICKER:
        return media_description

    if media_description.type == MessageMediaTypes.STICKER:
        return media_description  # already retyped on an earlier sighting

    logger.info(
        'Backfilling sticker type',
        extra=event('MEDIA_STICKER_BACKFILL', unique_id=message.media.unique_id),
    )
    retyped = await mark_as_sticker(media_description.id, message.media.sticker_emoji)
    if not retyped:
        return media_description

    if retyped.status == MessageMediaStatus.READY:
        await stickers_embedding_client.save_sticker(retyped)

    return retyped


def _skip_media_description_generation(description: MediaDescription) -> bool:
    """READY always skips; PROCESSING skips only while it's still plausibly in
    flight. A crash or pod restart between the PROCESSING write and the describe
    call would otherwise leave a row that is never retried and that
    `wait_for_media_ready` polls to its full timeout on every future sighting."""
    if description.status == MessageMediaStatus.READY:
        return True
    if description.status == MessageMediaStatus.PROCESSING:
        return not _is_processing_stale(description.updated_at)
    return False


def _is_processing_stale(updated_at: datetime | None) -> bool:
    # A missing stamp is a row written before this field existed — treat it as
    # stale rather than raising, so a legacy row is retried instead of stuck.
    if updated_at is None:
        return True
    age = datetime.now(timezone.utc) - updated_at
    return age > timedelta(minutes=settings.MEDIA_PROCESSING_STALE_MINUTES)


async def _generate_media_description(
    message: Message,
    media_detection_data: MediaDetectionData,
) -> MediaDescriptionData | None:
    started = time.monotonic()
    if isinstance(media_detection_data, ImageDetectionData):
        result = await describe_image(media_detection_data)
        logger.info(
            'Media description generated',
            extra=event(
                'MEDIA_DESCRIBE', kind='image', media_id=message.media.media_id,
                elapsed_ms=elapsed_ms(started), outcome='ok' if result else 'error',
            ),
        )
        return result

    if isinstance(media_detection_data, AnimationDetectionData):
        result = await describe_animation(media_detection_data)
        logger.info(
            'Media description generated',
            extra=event(
                'MEDIA_DESCRIBE', kind='animation', media_id=message.media.media_id,
                elapsed_ms=elapsed_ms(started), outcome='ok' if result else 'error',
            ),
        )
        return result

    return None
