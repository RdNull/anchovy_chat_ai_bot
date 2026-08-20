from datetime import datetime, timedelta, timezone

from src import settings
from src.memory.decay import DecayCaps, apply_decay, reconcile, resolve_now, summarize_churn
from src.memory.dedup import ConflictRecord
from src.memory.models import ChatState, DecayRecord, MemoryData, ParticipantInfo, StructuredMemory
from src.memory.processors import _prompt_memory
from src.models import format_ts

NOW = '26-05-01 18:00'
YESTERDAY = '26-04-30 18:00'
LAST_WEEK = '26-04-24 18:00'


def caps(**overrides) -> DecayCaps:
    """Caps for a test, generous enough that only the stated limit ever binds.

    Spelled out rather than read from `settings` on purpose: a test asserting
    eviction behaviour must not change meaning when a deployment default moves.
    `test_decay_ships_disabled` and `test_caps_come_from_settings` cover the real
    values and the wiring between them.
    """
    values = {
        'enabled': False,
        'traits_keep': 10,
        'recent_keep': 5,
        'max_cycles': 20,
        'topics_keep': 3,
        'questions_keep': 5,
        'jokes_keep': 5,
    }
    return DecayCaps(**{**values, **overrides})


def make_memory(**participants: ParticipantInfo) -> StructuredMemory:
    return StructuredMemory(participants={f'@{nick}': info for nick, info in participants.items()})


def born(**entries: str) -> dict[str, DecayRecord]:
    return {key: DecayRecord(born=stamp, cycles=0, field='recent') for key, stamp in entries.items()}


def cycle(updated, prior, guard_records=None, now=NOW, **overrides):
    """Runs one memory cycle in the order `extract_memory` runs it.

    Churn is measured last, against the memory that survived eviction — asserting it
    on `reconcile`'s output would re-create the bug where a deleted entry counted as
    carried.

    Returns:
        `(decay, evictions, churn)`.
    """
    decay = reconcile(updated, prior, now)
    evictions = apply_decay(updated, decay, caps(**overrides))
    churn = summarize_churn(updated, prior, decay, guard_records or [], evictions)
    return decay, evictions, churn


def events(churn) -> dict[str, str]:
    return {record.key: record.event for record in churn}


# --- reconcile ---

def test_carry_preserves_born_and_increments_cycles():
    updated = make_memory(alice=ParticipantInfo(recent=['ездил в Лондон']))
    prior = {'@alice': {'ездил в лондон': DecayRecord(born=LAST_WEEK, cycles=3, field='recent')}}

    decay, _, churn = cycle(updated, prior)

    record = decay['@alice']['ездил в лондон']
    assert record.born == LAST_WEEK
    assert record.cycles == 4
    assert events(churn) == {'ездил в лондон': 'carry'}


def test_birth_stamps_now_with_zero_cycles():
    updated = make_memory(alice=ParticipantInfo(recent=['ездил в Лондон']))

    decay, _, churn = cycle(updated, {})

    record = decay['@alice']['ездил в лондон']
    assert record.born == NOW
    assert record.cycles == 0
    assert record.field == 'recent'
    assert events(churn) == {'ездил в лондон': 'birth'}


def test_cold_start_stamps_everything_born_now():
    updated = make_memory(
        alice=ParticipantInfo(traits=['программист'], recent=['опоздал']),
        bob=ParticipantInfo(traits=['из Алматы']),
    )

    decay, _, churn = cycle(updated, {})

    assert {r.born for records in decay.values() for r in records.values()} == {NOW}
    assert {r.cycles for records in decay.values() for r in records.values()} == {0}
    assert [record.event for record in churn] == ['birth', 'birth', 'birth']


def test_reconcile_returns_the_sidecar_only():
    """Churn is a separate pass now, because it has to run after eviction."""
    updated = make_memory(alice=ParticipantInfo(recent=['опоздал']))

    decay = reconcile(updated, {}, NOW)

    assert decay == {'@alice': {'опоздал': DecayRecord(born=NOW, cycles=0, field='recent')}}


