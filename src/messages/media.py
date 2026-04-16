import base64
import io
from itertools import chain
from pathlib import Path

from bson import ObjectId
from telegram.ext import ContextTypes

from src.mongo import media_descriptions
from src.logs import logger
from src.models import (
    AnimationDetectionData, ImageDetectionData, MediaDescription, MediaDescriptionData,
    MediaDetectionData, Message,
    MessageMediaStatus, MessageMediaTypes,
)
from src.processors.media.animation import describe_animation
from src.processors.media.image import describe_image

FILE_FORMATS = {
    MessageMediaTypes.IMAGE: {"jpg", "jpeg", "png", "webp", },
    MessageMediaTypes.GIF: {'gif', 'webm', 'mp4', 'tgs', },
}
SUPPORTED_FORMATS = set(chain(*FILE_FORMATS.values()))


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

    media_detection_data = await _get_message_media(message.media.media_id, context)
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


async def create_media_description(
    media_id: str,
    content_hash: str | None = None,
    description: str | None = None,
    ocr_text: str | None = None,
    type: MessageMediaTypes = MessageMediaTypes.IMAGE,
    status: MessageMediaStatus = MessageMediaStatus.PENDING,
):
    result = await media_descriptions.insert_one({
        'hash': content_hash or None,
        'description': description or None,
        'ocr_text': ocr_text or None,
        'media_id': media_id,
        'type': type.value,
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


async def get_media_description(description_id: str) -> MediaDescription | None:
    result = await media_descriptions.find_one({'_id': ObjectId(description_id)})
    return _parse_media_description(result) if result else None


async def get_media_description_by_media_id(media_id: str) -> MediaDescription | None:
    result = await media_descriptions.find_one({'media_id': media_id})
    return _parse_media_description(result) if result else None


async def get_media_descriptions_by_hash(content_hash: str) -> MediaDescription | None:
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
    media_detection_data: MediaDetectionData,
) -> MediaDescriptionData | None:
    if isinstance(media_detection_data, ImageDetectionData):
        logger.info(f"Generating image description for image {message.media.media_id}")
        return await describe_image(media_detection_data)

    if isinstance(media_detection_data, AnimationDetectionData):
        logger.info(f"Generating animation description for animation {message.media.media_id}")
        return await describe_animation(media_detection_data)

    return None


async def _get_message_media(
    file_id: str,
    context: ContextTypes.DEFAULT_TYPE
) -> MediaDetectionData | None:
    media_file = await context.bot.get_file(file_id)
    file_format = Path(media_file.file_path).suffix[1:].lower()
    file_type = _get_file_type(file_format)
    if not file_type:
        logger.warning(f"Unsupported media sent: {file_format}")
        return None

    with io.BytesIO() as file_bytes:
        await media_file.download_to_memory(file_bytes)
        file_bytes.seek(0)

        media_type_parsers = {
            MessageMediaTypes.IMAGE: _parse_image_file,
            MessageMediaTypes.GIF: _parse_animation_file,
        }
        return media_type_parsers[file_type](file_format, file_bytes)


def _get_file_type(file_format: str) -> MessageMediaTypes | None:
    return next((t for t, f in FILE_FORMATS.items() if file_format in f), None)


def _parse_image_file(file_format: str, file_bytes: io.BytesIO) -> ImageDetectionData:
    base64_content = base64.b64encode(file_bytes.read())
    return ImageDetectionData(
        content=base64_content.decode('utf-8'),
        format=file_format,
    )


def _parse_animation_file(file_format: str, file_bytes: io.BytesIO) -> AnimationDetectionData:
    return AnimationDetectionData(
        content=file_bytes.read(),
        format=file_format,
    )


def _parse_media_description(data: dict) -> MediaDescription:
    return MediaDescription(
        _id=str(data['_id']),
        description=data['description'] or '',
        ocr_text=data['ocr_text'],
        type=data['type'],
        status=data['status'],
        media_id=data['media_id'],
    )
