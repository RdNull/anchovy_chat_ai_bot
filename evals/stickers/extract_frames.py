# ruff: noqa: INP001
"""Extract sticker frames for the sticker eval, the way prod does.

Run once by hand from the repo root with the project venv:
    TELEGRAM_TOKEN=x DATABASE_URL=x uv run python -m evals.stickers.extract_frames

The two dummy vars are there because importing the prod frame code pulls in
`src.settings`, which requires them; the values are never used.
"""

import base64
import shutil
import sys
from pathlib import Path

import yaml

from src.media.models import AnimationDetectionData
from src.media.processors.animation import _get_animation_key_frames

_ROOT = Path(__file__).parent
_FRAMES = _ROOT / 'frames'
_SOURCES = [_ROOT / 'public', _ROOT / 'fixtures']


def _referenced_files(source: Path) -> list[Path]:
    cases_path = source / 'cases.yaml'
    if not cases_path.exists():
        return []
    cases = yaml.safe_load(cases_path.read_text())
    return [source / case['file'] for case in cases]


def _extract(path: Path) -> int:
    unique_id = path.stem
    suffix = path.suffix.lower().lstrip('.')
    if suffix == 'webp':
        shutil.copyfile(path, _FRAMES / f'{unique_id}_0.webp')
        return 1

    animation = AnimationDetectionData(content=path.read_bytes(), format=suffix)
    key_frames = _get_animation_key_frames(animation)
    for n, key_frame in enumerate(key_frames):
        (_FRAMES / f'{unique_id}_{n}.jpg').write_bytes(base64.b64decode(key_frame))
    return len(key_frames)


def main():
    _FRAMES.mkdir(exist_ok=True)
    failed = []
    for source in _SOURCES:
        for path in _referenced_files(source):
            count = _extract(path)
            print(f'{path.name}: {count}')  # noqa: T201
            if count == 0:
                failed.append(path.name)
    if failed:
        sys.exit(f'No frames for: {", ".join(failed)}')


if __name__ == '__main__':
    main()