# --- summarize_churn ---

def test_vanished_entry_is_reported_and_not_carried():
    updated = make_memory(alice=ParticipantInfo(recent=['ездил в Лондон']))
    prior = {'@alice': {
        'ездил в лондон': DecayRecord(born=YESTERDAY, cycles=1, field='recent'),
        'опоздал на созвон': DecayRecord(born=YESTERDAY, cycles=1, field='recent'),
    }}

    decay, _, churn = cycle(updated, prior)

    assert set(decay['@alice']) == {'ездил в лондон'}
    assert events(churn)['опоздал на созвон'] == 'vanish'


def test_vanish_record_carries_the_field_it_was_lost_from():
    """`lost_recent` in the churn log is counted off this field."""
    updated = make_memory(alice=ParticipantInfo())
    prior = {'@alice': {
        'опоздал': DecayRecord(born=YESTERDAY, cycles=1, field='recent'),
        'программист': DecayRecord(born=YESTERDAY, cycles=1, field='traits'),
    }}

    _, _, churn = cycle(updated, prior)

    assert {r.key: r.field for r in churn if r.event == 'vanish'} == {
        'опоздал': 'recent',
        'программист': 'traits',
    }


def test_evicted_entry_is_neither_carried_nor_vanished():
    """The reason churn runs last: eviction's casualties have their own log line.

    Counted before eviction, «ездил в Лондон» read as `carried` while being deleted
    two lines later, and pruning then erased its record so it could never surface as
    a `vanish` on a later cycle either.
    """
    updated = make_memory(alice=ParticipantInfo(recent=['ездил в Лондон', 'купил велосипед']))
    prior = {'@alice': {'ездил в лондон': DecayRecord(born=LAST_WEEK, cycles=3, field='recent')}}

    _, evictions, churn = cycle(updated, prior, recent_keep=1)

    assert updated.participants['@alice'].recent == ['купил велосипед']
    assert [(e.text, e.applied) for e in evictions] == [('ездил в Лондон', True)]
    assert [(r.key, r.event) for r in churn] == [('купил велосипед', 'birth')]


def test_guard_dropped_entries_are_not_counted_as_vanished():
    """The guard runs first and mutates `updated`, so its drops are already gone."""
    updated = make_memory(alice=ParticipantInfo(traits=[]))
    prior = {'@alice': {'ездит на велосипеде': DecayRecord(born=YESTERDAY, cycles=1, field='traits')}}
    guard_records = [ConflictRecord(
        text='Ездит на велосипеде!',
        owner='@alice',
        field='traits',
        kept_owner='@bob',
        reason='incumbent_wins',
        removed=True,
    )]

    _, _, churn = cycle(updated, prior, guard_records)

    assert churn == []


def test_guard_reported_but_kept_entry_still_vanishes_if_absent():
    """`removed=False` means the guard left it alone, so its absence is a real loss."""
    updated = make_memory(alice=ParticipantInfo(traits=[]))
    prior = {'@alice': {'ездит на велосипеде': DecayRecord(born=YESTERDAY, cycles=1, field='traits')}}
    guard_records = [ConflictRecord(
        text='ездит на велосипеде',
        owner='@alice',
        field='traits',
        kept_owner=None,
        reason='no_incumbent',
        removed=False,
    )]

    _, _, churn = cycle(updated, prior, guard_records)

    assert events(churn) == {'ездит на велосипеде': 'vanish'}


def test_promotion_preserves_born_flips_field_and_reports_promote():
    updated = make_memory(alice=ParticipantInfo(traits=['ездит на велосипеде']))
    prior = {'@alice': {'ездит на велосипеде': DecayRecord(born=LAST_WEEK, cycles=4, field='recent')}}

    decay, _, churn = cycle(updated, prior)

    record = decay['@alice']['ездит на велосипеде']
    assert record.born == LAST_WEEK
    assert record.cycles == 5
    assert record.field == 'traits'
    assert events(churn) == {'ездит на велосипеде': 'promote'}


