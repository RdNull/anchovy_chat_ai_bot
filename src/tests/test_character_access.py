from unittest.mock import AsyncMock

import pytest
from telegram import InlineKeyboardMarkup

from src import settings
from src.characters.character import Character
from src.characters.registry import (
    CHARACTERS,
    available_characters,
    check_default_character,
    get_available_characters,
)
from src.characters.loader import load_characters
from src.chat_settings import repository as chat_settings_repository
from src.messages import handlers

OWNER_ID = 555


def make_character(code, public=True):
    return Character(
        code=code,
        display_name=f'Name {code}',
        name=f'Name {code}',
        description='d',
        style_prompt='p',
        public=public,
    )


@pytest.fixture
def characters(mocker):
    """One public and two private characters, on top of whatever is really loaded."""
    extra = {
        'open': make_character('open'),
        'priv_a': make_character('priv_a', public=False),
        'priv_b': make_character('priv_b', public=False),
    }
    mocker.patch.dict(CHARACTERS, extra)
    return extra


@pytest.fixture
def owner(mocker):
    mocker.patch.object(settings, 'OWNER_USER_ID', str(OWNER_ID))


def keyboard_rows(markup: InlineKeyboardMarkup) -> list[tuple[str, str]]:
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]


def make_callback_update(make_update, data, user_id=OWNER_ID, chat_id=222):
    update = make_update(user_id=user_id, chat_id=chat_id)
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.edit_message_reply_markup = AsyncMock()
    return update


def make_command_update(make_update, user_id=OWNER_ID, chat_id=222):
    update = make_update(user_id=user_id, chat_id=chat_id)
    update.callback_query = None
    return update


# --- yaml loading (AC4) ---


def write_yaml(directory, code, extra=''):
    path = directory / f'{code}.yaml'
    path.write_text(f'name: n\ndisplay_name: d\ndescription: x\nprompt: p\n{extra}')


def test_yaml_with_public_false_loads_as_private(tmp_path):
    write_yaml(tmp_path, 'hidden', 'public: false\n')

    assert load_characters(tmp_path)['hidden'].public is False


def test_yaml_without_the_field_loads_as_public(tmp_path):
    write_yaml(tmp_path, 'plain')

    assert load_characters(tmp_path)['plain'].public is True


# --- DEFAULT_CHARACTER validation (AC3) ---


def test_default_character_check_accepts_a_public_character():
    check_default_character({'a': make_character('a')}, 'a')


def test_default_character_check_rejects_a_missing_character():
    with pytest.raises(RuntimeError, match='not a loaded character'):
        check_default_character({'a': make_character('a')}, 'b')


def test_default_character_check_rejects_a_private_character():
    with pytest.raises(RuntimeError, match='private'):
        check_default_character({'a': make_character('a', public=False)}, 'a')


def test_the_configured_default_is_a_loaded_public_character():
    assert CHARACTERS[settings.DEFAULT_CHARACTER].public is True


# --- availability (AC5) ---


def test_available_characters_is_public_plus_allowed_private(characters):
    available = available_characters(['priv_a'])

    assert 'open' in available
    assert 'priv_a' in available
    assert 'priv_b' not in available


async def test_get_available_characters_reads_the_chats_allow_list(characters):
    await chat_settings_repository.toggle_allowed_character(300, 'priv_b')

    assert 'priv_b' in await get_available_characters(300)
    assert 'priv_b' not in await get_available_characters(301)


async def test_list_shows_only_public_and_allowed_characters(characters, make_update, make_context):
    await chat_settings_repository.toggle_allowed_character(222, 'priv_a')
    update = make_update()

    await handlers.list_characters(update, make_context)

    rows = keyboard_rows(update.message.reply_text.call_args.kwargs['reply_markup'])
    codes = {data for _, data in rows}
    assert 'select_char:open' in codes
    assert 'select_char:priv_a' in codes
    assert 'select_char:priv_b' not in codes


async def test_random_only_picks_available_characters(
    characters, mocker, make_update, make_context
):
    only_private_a = {'priv_a': characters['priv_a']}
    mocker.patch('src.messages.handlers.get_available_characters', return_value=only_private_a)
    update = make_update()

    await handlers.random_character(update, make_context)

    assert await chat_settings_repository.get_character_code(222) == 'priv_a'


async def test_random_never_picks_a_private_character_that_is_not_allowed(
    characters, make_update, make_context
):
    for _ in range(40):
        update = make_update()
        await handlers.random_character(update, make_context)

        saved = await chat_settings_repository.get_character_code(222)
        assert CHARACTERS[saved].public


async def test_select_of_an_unavailable_character_changes_nothing(
    characters, make_update, make_context
):
    await chat_settings_repository.set_character_code(222, 'open')
    update = make_callback_update(make_update, 'select_char:priv_a', user_id=111)

    await handlers.select_character(update, make_context)

    assert await chat_settings_repository.get_character_code(222) == 'open'
    assert update.callback_query.answer.call_count == 1
    assert update.callback_query.answer.call_args.kwargs['text']
    assert update.callback_query.edit_message_text.call_count == 0


async def test_select_of_an_allowed_private_character_works(characters, make_update, make_context):
    await chat_settings_repository.toggle_allowed_character(222, 'priv_a')
    update = make_callback_update(make_update, 'select_char:priv_a', user_id=111)

    await handlers.select_character(update, make_context)

    assert await chat_settings_repository.get_character_code(222) == 'priv_a'


# --- chat_settings allow list ---


