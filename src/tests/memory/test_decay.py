from datetime import datetime, timedelta, timezone

from src import settings
from src.memory.decay import apply_decay, reconcile, resolve_now
from src.memory.dedup import ConflictRecord
from src.memory.models import ChatState, DecayRecord, MemoryData, ParticipantInfo, StructuredMemory
from src.memory.processors import _prompt_memory
from src.models import format_ts

NOW = '26-05-01 18:00'
YESTERDAY = '26-04-30 18:00'
LAST_WEEK = '26-04-24 18:00'

KEEP = {'traits_keep': 10, 'recent_keep': 5, 'max_cycles': 20}


def make_memory(**participants: ParticipantInfo) -> StructuredMemory:
    return StructuredMemory(participants={f'@{nick}': info for nick, info in participants.items()})


def born(**entries: str) -> dict[str, DecayRecord]:
    return {key: DecayRecord(born=stamp, cycles=0, field='recent') for key, stamp in entries.items()}


def events(churn) -> dict[str, str]:
    return {record.key: record.event for record in churn}


# --- reconcile ---

def test_carry_preserves_born_and_increments_cycles():
    updated = make_memory(alice=ParticipantInfo(recent=['ездил в Лондон']))
    prior = {'@alice': {'ездил в лондон': DecayRecord(born=LAST_WEEK, cycles=3, field='recent')}}

    decay, churn = reconcile(updated, prior, [], NOW)

    record = decay['@alice']['ездил в лондон']
    assert record.born == LAST_WEEK
    assert record.cycles == 4
    assert events(churn) == {'ездил в лондон': 'carry'}


def test_birth_stamps_now_with_zero_cycles():
    updated = make_memory(alice=ParticipantInfo(recent=['ездил в Лондон']))

    decay, churn = reconcile(updated, {}, [], NOW)

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

    decay, churn = reconcile(updated, {}, [], NOW)

    assert {r.born for records in decay.values() for r in records.values()} == {NOW}
    assert {r.cycles for records in decay.values() for r in records.values()} == {0}
    assert [record.event for record in churn] == ['birth', 'birth', 'birth']


def test_vanished_entry_is_reported_and_not_carried():
    updated = make_memory(alice=ParticipantInfo(recent=['ездил в Лондон']))
    prior = {'@alice': {
        'ездил в лондон': DecayRecord(born=YESTERDAY, cycles=1, field='recent'),
        'опоздал на созвон': DecayRecord(born=YESTERDAY, cycles=1, field='recent'),
    }}

    decay, churn = reconcile(updated, prior, [], NOW)

    assert set(decay['@alice']) == {'ездил в лондон'}
    assert events(churn)['опоздал на созвон'] == 'vanish'


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

    _, churn = reconcile(updated, prior, guard_records, NOW)

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

    _, churn = reconcile(updated, prior, guard_records, NOW)

    assert events(churn) == {'ездит на велосипеде': 'vanish'}


def test_promotion_preserves_born_flips_field_and_reports_promote():
    updated = make_memory(alice=ParticipantInfo(traits=['ездит на велосипеде']))
    prior = {'@alice': {'ездит на велосипеде': DecayRecord(born=LAST_WEEK, cycles=4, field='recent')}}

    decay, churn = reconcile(updated, prior, [], NOW)

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

    _, churn = reconcile(updated, prior, [], NOW)

    # The candidate shares a key with the birth, so compare the records themselves.
    assert [(r.key, r.event) for r in churn] == [
        ('часто опаздывает', 'birth'),
        ('опоздал на созвон', 'vanish'),
        ('часто опаздывает', 'promote_candidate'),
    ]


def test_no_candidate_when_the_lost_entry_was_a_trait():
    """Only a lost `recent` entry can have been promoted."""
    updated = make_memory(alice=ParticipantInfo(traits=['новое свойство']))
    prior = {'@alice': {'старое свойство': DecayRecord(born=LAST_WEEK, cycles=3, field='traits')}}

    _, churn = reconcile(updated, prior, [], NOW)

    assert [r.event for r in churn if r.event == 'promote_candidate'] == []


