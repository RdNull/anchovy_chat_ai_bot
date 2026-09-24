from src import settings
from src.characters.character import Character
from src.characters.loader import load_characters
from src.chat_settings import repository as chat_settings_repository
from src.memory.models import MemoryData

CHARACTERS = load_characters(settings.CHARACTERS_DIRECTORY)


def check_default_character(characters: dict[str, Character], default_code: str) -> None:
    """Raises if the default is not a loaded, public character. Called at startup (`bot.main`)."""
    default = characters.get(default_code)
    if default is None:
        raise RuntimeError(
            f'DEFAULT_CHARACTER {default_code!r} is not a loaded character '
            f'(loaded: {sorted(characters)})'
        )
    if not default.public:
        raise RuntimeError(f'DEFAULT_CHARACTER {default_code!r} is private; it must be public')


def available_characters(allowed_codes: list[str]) -> dict[str, Character]:
    """Public characters plus the private ones this chat's allow list names."""
    return {
        code: character
        for code, character in CHARACTERS.items()
        if character.public or code in allowed_codes
    }


async def get_available_characters(chat_id: int) -> dict[str, Character]:
    allowed = await chat_settings_repository.get_allowed_characters(chat_id)
    return available_characters(allowed)


def get_character(character_code: str, memory: MemoryData | None = None) -> Character:
    # TODO: `CHARACTERS` holds one `Character` singleton per code, and this stamps
    # `.memory` onto it — two chats sharing a character can race between this
    # assignment and the read in `respond()`, each seeing the other's snapshot.
    # Pre-existing, not introduced by this refactor; fixing it means a per-call
    # copy of `Character` (or moving `memory` off the instance entirely), not a
    # smaller edit here.
    character = CHARACTERS[character_code]
    character.memory = memory
    return character


async def set_chat_character(chat_id: int, character_code: str) -> None:
    await chat_settings_repository.set_character_code(chat_id, character_code)


async def get_chat_character(
    chat_id: int,
    memory: MemoryData | None = None,
):
    character_code = await chat_settings_repository.get_character_code(chat_id)
    available = await get_available_characters(chat_id)
    if character_code not in available:
        character_code = settings.DEFAULT_CHARACTER

    character = get_character(character_code, memory=memory)
    await set_chat_character(chat_id, character.code)
    return character
