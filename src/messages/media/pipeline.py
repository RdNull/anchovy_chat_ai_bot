from telegram.ext import ContextTypes

from src.logs import logger
from src.models import (
    AnimationDetectionData, ImageDetectionData, MediaDescriptionData, MediaDetectionData,
    Message, MessageMediaStatus,
)
from src.processors.media.animation import describe_animation
from src.processors.media.image import describe_image
from .download import get_message_media
from .repository import (
    create_media_description,
    get_media_description_by_media_id,
    get_media_descriptions_by_hash,
    update_media_description,
    update_media_description_status,
)


async def handle_media_message(message: Message, context: ContextTypes.DEFAULT_TYPE):
    if not message.media.unique_id or _skip_media_description_generation(message.media.status):
        return

    media_description = await get_media_description_by_media_id(message.media.unique_id)
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
            type=media_detection_data.type,
            content_hash=content_hash,
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

    await update_media_description(
        description_id=media_description.id,
        content_hash=content_hash,
        description=image_description.description,
        ocr_text=image_description.ocr_text,
        status=MessageMediaStatus.READY,
    )


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