def test_no_candidate_when_nothing_new_landed_in_traits():
    updated = make_memory(alice=ParticipantInfo(recent=['другое событие']))
    prior = {'@alice': {'опоздал на созвон': DecayRecord(born=LAST_WEEK, cycles=3, field='recent')}}

    _, churn = reconcile(updated, prior, [], NOW)

    assert [r.event for r in churn if r.event == 'promote_candidate'] == []


def test_verbatim_promotion_reports_promote_not_candidate():
    """When the text does survive the move, the exact signal still fires."""
    updated = make_memory(alice=ParticipantInfo(traits=['ездит на велосипеде']))
    prior = {'@alice': {'ездит на велосипеде': DecayRecord(born=LAST_WEEK, cycles=3, field='recent')}}

    _, churn = reconcile(updated, prior, [], NOW)

    assert [r.event for r in churn] == ['promote']


def test_demotion_traits_to_recent_carries_rather_than_promotes():
    updated = make_memory(alice=ParticipantInfo(recent=['ездит на велосипеде']))
    prior = {'@alice': {'ездит на велосипеде': DecayRecord(born=LAST_WEEK, cycles=4, field='traits')}}

    decay, churn = reconcile(updated, prior, [], NOW)

    assert decay['@alice']['ездит на велосипеде'].field == 'recent'
    assert events(churn) == {'ездит на велосипеде': 'carry'}


def test_rewording_resets_age():
    """The known blind spot: keying by normalized text reads a reword as death + birth.

    Unfixable without stable ids. Documented here rather than pretended away — the
    churn log is what measures how often it happens.
    """
    updated = make_memory(alice=ParticipantInfo(recent=['слетал в Лондон на выходные']))
    prior = {'@alice': {'ездил в лондон': DecayRecord(born=LAST_WEEK, cycles=6, field='recent')}}

    decay, churn = reconcile(updated, prior, [], NOW)

    assert decay['@alice']['слетал в лондон на выходные'].born == NOW
    assert decay['@alice']['слетал в лондон на выходные'].cycles == 0
    assert events(churn) == {'ездил в лондон': 'vanish', 'слетал в лондон на выходные': 'birth'}


def test_reconcile_ignores_nicks_absent_from_updated():
    updated = make_memory(alice=ParticipantInfo(recent=['опоздал']))
    prior = {'@gone': {'уехал': DecayRecord(born=YESTERDAY, cycles=1, field='recent')}}

    decay, churn = reconcile(updated, prior, [], NOW)

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

    apply_decay(updated, {}, enabled=False, **KEEP)

    # Traits fit whole under the corrected cap — the inverted eviction is gone.
    assert updated.participants['@alice'].traits == items
    assert updated.participants['@alice'].recent == ['3', '4', '5', '6', '7']
    assert updated.state.active_topics == ['5', '6', '7']
    assert updated.state.open_questions == ['3', '4', '5', '6', '7']
    assert updated.state.running_jokes == ['3', '4', '5', '6', '7']


def test_caps_are_a_noop_under_the_limit():
    updated = StructuredMemory(
        participants={'@bob': ParticipantInfo(traits=['a', 'b'], recent=['r'])},
        state=ChatState(active_topics=['x']),
    )

    apply_decay(updated, {}, enabled=False, **KEEP)

    assert updated.participants['@bob'].traits == ['a', 'b']
    assert updated.participants['@bob'].recent == ['r']
    assert updated.state.active_topics == ['x']


def test_traits_over_the_cap_evict_from_the_front():
    updated = make_memory(alice=ParticipantInfo(traits=[f't{i}' for i in range(12)]))

    apply_decay(updated, {}, enabled=False, **KEEP)

    assert updated.participants['@alice'].traits == [f't{i}' for i in range(2, 12)]


# --- apply_decay: policy ---

# Emission order is deliberately the reverse of age here, so the positional baseline
# and the born-ordered policy select genuinely different pairs.
AGED = {'newest': NOW, 'middle': YESTERDAY, 'oldest': LAST_WEEK}


