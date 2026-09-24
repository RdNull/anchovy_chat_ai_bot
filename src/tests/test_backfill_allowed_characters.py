import pytest

from src import mongo, settings
from src.characters.registry import CHARACTERS
from src.chat_settings import repository as chat_settings_repository
from src.scripts.backfill_allowed_characters import backfill, private_codes, run


@pytest.fixture
def private_code():
    codes = [code for code, character in CHARACTERS.items() if not character.public]
    assert codes, 'the repo ships at least one private character'
    return codes[0]


async def allowed(chat_id):
    return await chat_settings_repository.get_allowed_characters(chat_id)


def test_the_default_character_is_public():
    assert settings.DEFAULT_CHARACTER not in private_codes()


def test_private_codes_includes_debug_even_when_its_flag_is_off():
    assert 'debug' not in CHARACTERS
    assert 'debug' in private_codes()


async def test_a_chat_on_a_private_character_is_allowed_it(private_code):
    await chat_settings_repository.set_character_code(1, private_code)

    await backfill(dry_run=False)

    assert await allowed(1) == [private_code]


async def test_a_chat_on_a_debug_character_is_allowed_it_although_debug_is_not_loaded():
    await chat_settings_repository.set_character_code(2, 'debug')

    await backfill(dry_run=False)

    assert await allowed(2) == ['debug']


async def test_a_chat_on_a_public_character_gets_nothing():
    await chat_settings_repository.set_character_code(3, settings.DEFAULT_CHARACTER)

    granted = await backfill(dry_run=False)

    assert await allowed(3) == []
    assert not granted


async def test_a_chat_without_a_stored_character_is_skipped():
    await mongo.chat_settings.insert_one({'chat_id': 4})

    await backfill(dry_run=False)

    assert await allowed(4) == []


async def test_existing_allow_list_entries_are_kept(private_code):
    await chat_settings_repository.set_character_code(5, private_code)
    await chat_settings_repository.toggle_allowed_character(5, 'something_else')

    await backfill(dry_run=False)

    assert sorted(await allowed(5)) == sorted(['something_else', private_code])


async def test_dry_run_writes_nothing_and_reports(private_code, capsys):
    await chat_settings_repository.set_character_code(6, private_code)

    granted = await backfill(dry_run=True)

    assert await allowed(6) == []
    assert granted == {private_code: 1}
    assert f'chat 6: allow {private_code}' in capsys.readouterr().out


async def test_second_run_is_a_no_op(private_code):
    await chat_settings_repository.set_character_code(7, private_code)
    await backfill(dry_run=False)
    before = await mongo.chat_settings.find_one({'chat_id': 7})

    granted = await backfill(dry_run=False)

    assert not granted
    assert await mongo.chat_settings.find_one({'chat_id': 7}) == before


async def test_the_backfilled_character_survives_get_chat_character(private_code):
    # The point of the script: without it the first reply after the deploy resets the chat.
    from src.characters.registry import get_chat_character

    await chat_settings_repository.set_character_code(8, private_code)
    await backfill(dry_run=False)

    character = await get_chat_character(8)

    assert character.code == private_code


async def test_the_chat_would_have_been_reset_without_the_backfill(private_code):
    from src.characters.registry import get_chat_character

    await chat_settings_repository.set_character_code(9, private_code)

    character = await get_chat_character(9)

    assert character.code == settings.DEFAULT_CHARACTER


async def test_run_prints_a_summary(private_code, capsys):
    await chat_settings_repository.set_character_code(10, private_code)

    await run(dry_run=True)

    out = capsys.readouterr().out
    assert 'DRY RUN' in out
    assert private_code in out
