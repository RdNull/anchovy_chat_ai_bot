import base64
import io
import os
from unittest.mock import AsyncMock, MagicMock, call

import cv2
import numpy as np
import pytest
from PIL import Image
from langchain_core.messages import HumanMessage, SystemMessage

from src.media.models import AnimationDetectionData, ImageDetectionData, MediaDescriptionData
from src.media.processors.animation import (
    MAX_COUNTED_FRAMES,
    _extract_gif_frames,
    _extract_tgs_frames,
    _extract_video_frames,
    _frame_indices,
    _image_to_base64,
    _resize_frame_if_needed,
    describe_animation,
)
from src.media.processors.image import current_describer as image_describer
from src.media.processors.animation import current_describer as animation_describer
from src.media.processors.image import describe_image
from src.media.processors.sticker import current_describer as sticker_describer
from src.media.processors.sticker import describe_sticker
from src.model_manager import model_manager

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


@pytest.fixture
def sample_zero_count_webm():
    path = os.path.join(MEDIA_DIR, 'zero_count_webm.webm')
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
    assert len(frames) == 8
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
    assert len(frames) == 8
    for i, frame_b64 in enumerate(frames):
        ref_path = os.path.join(MEDIA_DIR, f'laughing_toothless/frame_{i}.jpg')
        with open(ref_path, 'rb') as f:
            ref_data = f.read()
        assert base64.b64decode(frame_b64) == ref_data


def test_extract_video_frames_webm(sample_webm):
    frames = _extract_video_frames(sample_webm)
    assert isinstance(frames, list)
    assert len(frames) == 8
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
        content=base64.b64encode(sample_jpg).decode('utf-8'), format='jpg'
    )

    result = await describe_image(img_data)

    assert result.description == 'test desc'
    assert result.ocr_text == 'test ocr'
    assert mock_model.ainvoke.call_count == 1

    # Verify messages
    assert mock_model.ainvoke.call_args == call([
        SystemMessage(content='test prompt'),
        HumanMessage(
            content=[
                {
                    'type': 'image',
                    'mime_type': 'image/jpg',
                    'base64': base64.b64encode(sample_jpg).decode('utf-8'),
                }
            ]
        ),
    ])


