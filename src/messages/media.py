import base64
import io
from pathlib import Path

from bson import ObjectId
from telegram.ext import ContextTypes

from src.db import media_descriptions
from src.logs import logger
from src.models import (
    ImageDescription, ImageDetectionData, ImageDetectionResult, Message,
    MessageMediaStatus,
)
from src.processors.media_descriptor import describe_image


async def handle_media_message(message: Message, context: ContextTypes.DEFAULT_TYPE):
    print('*' * 100)
    print(message.media.unique_id, message.media.media_id)
    if not message.media.unique_id or _skip_media_description_generation(message.media.status):
        return

    media_description = await get_media_description_by_media_id(message.media.unique_id)
    if media_description:
        if _skip_media_description_generation(media_description.status):
            logger.info(
                f"Image description found for {message.media.unique_id}: {media_description.description}"
            )
            return

    image_detection_data = await _get_message_image(message.media.media_id, context)
    content_hash = image_detection_data.content_hash
    if not media_description:
        if media_description := await get_media_descriptions_by_hash(content_hash):
            if _skip_media_description_generation(media_description.status):
                logger.info(
                    f"Image description found for {content_hash}: {media_description.description}"
                )
                return

    if not media_description:
        media_description = await create_media_description(message.media.unique_id)


    if not image_detection_data:
        await update_media_description_status(media_description.id, MessageMediaStatus.ERROR)
        logger.warning(f"Failed to get image data for message {message.id}")
        return

    await update_media_description_status(media_description.id, MessageMediaStatus.PROCESSING)
    image_description = await _generate_media_description(message, image_detection_data)

    if not image_description:
        logger.warning(f"Failed to generate image description for message {message.id}")
        await update_media_description_status(media_description.id, MessageMediaStatus.ERROR)
        return

    await update_media_description(
        description_id=media_description.id,
        content_hash=content_hash,
        description=image_description.description,
        ocr_text=image_description.ocr_text,
        status=MessageMediaStatus.READY,
    )


async def create_media_description(
    media_id: str,
    content_hash: str | None = None,
    description: str | None = None,
    ocr_text: str | None = None,
    status: MessageMediaStatus = MessageMediaStatus.PENDING,
):
    result = await media_descriptions.insert_one({
        'hash': content_hash or None,
        'description': description or None,
        'ocr_text': ocr_text or None,
        'media_id': media_id,
        'type': 'image',
        'status': status.value,
    })
    return await get_media_description(result.inserted_id)


async def update_media_description(
    description_id: str,
    content_hash: str | None = None,
    description: str | None = None,
    ocr_text: str | None = None,
    status: MessageMediaStatus = MessageMediaStatus.PROCESSING,
):
    update = {}
    if content_hash:
        update['hash'] = content_hash
    if description:
        update['description'] = description
    if ocr_text:
        update['ocr_text'] = ocr_text
    if status:
        update['status'] = status.value

    if update:
        await media_descriptions.update_one({'_id': ObjectId(description_id)}, {'$set': update})

    return await get_media_description(description_id)


async def get_media_description(description_id: str) -> ImageDetectionResult | None:
    result = await media_descriptions.find_one({'_id': ObjectId(description_id)})
    return _parse_media_description(result) if result else None


async def get_media_description_by_media_id(media_id: str) -> ImageDetectionResult | None:
    result = await media_descriptions.find_one({'media_id': media_id})
    return _parse_media_description(result) if result else None


async def get_media_descriptions_by_hash(content_hash: str) -> ImageDetectionResult | None:
    result = await media_descriptions.find_one({'hash': content_hash})
    return _parse_media_description(result) if result else None

async def update_media_description_status(description_id: str, status: MessageMediaStatus):
    await media_descriptions.update_one(
        {'_id': description_id},
        {'$set': {'status': status.value}}
    )


def _skip_media_description_generation(status: MessageMediaStatus) -> bool:
    return status in {MessageMediaStatus.READY, MessageMediaStatus.PROCESSING}


async def _generate_media_description(
    message: Message,
    image_detection_data: ImageDetectionData,
) -> ImageDescription | None:
    logger.info(f"Generating image description for image {message.media.media_id}")
    image_description = await describe_image(image_detection_data)
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


def _parse_media_description(data: dict) -> ImageDetectionResult:
    return ImageDetectionResult(
        _id=str(data['_id']),
        description=data['description'] or '',
        ocr_text=data['ocr_text'],
        type=data['type'],
        status=data['status'],
        media_id=data['media_id'],
    )