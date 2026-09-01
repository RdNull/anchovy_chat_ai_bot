import asyncio

from telegram.ext import ContextTypes

from src.embeddings.stickers import stickers_embedding_client
from src.logs import logger
from src.models import (
    AnimationDetectionData, ImageDetectionData, MediaDescription, MediaDescriptionData,
    MediaDetectionData, Message, MessageMediaStatus, MessageMediaTypes,
)
from src.processors.media.animation import describe_animation
from src.processors.media.image import describe_image
from .download import get_message_media
from .repository import (
    create_media_description,
    get_media_description_by_media_id,
    get_media_descriptions_by_hash,
    mark_as_sticker,
    update_media_description,
    update_media_description_status,
)


async def handle_media_message(message: Message, context: ContextTypes.DEFAULT_TYPE):
    if not message.media.unique_id:
        return

    # Fetched before either skip check, because the backfill below has to run even on
    # media that needs no description generating — that is the whole point of it.
    media_description = await get_media_description_by_media_id(message.media.unique_id)
    if media_description:
        media_description = await _backfill_sticker(message, media_description)

    if _skip_media_description_generation(message.media.status):
        return

    if media_description:
        if _skip_media_description_generation(media_description.status):
            logger.info(
                f"Media description found for {message.media.unique_id}: {media_description.description}"
            )
            return

    media_detection_data = await get_message_media(message.media.media_id, context)
    if not media_detection_data:
        logger.warning(f"Failed to get media data for message {message.id}")
        return

    content_hash = media_detection_data.content_hash
    if not media_description:
        if media_description := await get_media_descriptions_by_hash(content_hash):
            if _skip_media_description_generation(media_description.status):
                logger.info(
                    f"Media description found for {content_hash}: {media_description.description}"
                )
                return

    if not media_description:
        media_description = await create_media_description(
            media_id=message.media.unique_id,
            type=_stored_type(message, media_detection_data),
            content_hash=content_hash,
            sticker_emoji=message.media.sticker_emoji,
        )

    if not media_detection_data:
        await update_media_description_status(media_description.id, MessageMediaStatus.ERROR)
        logger.warning(f"Failed to get media data for message {message.id}")
        return

    await update_media_description_status(media_description.id, MessageMediaStatus.PROCESSING)
    image_description = await _generate_media_description(message, media_detection_data)

    if not image_description:
        logger.warning(f"Failed to generate media description for message {message.id}")
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


async def wait_for_media_ready(unique_ids: list[str], timeout: float) -> None:
    pending = set(unique_ids)
    deadline = asyncio.get_event_loop().time() + timeout

    while pending:
        if asyncio.get_event_loop().time() >= deadline:
            logger.warning(
                f'Media processing timed out, proceeding without descriptions for: {pending}'
            )
            return

        for uid in list(pending):
            description = await get_media_description_by_media_id(uid)
            if not description:
                continue

            if description.status.is_finished:
                pending.discard(uid)

        if pending:
            await asyncio.sleep(0.5)


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

    logger.info(f"Backfilling sticker type for {message.media.unique_id}")
    retyped = await mark_as_sticker(media_description.id, message.media.sticker_emoji)
    if not retyped:
        return media_description

    if retyped.status == MessageMediaStatus.READY:
        await stickers_embedding_client.save_sticker(retyped)

    return retyped


def _skip_media_description_generation(status: MessageMediaStatus) -> bool:
    return status in {MessageMediaStatus.READY, MessageMediaStatus.PROCESSING}


async def _generate_media_description(
    message: Message,
    media_detection_data: MediaDetectionData,
) -> MediaDescriptionData | None:
    if isinstance(media_detection_data, ImageDetectionData):
        logger.info(f"Generating image description for image {message.media.media_id}")
        return await describe_image(media_detection_data)

    if isinstance(media_detection_data, AnimationDetectionData):
        logger.info(f"Generating animation description for animation {message.media.media_id}")
        return await describe_animation(media_detection_data)

    return None
