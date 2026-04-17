import base64
import io
from itertools import chain
from pathlib import Path

from telegram.ext import ContextTypes

from src.logs import logger
from src.models import (
    AnimationDetectionData, ImageDetectionData, MediaDetectionData,
    MessageMediaTypes,
)

FILE_FORMATS = {
    MessageMediaTypes.IMAGE: {'jpg', 'jpeg', 'png', 'webp'},
    MessageMediaTypes.GIF: {'gif', 'webm', 'mp4', 'tgs'},
}
SUPPORTED_FORMATS = set(chain(*FILE_FORMATS.values()))


async def get_message_media(
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
