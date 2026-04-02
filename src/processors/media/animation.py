import base64
import io
from operator import index, inv

import math
import os
import tempfile
from typing import List

import cv2
from PIL import Image
from langchain_core.messages import HumanMessage, ImageContentBlock, SystemMessage

from src import ai
from src.logs import logger
from src.models import AnimationDetectionData, MediaDescriptionData
from src.prompt_manager import prompt_manager



async def describe_animation(animation: AnimationDetectionData) -> MediaDescriptionData | None:
    key_frames = _get_animation_key_frames(animation)
    if not key_frames:
        logger.warning(f"No key frames found for animation {animation.content_hash}")
        return None

    logger.info(
        f"Generating animation description ({len(key_frames)} frames) for animation {animation.content_hash}"
    )
    llm = ai.get_animation_descriptor_model()
    model_with_structure = llm.with_structured_output(MediaDescriptionData)

    messages = [
        SystemMessage(content=prompt_manager.get_prompt('animation_describe')),
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


def _get_animation_key_frames(animation: AnimationDetectionData) -> List[str]:
    """
    Extracts key frames from GIF or video (without sound).
    The number of frames depends on the animation length: 1 to 4.
    Each frame is resized to max 300,000 pixels and returned as base64-encoded JPEG.
    """
    if animation.format.lower() in {'gif',}:
        return _extract_gif_frames(animation.content)
    return _extract_video_frames(animation.content)


def _extract_gif_frames(gif_bytes: bytes) -> List[str]:
    frames = []
    try:
        with Image.open(io.BytesIO(gif_bytes)) as img:
            num_frames = getattr(img, "n_frames", 1)
            # Short animations (up to 5 frames) -> 1 key frame
            # Long animations -> up to 4 key frames
            if num_frames <= 10:
                indices = [0]
            else:
                indices = [0, num_frames // 3, 2 * num_frames // 3, num_frames - 1]

            # Remove duplicate indices for very short gifs that weren't caught by num_frames <= 5
            indices = sorted(list(set(indices)))

            for i in indices:
                img.seek(i)
                frame = img.convert("RGB")
                frame = _resize_frame_if_needed(frame)
                frames.append(_image_to_base64(frame))
    except Exception as e:
        logger.error(f"Error extracting frames from GIF: {e}", exc_info=True)
    return frames


def _extract_video_frames(video_bytes: bytes) -> List[str]:
    frames = []
    # OpenCV cannot read directly from BytesIO, need a temporary file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".tmp") as temp_video:
        temp_video.write(video_bytes)
        temp_video_path = temp_video.name

    try:
        cap = cv2.VideoCapture(temp_video_path)
        if not cap.isOpened():
            logger.error("Could not open video file with OpenCV")
            return []

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            return []

        if total_frames <= 10:
            indices = [0]
        else:
            indices = [0, total_frames // 3, 2 * total_frames // 3, total_frames - 1]

        indices = sorted(list(set(indices)))

        for i in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            ret, frame = cap.read()
            if ret:
                # BGR to RGB
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(frame_rgb)
                pil_img = _resize_frame_if_needed(pil_img)
                frames.append(_image_to_base64(pil_img))
        cap.release()
    except Exception as e:
        logger.error(f"Error extracting frames from video: {e}", exc_info=True)
    finally:
        if os.path.exists(temp_video_path):
            os.remove(temp_video_path)
    return frames


def _resize_frame_if_needed(img: Image.Image, max_pixels: int = 300_000) -> Image.Image:
    width, height = img.size
    pixels = width * height
    if pixels > max_pixels:
        ratio = math.sqrt(max_pixels / pixels)
        new_width = int(width * ratio)
        new_height = int(height * ratio)
        return img.resize((new_width, new_height), Image.Resampling.LANCZOS)
    return img


def _image_to_base64(img: Image.Image) -> str:
    with io.BytesIO() as output:
        img.save(output, format="JPEG", quality=85)
        return base64.b64encode(output.getvalue()).decode('utf-8')
