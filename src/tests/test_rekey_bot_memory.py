from datetime import datetime, timedelta, UTC

from src import mongo, settings
from src.characters.registry import CHARACTERS
from src.chat_settings import repository as chat_settings_repository
from src.memory.models import MemoryData
from src.memory.repository import get_last_memory
from src.scripts.rekey_bot_memory import LEGACY_KEY, rekey, run

BOT_TRAITS = ['ник Элжан', 'цель — подъебать собеседника']
RECORD = {'born': '2026-08-24 13:06', 'cycles': 222, 'field': 'traits'}


def make_snapshot(participants, decay=None, chat_id=1, age_days=0):
    created_at = (datetime.now(UTC) - timedelta(days=age_days)).timestamp()
    return {
        'chat_id': chat_id,
        'content': {
            'participants': participants,
            'state': {'active_topics': [], 'open_questions': [], 'running_jokes': []},
        },
        'decay': decay or {},
        'created_at': created_at,
    }


def bot_participant():
    return {'traits': list(BOT_TRAITS), 'recent': []}


def other_participant():
    return {'traits': ['любит Елеке'], 'recent': []}


def tagged_key(code):
    return f'@{settings.BOT_NICKNAME}[{code}]'


async def current(chat_id=1) -> MemoryData:
    return await get_last_memory(chat_id)


async def test_the_legacy_participant_is_filed_under_the_chats_current_character():
    code = settings.DEFAULT_CHARACTER
    await chat_settings_repository.set_character_code(1, code)
    participants = {'@alice': other_participant(), LEGACY_KEY: bot_participant()}
    await mongo.memory.insert_one(make_snapshot(participants))

    await rekey(dry_run=False)

    memory = await current()
    assert LEGACY_KEY not in memory.content.participants
    assert memory.content.participants[tagged_key(code)].traits == BOT_TRAITS
    assert memory.content.participants['@alice'].traits == ['любит Елеке']


async def test_the_decay_sidecar_follows_the_rename_and_keeps_ages():
    code = settings.DEFAULT_CHARACTER
    await chat_settings_repository.set_character_code(1, code)
    decay = {LEGACY_KEY: {'ник элжан': RECORD}, '@alice': {'любит елеке': RECORD}}
    await mongo.memory.insert_one(
        make_snapshot({LEGACY_KEY: bot_participant(), '@alice': other_participant()}, decay)
    )

    await rekey(dry_run=False)

    memory = await current()
    assert LEGACY_KEY not in memory.decay
    assert memory.decay[tagged_key(code)]['ник элжан'].cycles == 222
    assert memory.decay['@alice']['любит елеке'].cycles == 222


async def test_participant_order_is_preserved():
    await chat_settings_repository.set_character_code(1, settings.DEFAULT_CHARACTER)
    participants = {
        '@a': other_participant(),
        LEGACY_KEY: bot_participant(),
        '@z': other_participant(),
    }
    await mongo.memory.insert_one(make_snapshot(participants))

    await rekey(dry_run=False)

    keys = list((await current()).content.participants)
    assert keys == ['@a', tagged_key(settings.DEFAULT_CHARACTER), '@z']


async def test_a_private_character_is_used_when_the_chat_is_on_it():
    private = next(code for code, c in CHARACTERS.items() if not c.public)
    await chat_settings_repository.set_character_code(1, private)
    await mongo.memory.insert_one(make_snapshot({LEGACY_KEY: bot_participant()}))

    await rekey(dry_run=False)

    assert tagged_key(private) in (await current()).content.participants


async def test_a_chat_without_a_stored_character_falls_back_to_the_default():
    await mongo.memory.insert_one(make_snapshot({LEGACY_KEY: bot_participant()}))

    await rekey(dry_run=False)

    assert tagged_key(settings.DEFAULT_CHARACTER) in (await current()).content.participants