def test_reworded_promotion_is_reported_as_a_candidate():
    """The real shape of promotion: the model generalises, so the key changes.

    «опоздал на созвон» becomes the trait «часто опаздывает», which is a different
    normalized key — so exact `promote` cannot fire. Measured against the extraction
    model, the trait text differed from the source text in every observed promotion,
    which would leave `promoted` at zero forever and make every promotion read as an
    unexplained `vanish`.
    """
    updated = make_memory(alice=ParticipantInfo(traits=['часто опаздывает'], recent=[]))
    prior = {'@alice': {'опоздал на созвон': DecayRecord(born=LAST_WEEK, cycles=3, field='recent')}}

    _, _, churn = cycle(updated, prior)

    # The candidate shares a key with the birth, so compare the records themselves.
    assert [(r.key, r.event) for r in churn] == [
        ('часто опаздывает', 'birth'),
        ('опоздал на созвон', 'vanish'),
        ('часто опаздывает', 'promote_candidate'),
    ]


def test_every_trait_born_beside_a_lost_recent_is_a_candidate():
    """One candidate per nick undercounted: three generalisations read as two losses.

    Nothing pairs a reworded trait to the entry it replaced, so all of them are
    reported and the log prints `lost_recent` next to the count.
    """
    updated = make_memory(alice=ParticipantInfo(
        traits=['часто опаздывает', 'ночная сова', 'любит кофе']
    ))
    prior = {'@alice': {
        'опоздал на созвон': DecayRecord(born=LAST_WEEK, cycles=3, field='recent'),
        'сидел до утра': DecayRecord(born=LAST_WEEK, cycles=2, field='recent'),
        'пил кофе': DecayRecord(born=LAST_WEEK, cycles=1, field='recent'),
    }}

    _, _, churn = cycle(updated, prior)

    assert [r.key for r in churn if r.event == 'promote_candidate'] == [
        'любит кофе', 'ночная сова', 'часто опаздывает'
    ]
    assert len([r for r in churn if r.event == 'vanish']) == 3


def test_no_candidate_when_the_lost_entry_was_a_trait():
    """Only a lost `recent` entry can have been promoted."""
    updated = make_memory(alice=ParticipantInfo(traits=['новое свойство']))
    prior = {'@alice': {'старое свойство': DecayRecord(born=LAST_WEEK, cycles=3, field='traits')}}

    _, _, churn = cycle(updated, prior)

    assert [r.event for r in churn if r.event == 'promote_candidate'] == []


def test_no_candidate_when_nothing_new_landed_in_traits():
    updated = make_memory(alice=ParticipantInfo(recent=['другое событие']))
    prior = {'@alice': {'опоздал на созвон': DecayRecord(born=LAST_WEEK, cycles=3, field='recent')}}

    _, _, churn = cycle(updated, prior)

    assert [r.event for r in churn if r.event == 'promote_candidate'] == []


def test_verbatim_promotion_reports_promote_not_candidate():
    """When the text does survive the move, the exact signal still fires."""
    updated = make_memory(alice=ParticipantInfo(traits=['ездит на велосипеде']))
    prior = {'@alice': {'ездит на велосипеде': DecayRecord(born=LAST_WEEK, cycles=3, field='recent')}}

    _, _, churn = cycle(updated, prior)

    assert [r.event for r in churn] == ['promote']


def test_demotion_traits_to_recent_carries_rather_than_promotes():
    updated = make_memory(alice=ParticipantInfo(recent=['ездит на велосипеде']))
    prior = {'@alice': {'ездит на велосипеде': DecayRecord(born=LAST_WEEK, cycles=4, field='traits')}}

    decay, _, churn = cycle(updated, prior)

    assert decay['@alice']['ездит на велосипеде'].field == 'recent'
    assert events(churn) == {'ездит на велосипеде': 'carry'}