async def test_describe_animation(mocker, sample_tgs):
    mock_model = MagicMock()
    mock_model.with_structured_output.return_value = mock_model
    mock_model.ainvoke = AsyncMock(
        return_value=MediaDescriptionData(description='anim desc', ocr_text='anim ocr')
    )

    mocker.patch('src.ai.get_animation_descriptor_model', return_value=mock_model)
    mocker.patch('src.prompt_manager.prompt_manager.get_prompt', return_value='anim prompt')

    anim_data = AnimationDetectionData(content=sample_tgs, format='tgs')

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
            'base64': frame_b64,
        })

    assert mock_model.ainvoke.call_args == call([
        SystemMessage(content='anim prompt'),
        HumanMessage(content=expected_human_content),
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

    assert _extract_video_frames(b'invalid data') == []


def test_extract_tgs_frames_error(mocker):
    # Test error handling in TGS extraction
    mocker.patch('src.media.processors.animation.import_tgs', side_effect=ValueError('bad tgs'))
    assert _extract_tgs_frames(b'bad data') == []


async def test_describe_animation_error(mocker):
    # Test LLM error in describe_animation
    mock_model = MagicMock()
    mock_model.with_structured_output.return_value = mock_model
    mock_model.ainvoke.side_effect = Exception('LLM fail')

    mocker.patch('src.ai.get_animation_descriptor_model', return_value=mock_model)
    mocker.patch('src.prompt_manager.prompt_manager.get_prompt', return_value='prompt')

    anim_data = AnimationDetectionData(content=b'data', format='gif')
    # Mocking _get_animation_key_frames to return something so it doesn't return None early
    mocker.patch('src.media.processors.animation._get_animation_key_frames', return_value=['f1'])

    result = await describe_animation(anim_data)
    assert result is None


async def test_describe_animation_no_frames(mocker):
    mocker.patch('src.media.processors.animation._get_animation_key_frames', return_value=[])
    anim_data = AnimationDetectionData(content=b'data', format='gif')
    result = await describe_animation(anim_data)
    assert result is None


def test_extract_video_frames_no_frames(mocker):
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    mock_cap.get.return_value = 0
    mock_cap.grab.return_value = False
    mocker.patch('cv2.VideoCapture', return_value=mock_cap)
    assert _extract_video_frames(b'data') == []


async def test_describe_image_error(mocker):
    mock_model = MagicMock()
    mock_model.with_structured_output.return_value = mock_model
    mock_model.ainvoke.side_effect = Exception('err')
    mocker.patch('src.ai.get_image_descriptor_model', return_value=mock_model)
    img_data = ImageDetectionData(content='b64', format='jpg')
    assert await describe_image(img_data) is None


def test_extract_tgs_frames_short(mocker):
    # Mock import_tgs to return a mock animation
    mock_anim = MagicMock()
    mock_anim.in_point = 0
    mock_anim.out_point = 5  # 6 frames total
    mocker.patch('src.media.processors.animation.import_tgs', return_value=mock_anim)

    # Mock Image.open to return a mock image with size
    mock_img = MagicMock()
    mock_img.size = (100, 100)
    mock_img.convert.return_value = mock_img

    # Use a side effect for PngRenderer to simulate serialization
    mock_renderer = MagicMock()
    # Ensure it works as a context manager
    mock_renderer.__enter__.return_value = mock_renderer
    mock_renderer.__exit__.return_value = False
    mocker.patch('src.media.processors.animation.PngRenderer', return_value=mock_renderer)

    # Patch Image.open in the module where it's imported
    mocker.patch('src.media.processors.animation.Image.open', return_value=mock_img)
    mocker.patch('src.media.processors.animation._image_to_base64', return_value='b64')

    # Mock _resize_frame_if_needed to just return the same mock image
    # and avoid its internal unpacking logic if it fails for some reason in mock
    mocker.patch('src.media.processors.animation._resize_frame_if_needed', return_value=mock_img)

    # 6 frames -> every frame
    frames = _extract_tgs_frames(b'data')

    assert len(frames) == 6
    assert frames[0] == 'b64'


async def test_describe_image_calls_exactly_what_the_stamp_names(mocker):
    llm = MagicMock()
    llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=MediaDescriptionData(description='x', ocr_text=None)
    )
    get_model = mocker.patch(
        'src.media.processors.image.ai.get_image_descriptor_model', return_value=llm
    )
    get_prompt = mocker.patch(
        'src.media.processors.image.prompt_manager.get_prompt', return_value='p'
    )

    await describe_image(ImageDetectionData(content='abc', format='jpg'))

    model_version = get_model.call_args.kwargs['version']
    (prompt_task,) = get_prompt.call_args.args
    prompt_version = get_prompt.call_args.kwargs['version']
    model = model_manager.get_model_settings('image_describe', model_version)['model']
    assert image_describer() == f'{prompt_task}/{prompt_version}@{model}'


async def test_describe_animation_calls_exactly_what_the_stamp_names(mocker):
    llm = MagicMock()
    llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=MediaDescriptionData(description='x', ocr_text=None)
    )
    get_model = mocker.patch(
        'src.media.processors.animation.ai.get_animation_descriptor_model', return_value=llm
    )
    get_prompt = mocker.patch(
        'src.media.processors.animation.prompt_manager.get_prompt', return_value='p'
    )
    mocker.patch('src.media.processors.animation._get_animation_key_frames', return_value=['f'])

    await describe_animation(AnimationDetectionData(content=b'x', format='gif'))

    model_version = get_model.call_args.kwargs['version']
    (prompt_task,) = get_prompt.call_args.args
    prompt_version = get_prompt.call_args.kwargs['version']
    model = model_manager.get_model_settings('animation_describe', model_version)['model']
    assert animation_describer() == f'{prompt_task}/{prompt_version}@{model}'


@pytest.mark.parametrize(
    ('num_frames', 'expected'),
    [
        (1, [0]),
        (2, [0, 1]),
        (4, [0, 1, 2, 3]),
        (8, [0, 1, 2, 3, 4, 5, 6, 7]),
        (9, [0, 1, 2, 3, 4, 5, 6, 8]),
        (10, [0, 1, 2, 3, 5, 6, 7, 9]),
        (90, [0, 12, 25, 38, 50, 63, 76, 89]),
    ],
)
def test_frame_indices(num_frames, expected):
    assert _frame_indices(num_frames) == expected


