from src.memory.dedup import ConflictRecord, normalize, resolve_attribution_conflicts
from src.memory.models import ChatState, ParticipantInfo, StructuredMemory


def make_recent(*texts: str) -> list[str]:
    return list(texts)


def make_memory(**participants: ParticipantInfo) -> StructuredMemory:
    return StructuredMemory(participants={f'@{nick}': info for nick, info in participants.items()})


def recent_texts(memory: StructuredMemory, nick: str) -> list[str]:
    return memory.participants[nick].recent


# --- normalize ---

def test_normalize_lowercases_and_strips_punctuation():
    assert normalize('Ездит На Велосипеде!!!') == 'ездит на велосипеде'


def test_normalize_folds_yo_and_strips_nicks():
    assert normalize('@name всё ещё «пилит» бота — ночью') == 'все еще пилит бота ночью'


def test_normalize_collapses_whitespace():
    assert normalize('  ездит   на\nвелосипеде  ') == 'ездит на велосипеде'


def test_normalize_returns_empty_for_bare_nick():
    assert normalize('@name') == ''


def test_normalize_strips_symbols_not_just_punctuation():
    """These keys become MongoDB field names, and Mongo restricts a leading `$`.

    Stripping category `P` alone left `$` (category `Sc`) in the key.
    """
    assert normalize('зарабатывает $5000') == 'зарабатывает 5000'
    assert normalize('долг $500 в месяц') == 'долг 500 в месяц'


def test_normalize_strips_emoji():
    assert normalize('любит 🍕 пиццу') == 'любит пиццу'


def test_normalize_strips_cyrillic_nicks():
    """`parsing.py` falls back to `first_name`, so a Cyrillic nick is routine."""
    assert normalize('@Миша опоздал на созвон') == 'опоздал на созвон'


# --- no conflicts ---

def test_no_duplicates_leaves_memory_unchanged():
    memory = make_memory(
        alice=ParticipantInfo(traits=['программист'], recent=make_recent('ездит на велосипеде')),
        bob=ParticipantInfo(traits=['из Алматы'], recent=make_recent('опоздал на созвон')),
    )

    drops = resolve_attribution_conflicts(memory, None)

    assert drops == []
    assert memory.participants['@alice'].traits == ['программист']
    assert recent_texts(memory, '@alice') == ['ездит на велосипеде']
    assert memory.participants['@bob'].traits == ['из Алматы']
    assert recent_texts(memory, '@bob') == ['опоздал на созвон']


# --- cross-participant resolution ---

def test_single_incumbent_keeps_incumbent_copy():
    current = make_memory(alice=ParticipantInfo(traits=['ездит на велосипеде']))
    updated = make_memory(
        alice=ParticipantInfo(traits=['ездит на велосипеде']),
        bob=ParticipantInfo(traits=['ездит на велосипеде']),
    )

    drops = resolve_attribution_conflicts(updated, current)

    assert updated.participants['@alice'].traits == ['ездит на велосипеде']
    assert updated.participants['@bob'].traits == []
    assert drops == [ConflictRecord(
        text='ездит на велосипеде',
        owner='@bob',
        field='traits',
        kept_owner='@alice',
        reason='incumbent_wins',
        removed=True,
    )]


def test_no_incumbent_keeps_every_copy():
    current = make_memory(carol=ParticipantInfo(traits=['играет в CS']))
    updated = make_memory(
        alice=ParticipantInfo(traits=['ездит на велосипеде']),
        bob=ParticipantInfo(traits=['ездит на велосипеде']),
    )

    records = resolve_attribution_conflicts(updated, current)

    assert updated.participants['@alice'].traits == ['ездит на велосипеде']
    assert updated.participants['@bob'].traits == ['ездит на велосипеде']
    assert [r.reason for r in records] == ['no_incumbent', 'no_incumbent']
    assert [r.owner for r in records] == ['@alice', '@bob']
    assert [r.kept_owner for r in records] == [None, None]
    assert [r.removed for r in records] == [False, False]


def test_parallel_facts_from_group_agreement_survive():
    fact = 'согласился на встречу в 18:00 по проекту Зенит'
    current = make_memory(
        bob=ParticipantInfo(traits=['продакт']),
        charlie=ParticipantInfo(traits=['дизайнер']),
    )
    updated = make_memory(
        bob=ParticipantInfo(traits=['продакт'], recent=make_recent(fact)),
        charlie=ParticipantInfo(traits=['дизайнер'], recent=make_recent(fact)),
    )

    records = resolve_attribution_conflicts(updated, current)

    assert recent_texts(updated, '@bob') == [fact]
    assert recent_texts(updated, '@charlie') == [fact]
    assert [(r.owner, r.field, r.reason, r.removed) for r in records] == [
        ('@bob', 'recent', 'no_incumbent', False),
        ('@charlie', 'recent', 'no_incumbent', False),
    ]