def test_rewording_resets_age():
    """The known blind spot: keying by normalized text reads a reword as death + birth.

    Unfixable without stable ids. Documented here rather than pretended away — the
    churn log is what measures how often it happens.
    """
    updated = make_memory(alice=ParticipantInfo(recent=['слетал в Лондон на выходные']))
    prior = {'@alice': {'ездил в лондон': DecayRecord(born=LAST_WEEK, cycles=6, field='recent')}}

    decay, _, churn = cycle(updated, prior)

    assert decay['@alice']['слетал в лондон на выходные'].born == NOW
    assert decay['@alice']['слетал в лондон на выходные'].cycles == 0
    assert events(churn) == {'ездил в лондон': 'vanish', 'слетал в лондон на выходные': 'birth'}


def test_reconcile_ignores_nicks_absent_from_updated():
    updated = make_memory(alice=ParticipantInfo(recent=['опоздал']))
    prior = {'@gone': {'уехал': DecayRecord(born=YESTERDAY, cycles=1, field='recent')}}

    decay, _, churn = cycle(updated, prior)

    assert set(decay) == {'@alice'}
    assert events(churn)['уехал'] == 'vanish'


# --- resolve_now ---

def test_resolve_now_uses_the_newest_message_timestamp():
    older = datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc)
    newer = datetime(2026, 5, 1, 13, 0, tzinfo=timezone.utc)

    assert resolve_now([newer, None, older]) == format_ts(newer)


def test_resolve_now_falls_back_to_wall_clock_without_timestamps():
    """`Message.created_at` is optional, so `max()` over an empty window would raise."""
    before = datetime.now(timezone.utc) - timedelta(minutes=1)

    result = resolve_now([None, None])

    assert format_ts(before) <= result <= format_ts(datetime.now(timezone.utc))


# --- apply_decay: baseline caps ---

def test_baseline_caps_apply_when_decay_disabled():
    items = [str(i) for i in range(8)]
    updated = StructuredMemory(
        participants={'@alice': ParticipantInfo(traits=items, recent=items)},
        state=ChatState(active_topics=items, open_questions=items, running_jokes=items),
    )

    apply_decay(updated, {}, caps())

    # Traits fit whole under the corrected cap — the inverted eviction is gone.
    assert updated.participants['@alice'].traits == items
    assert updated.participants['@alice'].recent == ['3', '4', '5', '6', '7']
    assert updated.state.active_topics == ['5', '6', '7']
    assert updated.state.open_questions == ['3', '4', '5', '6', '7']
    assert updated.state.running_jokes == ['3', '4', '5', '6', '7']


def test_state_caps_come_from_the_caps_object():
    """They were module constants, unreachable from settings and from any caller."""
    updated = StructuredMemory(state=ChatState(
        active_topics=['a', 'b', 'c'], open_questions=['a', 'b', 'c'], running_jokes=['a', 'b', 'c']
    ))

    apply_decay(updated, {}, caps(topics_keep=1, questions_keep=2, jokes_keep=0))

    assert updated.state.active_topics == ['c']
    assert updated.state.open_questions == ['b', 'c']
    assert updated.state.running_jokes == []


def test_caps_are_a_noop_under_the_limit():
    updated = StructuredMemory(
        participants={'@bob': ParticipantInfo(traits=['a', 'b'], recent=['r'])},
        state=ChatState(active_topics=['x']),
    )

    evictions = apply_decay(updated, {}, caps())

    assert updated.participants['@bob'].traits == ['a', 'b']
    assert updated.participants['@bob'].recent == ['r']
    assert updated.state.active_topics == ['x']
    assert evictions == []