async def test_toggle_allowed_character_adds_then_removes():
    assert await chat_settings_repository.toggle_allowed_character(400, 'x') == ['x']
    assert await chat_settings_repository.toggle_allowed_character(400, 'y') == ['x', 'y']
    assert await chat_settings_repository.toggle_allowed_character(400, 'x') == ['y']
    assert await chat_settings_repository.get_allowed_characters(400) == ['y']


async def test_toggle_keeps_the_stored_character_code():
    await chat_settings_repository.set_character_code(401, 'open')

    await chat_settings_repository.toggle_allowed_character(401, 'x')

    assert await chat_settings_repository.get_character_code(401) == 'open'


# --- /characters (AC6) ---


async def test_characters_command_shows_a_toggle_per_private_character(
    characters, owner, make_update, make_context
):
    await chat_settings_repository.toggle_allowed_character(222, 'priv_a')
    update = make_command_update(make_update)

    await handlers.manage_character_access(update, make_context)

    rows = keyboard_rows(update.message.reply_text.call_args.kwargs['reply_markup'])
    private = [(text, data) for text, data in rows if data.startswith('char_access:')]
    assert (f'✅ {characters["priv_a"].display_name}', 'char_access:priv_a') in private
    assert (f'⬜ {characters["priv_b"].display_name}', 'char_access:priv_b') in private
    assert not any(data == 'char_access:open' for _, data in rows)
    assert rows[-1] == ('Готово', 'char_access_done')


async def test_characters_command_without_private_characters_replies_plainly(
    owner, mocker, make_update, make_context
):
    only_public = {c: v for c, v in CHARACTERS.items() if v.public}
    mocker.patch.dict(CHARACTERS, only_public, clear=True)
    update = make_command_update(make_update)

    await handlers.manage_character_access(update, make_context)

    assert update.message.reply_text.call_count == 1
    assert 'reply_markup' not in update.message.reply_text.call_args.kwargs


async def test_tap_toggles_the_code_and_rerenders_the_keyboard(
    characters, owner, make_update, make_context
):
    update = make_callback_update(make_update, 'char_access:priv_b')

    await handlers.character_access(update, make_context)

    assert await chat_settings_repository.get_allowed_characters(222) == ['priv_b']
    markup = update.callback_query.edit_message_reply_markup.call_args.kwargs['reply_markup']
    assert (f'✅ {characters["priv_b"].display_name}', 'char_access:priv_b') in keyboard_rows(
        markup
    )

    await handlers.character_access(update, make_context)

    assert await chat_settings_repository.get_allowed_characters(222) == []


async def test_tap_on_a_public_or_unknown_code_changes_nothing(
    characters, owner, make_update, make_context
):
    for code in ('open', 'ghost'):
        update = make_callback_update(make_update, f'char_access:{code}')

        await handlers.character_access(update, make_context)

        assert update.callback_query.edit_message_reply_markup.call_count == 0

    assert await chat_settings_repository.get_allowed_characters(222) == []


async def test_done_summarises_and_removes_the_keyboard(
    characters, owner, make_update, make_context
):
    await chat_settings_repository.toggle_allowed_character(222, 'priv_a')
    update = make_callback_update(make_update, 'char_access_done')

    await handlers.character_access(update, make_context)

    kwargs = update.callback_query.edit_message_text.call_args.kwargs
    text = update.callback_query.edit_message_text.call_args.args[0]
    assert characters['priv_a'].display_name in text
    assert characters['priv_b'].display_name not in text
    assert 'reply_markup' not in kwargs


# --- owner-only (AC7, AC12) ---


async def test_non_owner_command_gets_an_error_styled_reply_and_no_state_change(
    characters, owner, make_update, make_context
):
    update = make_command_update(make_update, user_id=111)

    await handlers.manage_character_access(update, make_context)

    assert update.effective_message.reply_text.call_count == 1
    assert update.message.reply_text.call_count == 0
    assert await chat_settings_repository.get_allowed_characters(222) == []


async def test_non_owner_tap_gets_a_query_answer_and_no_state_change(
    characters, owner, make_update, make_context
):
    update = make_callback_update(make_update, 'char_access:priv_a', user_id=111)

    await handlers.character_access(update, make_context)

    assert update.callback_query.answer.call_count == 1
    assert update.callback_query.answer.call_args.kwargs['text']
    assert update.callback_query.answer.call_args.kwargs['show_alert'] is False
    assert update.callback_query.edit_message_reply_markup.call_count == 0
    assert await chat_settings_repository.get_allowed_characters(222) == []


async def test_non_owner_cannot_finish_either(characters, owner, make_update, make_context):
    update = make_callback_update(make_update, 'char_access_done', user_id=111)

    await handlers.character_access(update, make_context)

    assert update.callback_query.edit_message_text.call_count == 0


async def test_nobody_is_owner_when_the_setting_is_unset(
    characters, mocker, make_update, make_context
):
    mocker.patch.object(settings, 'OWNER_USER_ID', None)
    update = make_callback_update(make_update, 'char_access:priv_a')

    await handlers.character_access(update, make_context)

    assert await chat_settings_repository.get_allowed_characters(222) == []


async def test_owner_denial_is_logged(characters, owner, caplog, make_update, make_context):
    update = make_command_update(make_update, user_id=111)

    with caplog.at_level('WARNING'):
        await handlers.manage_character_access(update, make_context)

    record = next(r for r in caplog.records if getattr(r, 'event', None) == 'ACCESS_DENIED')
    assert record.scope == 'owner'
