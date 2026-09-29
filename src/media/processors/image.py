import time
from functools import cache

from langchain_core.messages import HumanMessage, ImageContentBlock, SystemMessage
from langsmith import traceable

from src import ai
from src.model_manager import model_manager
from src.logs import elapsed_ms, event, logger
from src.media.models import ImageDetectionData, MediaDescriptionData
from src.prompt_manager import prompt_manager


_TASK = 'image_describe'
_PROMPT_VERSION = 'v1'
_MODEL_VERSION = 'v2'


@cache
def current_describer() -> str:
    """The stamp for what `describe_image` calls: `<task>/<prompt>@<model>`.

    Built from the same constants the call reads, so the stamp cannot drift from it.
    """
    model = model_manager.get_model_settings(_TASK, _MODEL_VERSION)['model']
    return f'{_TASK}/{_PROMPT_VERSION}@{model}'


@traceable
async def describe_image(image: ImageDetectionData) -> MediaDescriptionData | None:
    llm = ai.get_image_descriptor_model(version=_MODEL_VERSION)
    model_with_structure = llm.with_structured_output(MediaDescriptionData)

    messages = [
        SystemMessage(content=prompt_manager.get_prompt(_TASK, version=_PROMPT_VERSION)),
        HumanMessage(
            content_blocks=[
                ImageContentBlock(
                    type='image', mime_type=f'image/{image.format}', base64=image.content
                )
            ]
        ),
    ]

    started = time.monotonic()
    try:
        response: MediaDescriptionData = await model_with_structure.ainvoke(messages)
        if not response:
            raise ValueError('No response from model')
    except Exception:
        logger.error(
            'Error generating image description',
            exc_info=True,
            extra=event(
                'MEDIA_DESCRIBE',
                outcome='error',
                kind='image',
                content_hash=image.content_hash,
            ),
        )
        return None
    else:
        logger.debug(
            'Image description text',
            extra=event(
                'MEDIA_DESCRIBE_TEXT',
                content_hash=image.content_hash,
                description=response.description,
                ocr_text=response.ocr_text,
            ),
        )
        logger.info(
            'Image description generated',
            extra=event(
                'MEDIA_DESCRIBE',
                outcome='ok',
                kind='image',
                content_hash=image.content_hash,
                describer=current_describer(),
                desc_len=len(response.description or ''),
                ocr_len=len(response.ocr_text or ''),
                elapsed_ms=elapsed_ms(started),
            ),
        )
        return response