class _FakeCapture:
    """A `cv2.VideoCapture` stand-in: `decodable` real frames, `declared` claimed ones."""

    instances = []

    def __init__(self, declared, decodable):
        self.declared = declared
        self.decodable = decodable
        self.grabs = 0
        self.reads = []
        self.position = 0
        self.seeks = []
        _FakeCapture.instances.append(self)

    def isOpened(self):  # noqa: N802
        return True

    def get(self, _prop):
        return self.declared

    def set(self, _prop, value):
        self.seeks.append(value)
        self.position = value
        return True

    def grab(self):
        self.grabs += 1
        if self.position >= self.decodable:
            return False
        self.position += 1
        return True

    def retrieve(self):
        frame = np.zeros((2, 2, 3), dtype=np.uint8)
        self.reads.append(self.position - 1)
        return True, frame

    def read(self):
        if not self.grab():
            return False, None
        return self.retrieve()

    def release(self):
        pass


def _fake_video(mocker, declared, decodable):
    _FakeCapture.instances = []

    def factory(path):
        # each VideoCapture is a fresh decoder at position 0, as with the real one
        return _FakeCapture(declared, decodable)

    mocker.patch('cv2.VideoCapture', side_effect=factory)
    return _FakeCapture.instances


def _declared_count(video_bytes, tmp_path):
    path = tmp_path / 'probe.webm'
    path.write_bytes(video_bytes)
    cap = cv2.VideoCapture(str(path))
    try:
        return int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    finally:
        cap.release()


def test_extract_video_frames_zero_declared_count_still_decodes(sample_zero_count_webm, tmp_path):
    declared = _declared_count(sample_zero_count_webm, tmp_path)
    assert declared == 0
    frames = _extract_video_frames(sample_zero_count_webm)
    assert len(frames) == 8


def test_extract_video_frames_wrong_declared_count_spans_real_length(mocker):
    # declares 87, decodes 166: indices must come from the real count
    cams = _fake_video(mocker, declared=87, decodable=166)
    frames = _extract_video_frames(b'data')
    assert len(frames) == 8
    reader = cams[-1]
    assert reader.reads == [0, 23, 47, 70, 94, 117, 141, 165]


def test_extract_video_frames_declared_count_larger_than_real(mocker):
    # declares 30, decodes 24: last frame must not be dropped
    cams = _fake_video(mocker, declared=30, decodable=24)
    frames = _extract_video_frames(b'data')
    assert len(frames) == 8
    assert cams[-1].reads == [0, 3, 6, 9, 13, 16, 19, 23]


def test_extract_video_frames_short_video_sends_every_frame(mocker):
    cams = _fake_video(mocker, declared=3, decodable=2)
    frames = _extract_video_frames(b'data')
    assert len(frames) == 2
    assert cams[-1].reads == [0, 1]


def test_extract_video_frames_long_video_keeps_declared_count_path(mocker):
    cams = _fake_video(mocker, declared=400, decodable=400)
    frames = _extract_video_frames(b'data')
    assert len(frames) == 8
    counter = cams[1]
    assert counter.grabs == MAX_COUNTED_FRAMES + 1
    seeker = cams[2]
    assert seeker.seeks == [0, 57, 114, 171, 228, 285, 342, 399]


def test_extract_video_frames_long_video_with_zero_declared_returns_nothing(mocker):
    _fake_video(mocker, declared=0, decodable=400)
    assert _extract_video_frames(b'data') == []


@pytest.mark.parametrize('num_frames', [2, 4, 8, 9])
def test_extract_gif_frames_short_loop(num_frames):
    images = [Image.new('RGB', (4, 4), color=(i * 20, 0, 0)) for i in range(num_frames)]
    gif_io = io.BytesIO()
    images[0].save(gif_io, format='GIF', save_all=True, append_images=images[1:], duration=50)
    frames = _extract_gif_frames(gif_io.getvalue())
    assert len(frames) == min(num_frames, 8)


