import base64
import io
from pathlib import Path

from telegram.ext import ContextTypes

from src.logs import logger
from src.messages.history import update_history_media
from src.models import ImageDetectionData, Message, MessageMediaStatus
from src.processors.media_descriptor import describe_image


async def handle_media_message(message: Message, context: ContextTypes.DEFAULT_TYPE):
    if not message.media:
        return

    logger.info(f"Generating image recap for message {message.id}")
    image_detection_data = await _get_message_image(
        message.media.media_id, context
    )
    image_description = await describe_image(image_detection_data)
    if not image_description:
        return

    message.media.status = MessageMediaStatus.READY
    message.media.description = image_description.description
    message.media.ocr_text = image_description.ocr_text

    logger.info(f"Image recap generated for message {message.id}")
    await update_history_media(message.id, message.media)


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

    await photo_file.download_to_drive()  # todo for test only
    return ImageDetectionData(
        content=base64_content.decode('utf-8'),
        format=file_format,
    )