def test_traits_over_the_cap_evict_from_the_front():
    updated = make_memory(alice=ParticipantInfo(traits=[f't{i}' for i in range(12)]))

    apply_decay(updated, {}, caps())

    assert updated.participants['@alice'].traits == [f't{i}' for i in range(2, 12)]


def test_trait_evictions_are_recorded():
    """They were applied silently, so the phase never saw which traits it cost."""
    updated = make_memory(alice=ParticipantInfo(traits=[f't{i}' for i in range(12)]))

    evictions = apply_decay(updated, {}, caps())

    assert [(e.field, e.text, e.reason, e.applied) for e in evictions] == [
        ('traits', 't0', 'cap', True),
        ('traits', 't1', 'cap', True),
    ]


# --- apply_decay: policy ---

# Emission order is deliberately the reverse of age here, so the positional baseline
# and the born-ordered policy select genuinely different pairs.
AGED = {'newest': NOW, 'middle': YESTERDAY, 'oldest': LAST_WEEK}


def test_policy_keeps_newest_by_born_not_by_list_position():
    updated = make_memory(alice=ParticipantInfo(recent=list(AGED)))

    evictions = apply_decay(
        updated, {'@alice': born(**AGED)}, caps(enabled=True, recent_keep=2)
    )

    # Survivors keep emission order, so the next cycle's positional cap stays sane.
    assert updated.participants['@alice'].recent == ['newest', 'middle']
    assert [(e.text, e.reason, e.applied) for e in evictions] == [('oldest', 'cap', True)]


def test_disabled_policy_records_both_what_it_would_drop_and_what_actually_went():
    """The gap between the two rules, which is the whole point of the log-only phase.

    Recording only the policy's picks reported «oldest» as evicted when it survived,
    and said nothing at all about «newest», which the baseline actually deleted.
    """
    updated = make_memory(alice=ParticipantInfo(recent=list(AGED)))

    evictions = apply_decay(
        updated, {'@alice': born(**AGED)}, caps(enabled=False, recent_keep=2)
    )

    assert updated.participants['@alice'].recent == ['middle', 'oldest']
    assert [(e.text, e.reason, e.applied) for e in evictions] == [
        ('newest', 'baseline', True),
        ('oldest', 'cap', False),
    ]


def test_cycles_evict_independently_of_the_cap():
    updated = make_memory(alice=ParticipantInfo(recent=['ancient', 'fresh']))
    decay = {'@alice': {
        'ancient': DecayRecord(born=LAST_WEEK, cycles=21, field='recent'),
        'fresh': DecayRecord(born=NOW, cycles=0, field='recent'),
    }}

    evictions = apply_decay(updated, decay, caps(enabled=True))

    assert updated.participants['@alice'].recent == ['fresh']
    assert [(e.text, e.reason) for e in evictions] == [('ancient', 'cycles')]


def test_cycles_exactly_at_the_limit_survive():
    updated = make_memory(alice=ParticipantInfo(recent=['borderline']))
    decay = {'@alice': {'borderline': DecayRecord(born=LAST_WEEK, cycles=20, field='recent')}}

    evictions = apply_decay(updated, decay, caps(enabled=True))

    assert updated.participants['@alice'].recent == ['borderline']
    assert evictions == []


def test_duplicate_entry_text_is_accounted_for_once_per_position():
    """Selection works in indices: a set keyed by text would drop both or neither."""
    updated = make_memory(alice=ParticipantInfo(recent=['опоздал', 'опоздал', 'новое']))

    evictions = apply_decay(updated, {}, caps(enabled=True, recent_keep=2))

    assert updated.participants['@alice'].recent == ['опоздал', 'новое']
    assert [(e.text, e.applied) for e in evictions] == [('опоздал', True)]


# --- apply_decay: sidecar pruning ---

