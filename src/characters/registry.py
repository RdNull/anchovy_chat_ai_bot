import random
from pathlib import Path

import yaml

from src import settings
from src.characters.character import Character
from src.chat_settings import repository as chat_settings_repository
from src.memory.models import MemoryData

CHARACTERS = {}
for path in Path(settings.CHARACTERS_DIRECTORY).rglob('*.yaml'):
    with open(str(path), 'r') as f:
        character_data = yaml.safe_load(f)

    character_code = path.stem
    if character_code == 'debug' and not settings.ENABLE_DEBUG_CHARACTER:
        continue

    CHARACTERS[character_code] = Character(
        code=character_code,
        name=character_data['name'],
        display_name=character_data['display_name'],
        description=character_data['description'],
        style_prompt=character_data['prompt'],
    )


def get_character(
    character_name: str = None,
    memory: MemoryData | None = None,
) -> Character:
    # TODO: `CHARACTERS` holds one `Character` singleton per code, and this stamps
    # `.memory` onto it — two chats sharing a character can race between this
    # assignment and the read in `respond()`, each seeing the other's snapshot.
    # Pre-existing, not introduced by this refactor; fixing it means a per-call
    # copy of `Character` (or moving `memory` off the instance entirely), not a
    # smaller edit here.
    character = CHARACTERS[character_name or random.choice(list(CHARACTERS.keys()))]
    character.memory = memory
    return character


async def set_chat_character(chat_id: int, character_code: str) -> None:
    await chat_settings_repository.set_character_code(chat_id, character_code)


async def get_chat_character(
    chat_id: int,
    memory: MemoryData | None = None,
):
    character_code = await chat_settings_repository.get_character_code(chat_id)
    character = get_character(character_code, memory=memory)
    await set_chat_character(chat_id, character.code)
    return character
