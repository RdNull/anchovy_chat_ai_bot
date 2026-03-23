import base64
import io
from pathlib import Path

from telegram.ext import ContextTypes

from src.db import media_descriptions
from src.logs import logger
from src.messages.history import update_history_media
from src.models import ImageDetectionData, ImageDetectionResult, Message, MessageMediaStatus
from src.processors.media_descriptor import describe_image


async def handle_media_message(message: Message, context: ContextTypes.DEFAULT_TYPE):
    if not message.media:
        return

    image_description = await _get_media_description(message, context)
    if not image_description:
        logger.warning(f"Failed to generate image description for message {message.id}")
        return


    message.media.status = MessageMediaStatus.READY
    message.media.description = image_description.description
    message.media.ocr_text = image_description.ocr_text

    logger.info(f"Image recap generated for message {message.id}")
    await update_history_media(message.id, message.media)


async def _get_media_description(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE
) -> ImageDetectionResult | None:
    if media_description := await _get_message_description_by_media_id(message.media.media_id):
        logger.info(
            f"Image description found for {message.media.media_id}: {media_description.description}"
        )
        return media_description

    image_detection_data = await _get_message_image(message.media.media_id, context)
    if media_description := await _get_media_description_by_hash(image_detection_data):
        logger.info(
            f"Image description found for {message.media.media_id}: {media_description.description}"
        )
        return media_description

    logger.info(f"Generating image description for image {message.media.media_id}")
    image_description = await describe_image(image_detection_data)
    await _save_media_description(
        message.media.media_id,
        image_detection_data.content_hash,
        image_description.description,
        image_description.ocr_text
    )

    return image_description


async def _get_message_image(
    file_id: str,
    context: ContextTypes.DEFAULT_TYPE
) -> ImageDetectionData | None:
    photo_file = await context.bot.get_file(file_id)
    file_format = Path(photo_file.file_path).suffix[1:].lower()
    if file_format not in {"jpg", "jpeg", "png", "webp"}:
        logger.warning(f"Unsupported image format sent: {file_format}")
        return None

    with io.BytesIO() as file_bytes:
        await photo_file.download_to_memory(file_bytes)
        file_bytes.seek(0)
        base64_content = base64.b64encode(file_bytes.read())

    return ImageDetectionData(
        content=base64_content.decode('utf-8'),
        format=file_format,
    )


async def _get_message_description_by_media_id(media_id: str) -> ImageDetectionResult | None:
    media_description = await media_descriptions.find_one({'media_id': media_id})
    return _parse_media_description(media_description) if media_description else None


async def _get_media_description_by_hash(
    image_data: ImageDetectionData
) -> ImageDetectionResult | None:
    media_description = await media_descriptions.find_one({'hash': image_data.content_hash})
    return _parse_media_description(media_description) if media_description else None


async def _save_media_description(
    media_id: str,
    content_hash: str,
    description: str,
    ocr_text: str | None = None,
):
    await media_descriptions.insert_one({
        'hash': content_hash,
        'description': description,
        'ocr_text': ocr_text,
        'media_id': media_id,
    })


def _parse_media_description(data: dict) -> ImageDetectionResult:
    return ImageDetectionResult(
        description=data['description'],
        ocr_text=data['ocr_text']
    )
