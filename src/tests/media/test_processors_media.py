import base64
import os
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image
from langchain_core.messages import HumanMessage, SystemMessage

from src.models import AnimationDetectionData, ImageDetectionData, MediaDescriptionData
from src.processors.media.animation import (
    _extract_tgs_frames, _extract_video_frames, _image_to_base64, _resize_frame_if_needed,
    describe_animation,
)
from src.processors.media.image import describe_image

MEDIA_DIR = 'src/tests/media/data'


@pytest.fixture
def sample_jpg():
    path = os.path.join(MEDIA_DIR, 'unsettled_tom/source.jpg')
    with open(path, 'rb') as f:
        return f.read()


@pytest.fixture
def sample_tgs():
    path = os.path.join(MEDIA_DIR, 'sample_tgs/source.tgs')
    with open(path, 'rb') as f:
        return f.read()


@pytest.fixture
def sample_mp4():
    path = os.path.join(MEDIA_DIR, 'laughing_toothless/source.mp4')
    with open(path, 'rb') as f:
        return f.read()


@pytest.fixture
def sample_webm():
    path = os.path.join(MEDIA_DIR, 'fat_horse/source.webm')
    with open(path, 'rb') as f:
        return f.read()


def test_resize_frame_if_needed():
    # Create a large image
    img = Image.new('RGB', (2000, 2000))
    resized = _resize_frame_if_needed(img, max_pixels=300_000)
    assert resized.size[0] * resized.size[1] <= 300_000

    # Small image should not be resized
    small_img = Image.new('RGB', (100, 100))
    not_resized = _resize_frame_if_needed(small_img, max_pixels=300_000)
    assert not_resized.size == (100, 100)


def test_image_to_base64():
    # Create red 10x10 image
    img = Image.new('RGB', (10, 10), color='red')
    b64 = _image_to_base64(img)
    assert isinstance(b64, str)
    assert len(b64) > 0

    # Compare with reference
    ref_path = os.path.join(MEDIA_DIR, 'red_10x10.jpg')
    with open(ref_path, 'rb') as f:
        ref_data = f.read()
    assert base64.b64decode(b64) == ref_data


def test_extract_tgs_frames(sample_tgs):
    frames = _extract_tgs_frames(sample_tgs)
    assert isinstance(frames, list)
    assert len(frames) == 4
    for i, frame_b64 in enumerate(frames):
        assert isinstance(frame_b64, str)
        # Compare with reference
        ref_path = os.path.join(MEDIA_DIR, f'sample_tgs/frame_{i}.jpg')
        with open(ref_path, 'rb') as f:
            ref_data = f.read()
        assert base64.b64decode(frame_b64) == ref_data


def test_extract_video_frames_mp4(sample_mp4):
    frames = _extract_video_frames(sample_mp4)
    assert isinstance(frames, list)
    assert len(frames) == 4
    for i, frame_b64 in enumerate(frames):
        ref_path = os.path.join(MEDIA_DIR, f'laughing_toothless/frame_{i}.jpg')
        with open(ref_path, 'rb') as f:
            ref_data = f.read()
        assert base64.b64decode(frame_b64) == ref_data


def test_extract_video_frames_webm(sample_webm):
    frames = _extract_video_frames(sample_webm)
    assert isinstance(frames, list)
    assert len(frames) == 4
    for i, frame_b64 in enumerate(frames):
        ref_path = os.path.join(MEDIA_DIR, f'fat_horse/frame_{i}.jpg')
        with open(ref_path, 'rb') as f:
            ref_data = f.read()
        assert base64.b64decode(frame_b64) == ref_data


async def test_describe_image(mocker, sample_jpg):
    mock_model = MagicMock()
    mock_model.with_structured_output.return_value = mock_model
    mock_model.ainvoke = AsyncMock(
        return_value=MediaDescriptionData(description='test desc', ocr_text='test ocr')
    )

    mocker.patch('src.ai.get_image_descriptor_model', return_value=mock_model)
    mocker.patch('src.prompt_manager.prompt_manager.get_prompt', return_value='test prompt')

    img_data = ImageDetectionData(
        content=base64.b64encode(sample_jpg).decode('utf-8'),
        format='jpg'
    )

    result = await describe_image(img_data)

    assert result.description == 'test desc'
    assert result.ocr_text == 'test ocr'
    assert mock_model.ainvoke.call_count == 1

    # Verify messages
    mock_model.ainvoke.assert_called_once_with([
        SystemMessage(content='test prompt'),
        HumanMessage(content=[
            {
                'type': 'image',
                'mime_type': 'image/jpg',
                'base64': base64.b64encode(sample_jpg).decode('utf-8')
            }
        ])
    ])


async def test_describe_animation(mocker, sample_tgs):
    mock_model = MagicMock()
    mock_model.with_structured_output.return_value = mock_model
    mock_model.ainvoke = AsyncMock(
        return_value=MediaDescriptionData(description='anim desc', ocr_text='anim ocr'))

    mocker.patch('src.ai.get_animation_descriptor_model', return_value=mock_model)
    mocker.patch('src.prompt_manager.prompt_manager.get_prompt', return_value='anim prompt')

    anim_data = AnimationDetectionData(
        content=sample_tgs,
        format='tgs'
    )

    result = await describe_animation(anim_data)

    assert result.description == 'anim desc'
    assert result.ocr_text == 'anim ocr'
    assert mock_model.ainvoke.call_count == 1

    # Verify messages
    expected_human_content = []
    frames = _extract_tgs_frames(sample_tgs)
    for frame_b64 in frames:
        expected_human_content.append({
            'type': 'image',
            'mime_type': 'image/jpeg',
            'base64': frame_b64
        })

    mock_model.ainvoke.assert_called_once_with([
        SystemMessage(content='anim prompt'),
        HumanMessage(content=expected_human_content)
    ])