def test_evicted_keys_are_pruned_from_the_sidecar():
    updated = make_memory(alice=ParticipantInfo(recent=['oldest', 'newest']))
    decay = {'@alice': born(oldest=LAST_WEEK, newest=NOW)}

    apply_decay(updated, decay, caps(enabled=True, recent_keep=1))

    assert set(decay['@alice']) == {'newest'}


def test_re_emitted_evicted_entry_is_treated_as_a_birth():
    """Without pruning, a stale `born` would resurrect and re-evict it forever."""
    updated = make_memory(alice=ParticipantInfo(recent=['oldest', 'newest']))
    decay = {'@alice': born(oldest=LAST_WEEK, newest=NOW)}
    apply_decay(updated, decay, caps(enabled=True, recent_keep=1))

    re_emitted = make_memory(alice=ParticipantInfo(recent=['newest', 'oldest']))
    next_decay, _, churn = cycle(re_emitted, decay)

    assert next_decay['@alice']['oldest'].born == NOW
    assert next_decay['@alice']['oldest'].cycles == 0
    assert events(churn)['oldest'] == 'birth'


def test_sidecar_drops_participants_that_emptied():
    updated = make_memory(alice=ParticipantInfo())
    decay = {'@alice': born(gone=LAST_WEEK)}

    apply_decay(updated, decay, caps(enabled=True))

    assert decay == {}


def test_traits_survive_the_recent_policy_untouched():
    updated = make_memory(alice=ParticipantInfo(traits=['программист'], recent=['опоздал']))
    decay = {'@alice': {
        'программист': DecayRecord(born=LAST_WEEK, cycles=99, field='traits'),
        'опоздал': DecayRecord(born=NOW, cycles=0, field='recent'),
    }}

    apply_decay(updated, decay, caps(enabled=True))

    # `cycles` far past the limit does not touch a trait — the rule is recent-only.
    assert updated.participants['@alice'].traits == ['программист']
    assert set(decay['@alice']) == {'программист', 'опоздал'}


# --- active_topics stripped from the prompt input ---

def test_prompt_input_strips_active_topics_without_mutating_current():
    content = StructuredMemory(
        participants={'@alice': ParticipantInfo(traits=['программист'])},
        state=ChatState(
            active_topics=['деплой'], open_questions=['когда релиз?'], running_jokes=['лежит']
        ),
    )
    current = MemoryData(chat_id=1, created_at=datetime.now(timezone.utc), content=content)

    rendered = _prompt_memory(current)

    assert '"active_topics":[]' in rendered.replace(' ', '')
    assert 'деплой' not in rendered
    assert 'когда релиз?' in rendered
    assert 'программист' in rendered
    # The same object is the guard's incumbent map and the caller holds a reference.
    assert current.content.state.active_topics == ['деплой']


def test_prompt_input_is_empty_object_without_prior_memory():
    assert _prompt_memory(None) == '{}'


# --- settings wiring ---

def test_decay_ships_disabled():
    assert settings.ENABLE_MEMORY_DECAY is False
    assert settings.TRAITS_KEEP == 10
    assert settings.RECENT_KEEP == 5
    assert settings.RECENT_MAX_CYCLES == 20
    assert settings.TOPICS_KEEP == 3
    assert settings.QUESTIONS_KEEP == 5
    assert settings.JOKES_KEEP == 5


def test_caps_come_from_settings(mocker):
    """Every cap has one source. Three of them used to be module constants."""
    mocker.patch.object(settings, 'ENABLE_MEMORY_DECAY', True)
    mocker.patch.object(settings, 'TOPICS_KEEP', 1)

    resolved = DecayCaps.from_settings()

    assert resolved.enabled is True
    assert resolved.topics_keep == 1
    assert resolved.traits_keep == settings.TRAITS_KEEP
    assert resolved.recent_keep == settings.RECENT_KEEP
    assert resolved.max_cycles == settings.RECENT_MAX_CYCLES
    assert resolved.questions_keep == settings.QUESTIONS_KEEP
    assert resolved.jokes_keep == settings.JOKES_KEEP