def test_policy_keeps_newest_by_born_not_by_list_position():
    updated = make_memory(alice=ParticipantInfo(recent=list(AGED)))

    evictions = apply_decay(
        updated, {'@alice': born(**AGED)}, enabled=True, traits_keep=10, recent_keep=2, max_cycles=20
    )

    # Survivors keep emission order, so the next cycle's positional cap stays sane.
    assert updated.participants['@alice'].recent == ['newest', 'middle']
    assert [(e.text, e.reason, e.applied) for e in evictions] == [('oldest', 'cap', True)]


def test_disabled_policy_applies_the_baseline_but_still_reports_the_policy():
    updated = make_memory(alice=ParticipantInfo(recent=list(AGED)))

    evictions = apply_decay(
        updated, {'@alice': born(**AGED)}, enabled=False, traits_keep=10, recent_keep=2, max_cycles=20
    )

    # The baseline keeps the tail — the two oldest entries, exactly what the policy
    # would have dropped. That gap is the reason the log-only phase is worth running.
    assert updated.participants['@alice'].recent == ['middle', 'oldest']
    assert [(e.text, e.reason, e.applied) for e in evictions] == [('oldest', 'cap', False)]


def test_cycles_evict_independently_of_the_cap():
    updated = make_memory(alice=ParticipantInfo(recent=['ancient', 'fresh']))
    decay = {'@alice': {
        'ancient': DecayRecord(born=LAST_WEEK, cycles=21, field='recent'),
        'fresh': DecayRecord(born=NOW, cycles=0, field='recent'),
    }}

    evictions = apply_decay(updated, decay, enabled=True, **KEEP)

    assert updated.participants['@alice'].recent == ['fresh']
    assert [(e.text, e.reason) for e in evictions] == [('ancient', 'cycles')]


def test_cycles_exactly_at_the_limit_survive():
    updated = make_memory(alice=ParticipantInfo(recent=['borderline']))
    decay = {'@alice': {'borderline': DecayRecord(born=LAST_WEEK, cycles=20, field='recent')}}

    evictions = apply_decay(updated, decay, enabled=True, **KEEP)

    assert updated.participants['@alice'].recent == ['borderline']
    assert evictions == []


# --- apply_decay: sidecar pruning ---

def test_evicted_keys_are_pruned_from_the_sidecar():
    updated = make_memory(alice=ParticipantInfo(recent=['oldest', 'newest']))
    decay = {'@alice': born(oldest=LAST_WEEK, newest=NOW)}

    apply_decay(updated, decay, enabled=True, traits_keep=10, recent_keep=1, max_cycles=20)

    assert set(decay['@alice']) == {'newest'}


def test_re_emitted_evicted_entry_is_treated_as_a_birth():
    """Without pruning, a stale `born` would resurrect and re-evict it forever."""
    updated = make_memory(alice=ParticipantInfo(recent=['oldest', 'newest']))
    decay = {'@alice': born(oldest=LAST_WEEK, newest=NOW)}
    apply_decay(updated, decay, enabled=True, traits_keep=10, recent_keep=1, max_cycles=20)

    re_emitted = make_memory(alice=ParticipantInfo(recent=['newest', 'oldest']))
    next_decay, churn = reconcile(re_emitted, decay, [], NOW)

    assert next_decay['@alice']['oldest'].born == NOW
    assert next_decay['@alice']['oldest'].cycles == 0
    assert events(churn)['oldest'] == 'birth'


def test_sidecar_drops_participants_that_emptied():
    updated = make_memory(alice=ParticipantInfo())
    decay = {'@alice': born(gone=LAST_WEEK)}

    apply_decay(updated, decay, enabled=True, **KEEP)

    assert decay == {}


def test_traits_survive_the_recent_policy_untouched():
    updated = make_memory(alice=ParticipantInfo(traits=['программист'], recent=['опоздал']))
    decay = {'@alice': {
        'программист': DecayRecord(born=LAST_WEEK, cycles=99, field='traits'),
        'опоздал': DecayRecord(born=NOW, cycles=0, field='recent'),
    }}

    apply_decay(updated, decay, enabled=True, **KEEP)

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
