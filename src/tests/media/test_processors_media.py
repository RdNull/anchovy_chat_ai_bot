import base64
import io
import os
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from PIL import Image
from langchain_core.messages import HumanMessage, SystemMessage

from src.models import AnimationDetectionData, ImageDetectionData, MediaDescriptionData
from src.processors.media.animation import (
    _extract_gif_frames, _extract_tgs_frames,
    _extract_video_frames, _image_to_base64, _resize_frame_if_needed, describe_animation,
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
    assert mock_model.ainvoke.call_args == call([
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

    assert mock_model.ainvoke.call_args == call([
        SystemMessage(content='anim prompt'),
        HumanMessage(content=expected_human_content)
    ])


def test_extract_gif_frames_short():
    # Create a 1x1 1-frame GIF
    img = Image.new('RGB', (1, 1), color='red')
    gif_io = io.BytesIO()
    img.save(gif_io, format='GIF')
    frames = _extract_gif_frames(gif_io.getvalue())
    assert len(frames) == 1


def test_extract_video_frames_error(mocker):
    # Test error handling when cap.isOpened() is False
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = False
    mocker.patch('cv2.VideoCapture', return_value=mock_cap)

    assert _extract_video_frames(b"invalid data") == []


def test_extract_tgs_frames_error(mocker):
    # Test error handling in TGS extraction
    mocker.patch('src.processors.media.animation.import_tgs', side_effect=ValueError("bad tgs"))
    assert _extract_tgs_frames(b"bad data") == []


async def test_describe_animation_error(mocker):
    # Test LLM error in describe_animation
    mock_model = MagicMock()
    mock_model.with_structured_output.return_value = mock_model
    mock_model.ainvoke.side_effect = Exception("LLM fail")

    mocker.patch('src.ai.get_animation_descriptor_model', return_value=mock_model)
    mocker.patch('src.prompt_manager.prompt_manager.get_prompt', return_value='prompt')

    anim_data = AnimationDetectionData(content=b"data", format='gif')
    # Mocking _get_animation_key_frames to return something so it doesn't return None early
    mocker.patch('src.processors.media.animation._get_animation_key_frames', return_value=['f1'])

    result = await describe_animation(anim_data)
    assert result is None


async def test_describe_animation_no_frames(mocker):
    mocker.patch('src.processors.media.animation._get_animation_key_frames', return_value=[])
    anim_data = AnimationDetectionData(content=b"data", format='gif')
    result = await describe_animation(anim_data)
    assert result is None


def test_extract_video_frames_no_frames(mocker):
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    mock_cap.get.return_value = 0
    mocker.patch('cv2.VideoCapture', return_value=mock_cap)
    assert _extract_video_frames(b"data") == []


async def test_describe_image_error(mocker):
    mock_model = MagicMock()
    mock_model.with_structured_output.return_value = mock_model
    mock_model.ainvoke.side_effect = Exception("err")
    mocker.patch('src.ai.get_image_descriptor_model', return_value=mock_model)
    img_data = ImageDetectionData(content="b64", format="jpg")
    assert await describe_image(img_data) is None


def test_extract_tgs_frames_short(mocker):
    # Mock import_tgs to return a mock animation
    mock_anim = MagicMock()
    mock_anim.in_point = 0
    mock_anim.out_point = 5  # 6 frames total
    mocker.patch('src.processors.media.animation.import_tgs', return_value=mock_anim)

    # Mock Image.open to return a mock image with size
    mock_img = MagicMock()
    mock_img.size = (100, 100)
    mock_img.convert.return_value = mock_img

    # Use a side effect for PngRenderer to simulate serialization
    mock_renderer = MagicMock()
    # Ensure it works as a context manager
    mock_renderer.__enter__.return_value = mock_renderer
    mock_renderer.__exit__.return_value = False
    mocker.patch('src.processors.media.animation.PngRenderer', return_value=mock_renderer)

    # Patch Image.open in the module where it's imported
    mocker.patch('src.processors.media.animation.Image.open', return_value=mock_img)
    mocker.patch('src.processors.media.animation._image_to_base64', return_value="b64")

    # Mock _resize_frame_if_needed to just return the same mock image
    # and avoid its internal unpacking logic if it fails for some reason in mock
    mocker.patch('src.processors.media.animation._resize_frame_if_needed', return_value=mock_img)

    # Should use only start index
    # animation.py:86: num_frames = animation.out_point - animation.in_point + 1 = 6
    # animation.py:88: if num_frames < 10: indices = [start] where start = animation.in_point = 0
    frames = _extract_tgs_frames(b"data")

    assert len(frames) == 1
    assert frames[0] == "b64"
