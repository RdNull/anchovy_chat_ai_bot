from langchain_core.messages import HumanMessage, ImageContentBlock, SystemMessage

from src import ai
from src.logs import logger
from src.models import ImageDetectionData, MediaDescriptionData
from src.prompt_manager import prompt_manager


async def describe_image(image: ImageDetectionData) -> MediaDescriptionData | None:
    llm = ai.get_image_descriptor_model()
    model_with_structure = llm.with_structured_output(MediaDescriptionData)

    messages = [
        SystemMessage(content=prompt_manager.get_prompt('image_describe')),
        HumanMessage(content_blocks=[
            ImageContentBlock(
                type="image",
                mime_type=f'image/{image.format}',
                base64=image.content
            )
        ])
    ]

    try:
        response: MediaDescriptionData = await model_with_structure.ainvoke(messages)
        logger.info(
            "Image description generated for "
            f"{image.content_hash}: {response.description}; {response.ocr_text}"
        )
        return response
    except Exception as e:
        logger.error(
            f"Error generating image description for image {image.content_hash}: {e}",
            exc_info=True
        )

    return None
