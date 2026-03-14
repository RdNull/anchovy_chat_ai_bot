from pathlib import Path

import yaml

from src import settings
from src.characters.character import Character

CHARACTERS = {}
for path in Path(settings.CHARACTERS_DIRECTORY).rglob('*.yaml'):
    with open(str(path), 'r') as f:
        character_data = yaml.safe_load(f)

    CHARACTERS[path.stem] = Character(
        name=character_data['name'],
        description=character_data['description'],
        style_prompt=character_data['prompt']
    )


def get_character(character_name: str = None, last_messages_recap: str | None = None) -> Character:
    character = CHARACTERS[character_name or next(iter(CHARACTERS))]
    character.last_messages_recap = last_messages_recap
    return character
