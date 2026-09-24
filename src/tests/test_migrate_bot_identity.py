from datetime import datetime, timedelta, UTC

import pytest

from src import mongo, settings
from src.chat_settings import repository as chat_settings_repository
from src.characters.registry import CHARACTERS
from src.scripts.migrate_bot_identity import load_name_to_code, run

BOT = settings.BOT_NICKNAME
DAYS_AGO_10 = (datetime.now(UTC) - timedelta(days=10)).timestamp()
DAYS_AGO_200 = (datetime.now(UTC) - timedelta(days=200)).timestamp()


def name_and_code():
    code = settings.DEFAULT_CHARACTER
    return CHARACTERS[code].name, code


async def insert(**fields):
    doc = {'chat_id': 1, 'role': 'ai', 'text': 't', 'created_at': DAYS_AGO_10, **fields}
    result = await mongo.messages.insert_one(doc)
    return result.inserted_id


async def fetch(message_id):
    return await mongo.messages.find_one({'_id': message_id})


def test_name_to_code_maps_every_loaded_character_name():
    mapping = load_name_to_code()

    name, code = name_and_code()
    assert mapping[name] == code


async def test_legacy_bot_message_gets_code_and_tagged_nickname():
    name, code = name_and_code()
    message_id = await insert(nickname=f'{BOT}({name})')

    await run(days=90, dry_run=False)

    doc = await fetch(message_id)
    assert doc['character_code'] == code
    assert doc['nickname'] == f'{BOT}[{code}]'


async def test_dry_run_writes_nothing(capsys):
    name, code = name_and_code()
    message_id = await insert(nickname=f'{BOT}({name})')
    reacted_id = await insert(role='user', nickname='u', reactions={'🤡': [BOT]})

    await run(days=90, dry_run=True)

    assert (await fetch(message_id))['nickname'] == f'{BOT}({name})'
    assert 'character_code' not in await fetch(message_id)
    assert (await fetch(reacted_id))['reactions'] == {'🤡': [BOT]}
    out = capsys.readouterr().out
    assert 'DRY RUN' in out
    assert code in out


async def test_second_run_is_a_no_op(capsys):
    name, _ = name_and_code()
    await insert(nickname=f'{BOT}({name})')
    await insert(role='user', nickname='u', reactions={'🤡': [BOT, 'dima']})
    await run(days=90, dry_run=False)
    before = [d async for d in mongo.messages.find({}).sort('_id')]
    capsys.readouterr()

    await run(days=90, dry_run=False)

    after = [d async for d in mongo.messages.find({}).sort('_id')]
    assert after == before
    assert 'none' in capsys.readouterr().out


async def test_messages_older_than_the_window_are_left_alone():
    name, _ = name_and_code()
    message_id = await insert(nickname=f'{BOT}({name})', created_at=DAYS_AGO_200)

    await run(days=90, dry_run=False)

    assert (await fetch(message_id))['nickname'] == f'{BOT}({name})'


async def test_unmatched_names_are_left_as_is_and_counted(capsys):
    message_id = await insert(nickname=f'{BOT}(Никто Такой)')

    await run(days=90, dry_run=False)

    assert (await fetch(message_id))['nickname'] == f'{BOT}(Никто Такой)'
    assert 'Никто Такой' in capsys.readouterr().out


async def test_user_messages_are_never_touched():
    name, _ = name_and_code()
    message_id = await insert(role='user', nickname=f'{BOT}({name})')

    await run(days=90, dry_run=False)

    assert 'character_code' not in await fetch(message_id)


@pytest.fixture
def two_codes():
    codes = list(CHARACTERS)
    assert len(codes) >= 2
    return codes[:2]


async def test_plain_bot_reactions_go_to_the_chats_current_character(two_codes):
    current = two_codes[1]
    await chat_settings_repository.set_character_code(1, current)
    message_id = await insert(
        role='user', nickname='u', reactions={'🤡': [BOT, 'dima'], '👍': ['x']}
    )

    await run(days=90, dry_run=False)

    reactions = (await fetch(message_id))['reactions']
    assert reactions == {'🤡': [f'{BOT}[{current}]', 'dima'], '👍': ['x']}


async def test_reactions_fall_back_to_the_default_character_for_a_chat_without_settings():
    message_id = await insert(chat_id=77, role='user', nickname='u', reactions={'🤡': [BOT]})

    await run(days=90, dry_run=False)

    reactions = (await fetch(message_id))['reactions']
    assert reactions == {'🤡': [f'{BOT}[{settings.DEFAULT_CHARACTER}]']}