async def test_only_the_newest_snapshot_is_touched():
    await mongo.memory.insert_one(make_snapshot({LEGACY_KEY: bot_participant()}, age_days=5))
    await mongo.memory.insert_one(make_snapshot({LEGACY_KEY: bot_participant()}))

    await rekey(dry_run=False)

    old = await mongo.memory.find_one({'chat_id': 1}, sort=[('created_at', 1)])
    assert LEGACY_KEY in old['content']['participants']
    assert LEGACY_KEY not in (await current()).content.participants


async def test_each_chat_gets_its_own_characters_tag():
    private = next(code for code, c in CHARACTERS.items() if not c.public)
    await chat_settings_repository.set_character_code(1, private)
    await chat_settings_repository.set_character_code(2, settings.DEFAULT_CHARACTER)
    await mongo.memory.insert_one(make_snapshot({LEGACY_KEY: bot_participant()}, chat_id=1))
    await mongo.memory.insert_one(make_snapshot({LEGACY_KEY: bot_participant()}, chat_id=2))

    await rekey(dry_run=False)

    assert tagged_key(private) in (await current(1)).content.participants
    assert tagged_key(settings.DEFAULT_CHARACTER) in (await current(2)).content.participants


async def test_an_existing_tagged_participant_is_never_overwritten(capsys):
    code = settings.DEFAULT_CHARACTER
    await chat_settings_repository.set_character_code(1, code)
    participants = {
        LEGACY_KEY: bot_participant(),
        tagged_key(code): {'traits': ['уже тут'], 'recent': []},
    }
    await mongo.memory.insert_one(make_snapshot(participants))

    result = await rekey(dry_run=False)

    after = (await current()).content.participants
    assert after[tagged_key(code)].traits == ['уже тут']
    assert LEGACY_KEY in after
    assert result['conflict'] == [(1, tagged_key(code))]
    assert 'already exists' in capsys.readouterr().out


async def test_a_snapshot_without_the_bot_is_left_alone():
    await mongo.memory.insert_one(make_snapshot({'@alice': other_participant()}))
    before = await mongo.memory.find_one({'chat_id': 1})

    result = await rekey(dry_run=False)

    assert await mongo.memory.find_one({'chat_id': 1}) == before
    assert result == {'renamed': [], 'conflict': []}


async def test_dry_run_reports_and_writes_nothing(capsys):
    await mongo.memory.insert_one(make_snapshot({LEGACY_KEY: bot_participant()}))
    before = await mongo.memory.find_one({'chat_id': 1})

    result = await rekey(dry_run=True)

    assert await mongo.memory.find_one({'chat_id': 1}) == before
    assert len(result['renamed']) == 1
    assert f'{LEGACY_KEY} ->' in capsys.readouterr().out


async def test_second_run_is_a_no_op():
    await mongo.memory.insert_one(make_snapshot({LEGACY_KEY: bot_participant()}))
    await rekey(dry_run=False)
    before = await mongo.memory.find_one({'chat_id': 1})

    result = await rekey(dry_run=False)

    assert await mongo.memory.find_one({'chat_id': 1}) == before
    assert result == {'renamed': [], 'conflict': []}


async def test_the_renamed_snapshot_still_loads_as_memory_data():
    # The point is that the next memory cycle can read it back.
    await mongo.memory.insert_one(
        make_snapshot({LEGACY_KEY: bot_participant()}, {LEGACY_KEY: {'ник элжан': RECORD}})
    )

    await rekey(dry_run=False)

    memory = await current()
    assert 'ник элжан' in next(iter(memory.decay.values()))
    assert 'ПАМЯТЬ' in memory.prompt_format()


async def test_run_prints_a_summary(capsys):
    await mongo.memory.insert_one(make_snapshot({LEGACY_KEY: bot_participant()}))

    await run(dry_run=True)

    out = capsys.readouterr().out
    assert 'DRY RUN' in out
    assert 'renamed: 1' in out
