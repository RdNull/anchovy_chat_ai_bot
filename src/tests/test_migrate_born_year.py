from src import mongo
from src.scripts.migrate_born_year import run, widen_decay


def record(born):
    return {'born': born, 'cycles': 1, 'field': 'recent'}


def test_widen_decay_rewrites_only_legacy_stamps():
    decay = {'@a': {'x': record('26-08-24 13:06'), 'y': record('2026-08-25 09:00')}}

    widened, changed = widen_decay(decay)

    assert changed == 1
    assert widened['@a']['x']['born'] == '2026-08-24 13:06'
    assert widened['@a']['y']['born'] == '2026-08-25 09:00'
    assert decay['@a']['x']['born'] == '26-08-24 13:06'


async def test_run_dry_run_writes_nothing():
    await mongo.memory.insert_one({'chat_id': 1, 'decay': {'@a': {'x': record('26-08-24 13:06')}}})

    await run(dry_run=True)

    doc = await mongo.memory.find_one({'chat_id': 1})
    assert doc['decay']['@a']['x']['born'] == '26-08-24 13:06'


async def test_run_widens_and_is_idempotent():
    await mongo.memory.insert_one({'chat_id': 1, 'decay': {'@a': {'x': record('26-08-24 13:06')}}})

    await run(dry_run=False)
    await run(dry_run=False)

    doc = await mongo.memory.find_one({'chat_id': 1})
    assert doc['decay']['@a']['x']['born'] == '2026-08-24 13:06'
