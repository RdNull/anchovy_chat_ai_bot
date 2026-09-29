import time
from datetime import datetime, timedelta, UTC

from telegram.ext import ContextTypes

from src import settings
from src.embeddings.stickers import stickers_embedding_client
from src.logs import elapsed_ms, event, logger
from src.media.download import get_message_media
from src.media.models import (
    AnimationDetectionData,
    ImageDetectionData,
    MediaDescription,
    MediaDescriptionData,
    MediaDetectionData,
)
from src.media.processors import animation as animation_processor
from src.media.processors import image as image_processor
from src.media.processors.animation import describe_animation
from src.media.processors.image import describe_image
from src.media.repository import (
    claim_redescribe,
    create_media_description,
    get_media_description_by_media_id,
    get_media_descriptions_by_hash,
    mark_as_sticker,
    release_redescribe_claim,
    replace_media_description,
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

    # A READY row that got past the skip check is stale and the flag is on. The claim
    # comes before the download, so a spammed sticker downloads once, not per sighting.
    if media_description and media_description.status == MessageMediaStatus.READY:
        await _redescribe(message, context, media_description, None)
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
                        'MEDIA_DESCRIPTION_TEXT',
                        description=media_description.description,
                    ),
                )
                return

            if media_description.status == MessageMediaStatus.READY:
                await _redescribe(message, context, media_description, media_detection_data)
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
        describer=_describer_for(media_detection_data),
    )
    # Deliberately not gated on ENABLE_STICKER_REPLIES: the flag gates the tools, and
    # the corpus has to accumulate while it is off so there is something there to
    # search when it is flipped on.
    if updated and updated.type == MessageMediaTypes.STICKER:
        await stickers_embedding_client.save_sticker(updated)


def _stored_type(
    message: Message,
    media_detection_data: MediaDetectionData,
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
    message: Message,
    media_description: MediaDescription,
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


def _current_describers() -> set[str]:
    return {image_processor.current_describer(), animation_processor.current_describer()}


def _describer_for(media_detection_data: MediaDetectionData) -> str | None:
    if isinstance(media_detection_data, ImageDetectionData):
        return image_processor.current_describer()

    if isinstance(media_detection_data, AnimationDetectionData):
        return animation_processor.current_describer()

    return None


def _is_describer_stale(description: MediaDescription) -> bool:
    """Only meaningful with the flag on; a row with no stamp counts as stale.

    Membership in the current set rather than a per-kind check, so nobody has to know
    before the download whether a sticker is static or animated.
    """
    if not settings.MEDIA_REDESCRIBE_ON_SIGHTING:
        return False

    return description.describer not in _current_describers()


async def _redescribe(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    description: MediaDescription,
    media_detection_data: MediaDetectionData | None,
):
    """Describes a stale READY row again without ever taking it off READY.

    No PROCESSING write: `fetch_last_messages` would make a reply wait on media that
    already has a usable description, and a failure would lose it. A failure at any
    step leaves the old row as it was and releases the claim, so the next sighting
    tries again.
    """
    started = time.monotonic()
    fields = {
        'unique_id': message.media.unique_id,
        'kind': description.type.value,
        'old_describer': description.describer,
    }

    if not await claim_redescribe(description.id, _current_describers()):
        logger.info(
            'Media re-describe claimed elsewhere',
            extra=event(
                'MEDIA_REDESCRIBE',
                **fields,
                new_describer=None,
                outcome='claimed_elsewhere',
                elapsed_ms=elapsed_ms(started),
            ),
        )
        return

    new_describer = None
    outcome = 'error'
    try:
        if not media_detection_data:
            media_detection_data = await get_message_media(message.media.media_id, context)

        result = None
        if media_detection_data:
            result = await _generate_media_description(message, media_detection_data)

        if result:
            new_describer = _describer_for(media_detection_data)
            updated = await replace_media_description(
                description_id=description.id,
                content_hash=media_detection_data.content_hash,
                description=result.description,
                ocr_text=result.ocr_text,
                describer=new_describer,
            )
            outcome = 'ok'
            if updated and updated.type == MessageMediaTypes.STICKER:
                await stickers_embedding_client.save_sticker(updated)
    except Exception:
        logger.error('Media re-describe failed', exc_info=True)

    if outcome != 'ok':
        await release_redescribe_claim(description.id)

    logger.info(
        'Media re-described',
        extra=event(
            'MEDIA_REDESCRIBE',
            **fields,
            new_describer=new_describer,
            outcome=outcome,
            elapsed_ms=elapsed_ms(started),
        ),
    )


def _skip_media_description_generation(description: MediaDescription) -> bool:
    """READY skips unless it is stale and re-describe is on; PROCESSING skips only
    while it's still plausibly in flight. A crash or pod restart between the PROCESSING write and the describe
    call would otherwise leave a row that is never retried and that
    `wait_for_media_ready` polls to its full timeout on every future sighting.
    """
    if description.status == MessageMediaStatus.READY:
        return not _is_describer_stale(description)
    if description.status == MessageMediaStatus.PROCESSING:
        return not _is_processing_stale(description.updated_at)
    return False


def _is_processing_stale(updated_at: datetime | None) -> bool:
    # A missing stamp is a row written before this field existed — treat it as
    # stale rather than raising, so a legacy row is retried instead of stuck.
    if updated_at is None:
        return True
    age = datetime.now(UTC) - updated_at
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
                'MEDIA_DESCRIBE',
                kind='image',
                media_id=message.media.media_id,
                describer=_describer_for(media_detection_data),
                elapsed_ms=elapsed_ms(started),
                outcome='ok' if result else 'error',
            ),
        )
        return result

    if isinstance(media_detection_data, AnimationDetectionData):
        result = await describe_animation(media_detection_data)
        logger.info(
            'Media description generated',
            extra=event(
                'MEDIA_DESCRIBE',
                kind='animation',
                media_id=message.media.media_id,
                describer=_describer_for(media_detection_data),
                elapsed_ms=elapsed_ms(started),
                outcome='ok' if result else 'error',
            ),
        )
        return result

    return None