@pytest.mark.parametrize(('out_point', 'expected'), [(1, 2), (3, 4), (8, 8)])
def test_extract_tgs_frames_short_loop(mocker, out_point, expected):
    mock_anim = MagicMock()
    mock_anim.in_point = 0
    mock_anim.out_point = out_point
    mocker.patch('src.media.processors.animation.import_tgs', return_value=mock_anim)

    mock_img = MagicMock()
    mock_img.size = (100, 100)
    mock_img.convert.return_value = mock_img
    mock_renderer = MagicMock()
    mock_renderer.__enter__.return_value = mock_renderer
    mock_renderer.__exit__.return_value = False
    mocker.patch('src.media.processors.animation.PngRenderer', return_value=mock_renderer)
    mocker.patch('src.media.processors.animation.Image.open', return_value=mock_img)
    mocker.patch('src.media.processors.animation._image_to_base64', return_value='b64')
    mocker.patch('src.media.processors.animation._resize_frame_if_needed', return_value=mock_img)

    assert len(_extract_tgs_frames(b'data')) == expected


def _sticker_llm(mocker, result):
    llm = MagicMock()
    llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=result)
    get_model = mocker.patch(
        'src.media.processors.sticker.ai.get_sticker_descriptor_model', return_value=llm
    )
    get_prompt = mocker.patch(
        'src.media.processors.sticker.prompt_manager.get_prompt', return_value='p'
    )
    return llm, get_model, get_prompt


async def test_describe_sticker_calls_exactly_what_the_stamp_names(mocker):
    result = MediaDescriptionData(description='x', ocr_text=None)
    _, get_model, get_prompt = _sticker_llm(mocker, result)

    await describe_sticker(ImageDetectionData(content='abc', format='webp'))

    model_version = get_model.call_args.kwargs['version']
    (prompt_task,) = get_prompt.call_args.args
    prompt_version = get_prompt.call_args.kwargs['version']
    model = model_manager.get_model_settings('sticker_describe', model_version)['model']
    assert sticker_describer() == f'{prompt_task}/{prompt_version}@{model}'
    assert sticker_describer() == 'sticker_describe/v2@google/gemini-3.8-flash'


async def test_describe_sticker_sends_one_block_for_an_image(mocker):
    result = MediaDescriptionData(description='x', ocr_text=None)
    llm, _, _ = _sticker_llm(mocker, result)

    out = await describe_sticker(ImageDetectionData(content='abc', format='webp'))

    messages = llm.with_structured_output.return_value.ainvoke.call_args.args[0]
    blocks = messages[1].content_blocks
    assert out == result
    assert [b['mime_type'] for b in blocks] == ['image/webp']


async def test_describe_sticker_sends_the_frames_of_an_animation(mocker):
    result = MediaDescriptionData(description='x', ocr_text=None)
    llm, _, _ = _sticker_llm(mocker, result)
    mocker.patch(
        'src.media.processors.sticker._get_animation_key_frames', return_value=['f1', 'f2', 'f3']
    )

    out = await describe_sticker(AnimationDetectionData(content=b'x', format='webm'))

    messages = llm.with_structured_output.return_value.ainvoke.call_args.args[0]
    blocks = messages[1].content_blocks
    assert out == result
    assert [b['base64'] for b in blocks] == ['f1', 'f2', 'f3']


async def test_describe_sticker_animation_without_frames_returns_none(mocker, caplog):
    _, get_model, _ = _sticker_llm(mocker, None)
    mocker.patch('src.media.processors.sticker._get_animation_key_frames', return_value=[])

    with caplog.at_level('WARNING'):
        out = await describe_sticker(AnimationDetectionData(content=b'x', format='webm'))

    assert out is None
    assert get_model.call_count == 0
    assert [r.outcome for r in caplog.records if getattr(r, 'event', '') == 'MEDIA_FRAMES'] == [
        'empty'
    ]


async def test_describe_sticker_error_returns_none(mocker):
    llm, _, _ = _sticker_llm(mocker, None)
    llm.with_structured_output.return_value.ainvoke = AsyncMock(side_effect=RuntimeError('boom'))

    assert await describe_sticker(ImageDetectionData(content='abc', format='webp')) is None
