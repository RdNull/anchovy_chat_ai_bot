from langchain_core.messages import HumanMessage, ImageContentBlock, SystemMessage

from src import ai
from src.db import media_descriptions
from src.logs import logger
from src.models import ImageDetectionData, ImageDetectionResult

IMAGE_DESCRIBE_PROMPT = '''
Ты — ассистент, который описывает изображения для использования в контексте чата и в сводках.
Твоя задача — создавать КОРОТКОЕ, СТРУКТУРИРОВАННОЕ и ИНФОРМАТИВНОЕ описание.

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
- ocr_text: любой читаемый текст на изображении (или пустая строка)

Особые случаи:
- Мем → опиши и изображение, и смысл шутки
- Скриншот → кратко передай суть интерфейса/содержимого
- Скриншот чата → передай тему разговора
- NSFW → просто напиши "nsfw content" в description

Важно:
- Пиши ТОЛЬКО на русском языке
- Не добавляй ничего вне JSON
'''


async def describe_image(image: ImageDetectionData) -> ImageDetectionResult | None:

    if media_description := await _get_media_description(image):
        logger.info(
            f"Image description found for {image.content_hash}: {media_description.description}"
        )
        return media_description

    llm = ai.get_image_descriptor_model()
    model_with_structure = llm.with_structured_output(ImageDetectionResult)

    messages = [
        SystemMessage(content=IMAGE_DESCRIBE_PROMPT),
        HumanMessage(content_blocks=[
            ImageContentBlock(
                type="image",
                mime_type=image.format,
                base64=image.content
            )
        ])
    ]

    try:
        response: ImageDetectionResult = await model_with_structure.ainvoke(messages)
        logger.info(
            "Image description generated for "
            f"{image.content_hash}: {response.description}; {response.ocr_text}"
        )
        await _save_media_description(image, response.description, response.ocr_text)
    except Exception as e:
        logger.error(
            f"Error generating image description for image {image.content_hash}: {e}",
            exc_info=True)

    return None


async def _get_media_description(image_data: ImageDetectionData) -> ImageDetectionResult | None:
    media_description = await media_descriptions.find_one({'hash': image_data.content_hash})
    if not media_description:
        return None

    return ImageDetectionResult(
        description=media_description['description'],
        ocr_text=media_description['ocr_text']
    )


async def _save_media_description(
    image_data: ImageDetectionData,
    description: str,
    ocr_text: str | None = None,
):
    await media_descriptions.insert_one({
        'hash': image_data.content_hash,
        'description': description,
        'ocr_text': ocr_text
    })