def test_ambiguous_incumbent_drops_every_copy():
    current = make_memory(
        alice=ParticipantInfo(traits=['ездит на велосипеде']),
        bob=ParticipantInfo(traits=['ездит на велосипеде']),
    )
    updated = make_memory(
        alice=ParticipantInfo(traits=['ездит на велосипеде']),
        bob=ParticipantInfo(traits=['ездит на велосипеде']),
    )

    drops = resolve_attribution_conflicts(updated, current)

    assert updated.participants['@alice'].traits == []
    assert updated.participants['@bob'].traits == []
    assert [d.reason for d in drops] == ['ambiguous_incumbent', 'ambiguous_incumbent']
    assert [d.kept_owner for d in drops] == [None, None]


def test_missing_current_memory_keeps_every_copy():
    updated = make_memory(
        alice=ParticipantInfo(traits=['ездит на велосипеде']),
        bob=ParticipantInfo(traits=['ездит на велосипеде']),
    )

    records = resolve_attribution_conflicts(updated, None)

    assert updated.participants['@alice'].traits == ['ездит на велосипеде']
    assert updated.participants['@bob'].traits == ['ездит на велосипеде']
    assert [r.reason for r in records] == ['no_incumbent', 'no_incumbent']
    assert [r.removed for r in records] == [False, False]


def test_normalized_variants_collide_across_participants():
    updated = make_memory(
        alice=ParticipantInfo(traits=['Ездит на велосипеде.']),
        bob=ParticipantInfo(traits=['@alice ездит на велосипеде']),
        carol=ParticipantInfo(traits=['ездит на велосипедё']),
    )

    records = resolve_attribution_conflicts(updated, None)

    assert updated.participants['@alice'].traits == ['Ездит на велосипеде.']
    assert updated.participants['@bob'].traits == ['@alice ездит на велосипеде']
    assert updated.participants['@carol'].traits == ['ездит на велосипедё']
    assert len(records) == 3
    assert {r.reason for r in records} == {'no_incumbent'}
    assert {r.removed for r in records} == {False}


def test_trait_and_recent_share_one_keyspace_across_participants():
    current = make_memory(alice=ParticipantInfo(traits=['ездит на велосипеде']))
    updated = make_memory(
        alice=ParticipantInfo(traits=['ездит на велосипеде']),
        bob=ParticipantInfo(recent=make_recent('ездит на велосипеде')),
    )

    drops = resolve_attribution_conflicts(updated, current)

    assert updated.participants['@alice'].traits == ['ездит на велосипеде']
    assert recent_texts(updated, '@bob') == []
    assert [(d.owner, d.field, d.kept_owner) for d in drops] == [('@bob', 'recent', '@alice')]


# --- within-participant traits/recent overlap ---

def test_traits_recent_overlap_keeps_trait():
    updated = make_memory(
        alice=ParticipantInfo(
            traits=['ездит на велосипеде'],
            recent=make_recent('Ездит на велосипеде!'),
        ),
    )

    drops = resolve_attribution_conflicts(updated, None)

    assert updated.participants['@alice'].traits == ['ездит на велосипеде']
    assert recent_texts(updated, '@alice') == []
    assert drops == [ConflictRecord(
        text='Ездит на велосипеде!',
        owner='@alice',
        field='recent',
        kept_owner='@alice',
        reason='traits_recent_overlap',
        removed=True,
    )]


# --- untouched data ---

def test_state_is_not_deduplicated_against_participants():
    updated = StructuredMemory(
        participants={'@alice': ParticipantInfo(traits=['батя чата'])},
        state=ChatState(running_jokes=['батя чата'], active_topics=['батя чата']),
    )

    drops = resolve_attribution_conflicts(updated, None)

    assert drops == []
    assert updated.participants['@alice'].traits == ['батя чата']
    assert updated.state.running_jokes == ['батя чата']
    assert updated.state.active_topics == ['батя чата']


def test_emptied_participant_is_kept():
    current = make_memory(
        alice=ParticipantInfo(traits=['ездит на велосипеде']),
        bob=ParticipantInfo(traits=['ездит на велосипеде']),
    )
    updated = make_memory(
        alice=ParticipantInfo(traits=['ездит на велосипеде']),
        bob=ParticipantInfo(traits=['ездит на велосипеде']),
    )

    resolve_attribution_conflicts(updated, current)

    assert set(updated.participants) == {'@alice', '@bob'}
    assert updated.participants['@bob'].traits == []


def test_survivor_order_is_preserved():
    current = make_memory(bob=ParticipantInfo(traits=['общий факт']))
    updated = make_memory(
        alice=ParticipantInfo(
            traits=['первый', 'общий факт', 'второй', 'третий'],
            recent=make_recent('r1', 'общий факт', 'r2'),
        ),
        bob=ParticipantInfo(traits=['общий факт']),
    )

    drops = resolve_attribution_conflicts(updated, current)

    assert updated.participants['@alice'].traits == ['первый', 'второй', 'третий']
    assert recent_texts(updated, '@alice') == ['r1', 'r2']
    assert updated.participants['@bob'].traits == ['общий факт']
    assert {d.owner for d in drops} == {'@alice'}
