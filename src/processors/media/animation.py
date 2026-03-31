from langchain_core.messages import HumanMessage, ImageContentBlock, SystemMessage

from src import ai
from src.logs import logger
from src.models import AnimationDetectionData, MediaDescriptionData, ImageDetectionData

ANIMATION_DESCRIBE_PROMPT = '''
Ты — ассистент, который описывает анимацию (GIF или анимированные стикеры) для использования в контексте чата и в сводках.
Твоя задача — создавать КОРОТКОЕ, СТРУКТУРИРОВАННОЕ и ИНФОРМАТИВНОЕ описание на основе нескольких кадров из анимации.

Правила:
- Пиши кратко (максимум 20 слов в description)
- Используй только полезные и объективные детали
- Игнорируй художественный стиль, если он не важен
- НЕ выдумывай и НЕ додумывай детали
- НЕ добавляй эмоции, если они явно не видны
- Приоритет: объекты, действия, текст, контекст

Ты должен вернуть JSON строго в следующем формате:

{
  "description": "...",
  "ocr_text": "..."
}

Правила полей:
- description: короткое естественное описание (≤20 слов)
- ocr_text: любой читаемый текст (или пустая строка)

Особые случаи:
- Мем → опиши и анимацию, и смысл шутки
- NSFW → просто напиши "nsfw content" в description

Важно:
- Пиши ТОЛЬКО на русском языке
- Не добавляй ничего вне JSON
'''


async def describe_animation(animation: AnimationDetectionData) -> MediaDescriptionData | None:
    key_frames = _get_animation_key_frames(animation)

    llm = ai.get_animation_descriptor_model()
    model_with_structure = llm.with_structured_output(MediaDescriptionData)

    messages = [
        SystemMessage(content=ANIMATION_DESCRIBE_PROMPT),
        HumanMessage(content_blocks=[
            ImageContentBlock(
                type="image",
                mime_type='image/jpeg',
                base64=key_frame
            )
            for key_frame in key_frames
        ])
    ]

    try:
        response: MediaDescriptionData = await model_with_structure.ainvoke(messages)
        logger.info(
            "Image description generated for "
            f"{animation.content_hash}: {response.description}; {response.ocr_text}"
        )
        return response
    except Exception as e:
        logger.error(
            f"Error generating image description for image {animation.content_hash}: {e}",
            exc_info=True
        )

    return None


def _get_animation_key_frames(animation: AnimationDetectionData) -> list[str]:
    pass
