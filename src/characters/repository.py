import random
from pathlib import Path

import yaml

from src import settings
from src.characters.character import Character
from src.models import MemoryData, RelatedMessagesData

CHARACTERS = {}
for path in Path(settings.CHARACTERS_DIRECTORY).rglob('*.yaml'):
    with open(str(path), 'r') as f:
        character_data = yaml.safe_load(f)

    character_code = path.stem
    CHARACTERS[character_code] = Character(
        code=character_code,
        name=character_data['name'],
        display_name=character_data['display_name'],
        description=character_data['description'],
        style_prompt=character_data['prompt']
    )


def get_character(
    character_name: str = None,
    memory: MemoryData | None = None,
    related_messages: list[RelatedMessagesData] | None = None
) -> Character:
    character = CHARACTERS[character_name or random.choice(list(CHARACTERS.keys()))]
    character.memory = memory
    character.related_messages = related_messages
    return character
