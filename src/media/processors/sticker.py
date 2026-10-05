import time
from functools import cache

from langchain_core.messages import HumanMessage, ImageContentBlock, SystemMessage
from langsmith import traceable

from src import ai
from src.logs import elapsed_ms, event, logger
from src.media.models import AnimationDetectionData, ImageDetectionData, MediaDescriptionData
from src.media.processors.animation import _get_animation_key_frames
from src.model_manager import model_manager
from src.prompt_manager import prompt_manager


_TASK = 'sticker_describe'
_PROMPT_VERSION = 'v2'
_MODEL_VERSION = 'v1'


@cache
def current_describer() -> str:
    """The stamp for what `describe_sticker` calls: `<task>/<prompt>@<model>`.

    Built from the same constants the call reads, so the stamp cannot drift from it.
    """
    model = model_manager.get_model_settings(_TASK, _MODEL_VERSION)['model']
    return f'{_TASK}/{_PROMPT_VERSION}@{model}'


def _image_blocks(media: ImageDetectionData | AnimationDetectionData) -> list[ImageContentBlock]:
    if isinstance(media, ImageDetectionData):
        return [
            ImageContentBlock(type='image', mime_type=f'image/{media.format}', base64=media.content)
        ]

    key_frames = _get_animation_key_frames(media)
    return [
        ImageContentBlock(type='image', mime_type='image/jpeg', base64=key_frame)
        for key_frame in key_frames
    ]


@traceable
async def describe_sticker(
    media: ImageDetectionData | AnimationDetectionData,
) -> MediaDescriptionData | None:
    blocks = _image_blocks(media)
    if not blocks:
        logger.warning(
            'No key frames found for animation',
            extra=event('MEDIA_FRAMES', outcome='empty', content_hash=media.content_hash),
        )
        return None

    llm = ai.get_sticker_descriptor_model(version=_MODEL_VERSION)
    model_with_structure = llm.with_structured_output(MediaDescriptionData)

    messages = [
        SystemMessage(content=prompt_manager.get_prompt(_TASK, version=_PROMPT_VERSION)),
        HumanMessage(content_blocks=blocks),
    ]

    started = time.monotonic()
    try:
        response: MediaDescriptionData = await model_with_structure.ainvoke(messages)
        if not response:
            raise ValueError('No response from model')
    except Exception:
        logger.error(
            'Error generating sticker description',
            exc_info=True,
            extra=event(
                'MEDIA_DESCRIBE',
                outcome='error',
                kind='sticker',
                content_hash=media.content_hash,
            ),
        )
        return None
    else:
        logger.debug(
            'Sticker description text',
            extra=event(
                'MEDIA_DESCRIBE_TEXT',
                content_hash=media.content_hash,
                description=response.description,
                ocr_text=response.ocr_text,
            ),
        )
        logger.info(
            'Sticker description generated',
            extra=event(
                'MEDIA_DESCRIBE',
                outcome='ok',
                kind='sticker',
                content_hash=media.content_hash,
                describer=current_describer(),
                desc_len=len(response.description or ''),
                ocr_len=len(response.ocr_text or ''),
                elapsed_ms=elapsed_ms(started),
            ),
        )
        return response
