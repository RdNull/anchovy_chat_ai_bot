from pathlib import Path

import yaml

from src import settings
from src.characters.character import Character


def load_characters(
    directory: str | Path, include_debug: bool | None = None
) -> dict[str, Character]:
    """Loads every yaml under `directory`; `include_debug=None` follows the settings flag."""
    if include_debug is None:
        include_debug = settings.ENABLE_DEBUG_CHARACTER

    characters = {}
    for path in Path(directory).rglob('*.yaml'):
        with open(str(path)) as f:
            character_data = yaml.safe_load(f)

        character_code = path.stem
        if character_code == 'debug' and not include_debug:
            continue

        characters[character_code] = Character(
            code=character_code,
            name=character_data['name'],
            display_name=character_data['display_name'],
            description=character_data['description'],
            style_prompt=character_data['prompt'],
            public=character_data.get('public', True),
        )

    return characters
