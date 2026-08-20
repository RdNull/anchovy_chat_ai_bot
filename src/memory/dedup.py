import re
from collections import defaultdict
from typing import Callable, TypeVar

import unicodedata

from src.memory.models import ParticipantInfo, RecentItem, StructuredMemory
from src.models import BaseModel

NICK_PATTERN = re.compile(r'@[\w\d_]+')

TRAITS_FIELD = 'traits'
RECENT_FIELD = 'recent'

TRAITS_RECENT_OVERLAP = 'traits_recent_overlap'
INCUMBENT_WINS = 'incumbent_wins'
NO_INCUMBENT = 'no_incumbent'
AMBIGUOUS_INCUMBENT = 'ambiguous_incumbent'

Entry = TypeVar('Entry', str, RecentItem)


class DropRecord(BaseModel):
    """A single participant entry removed by the attribution guard."""

    text: str
    owner: str
    field: str
    kept_owner: str | None
    reason: str


def normalize(text: str) -> str:
    """Reduces an entry to its comparison key.

    Lowercases, strips `@nick` tokens, folds `ё` to `е`, replaces Unicode
    punctuation with spaces and collapses whitespace. Nick stripping must run
    before punctuation stripping, since `@` is punctuation itself.

    Args:
        text: Raw entry text as the model emitted it.

    Returns:
        The comparison key, or an empty string when the entry carries no
        content of its own (e.g. a bare nick).
    """
    lowered = NICK_PATTERN.sub(' ', text.lower()).replace('ё', 'е')
    depunctuated = ''.join(
        ' ' if unicodedata.category(char).startswith('P') else char for char in lowered
    )
    return ' '.join(depunctuated.split())


def _entry_keys(info: ParticipantInfo) -> set[str]:
    """Returns the unified `traits` + `recent` keyspace of one participant."""
    keys = {normalize(trait) for trait in info.traits}
    keys.update(normalize(item.text) for item in info.recent)
    keys.discard('')
    return keys


def _key_owners(memory: StructuredMemory | None) -> dict[str, set[str]]:
    """Maps every participant key to the nicks holding it."""
    owners: dict[str, set[str]] = defaultdict(set)
    if not memory:
        return owners

    for nick, info in memory.participants.items():
        for key in _entry_keys(info):
            owners[key].add(nick)
    return owners


def _drop_traits_recent_overlap(updated: StructuredMemory) -> list[DropRecord]:
    """Drops `recent` items duplicating a trait of the same participant.

    The prompt migrates a stably repeating fact from `recent` to `traits`, so an
    entry sitting on both sides means the migration ran without the source being
    removed: the trait is the intended survivor.
    """
    drops = []
    for nick, info in updated.participants.items():
        trait_keys = {normalize(trait) for trait in info.traits}
        trait_keys.discard('')

        kept = []
        for item in info.recent:
            if normalize(item.text) not in trait_keys:
                kept.append(item)
                continue

            drops.append(DropRecord(
                text=item.text,
                owner=nick,
                field=RECENT_FIELD,
                kept_owner=nick,
                reason=TRAITS_RECENT_OVERLAP,
            ))
        info.recent = kept
    return drops


def _resolve_owners(
    updated: StructuredMemory,
    current: StructuredMemory | None,
) -> dict[str, tuple[str | None, str]]:
    """Adjudicates every key held by more than one participant.

    Returns:
        A mapping of contested key to `(kept_owner, reason)`, where a
        `kept_owner` of `None` means every copy of that key must go.
    """
    incumbent = _key_owners(current)
    resolutions: dict[str, tuple[str | None, str]] = {}

    for key, nicks in _key_owners(updated).items():
        if len(nicks) < 2:
            continue

        survivors = nicks & incumbent.get(key, set())
        if len(survivors) == 1:
            resolutions[key] = (next(iter(survivors)), INCUMBENT_WINS)
        elif not survivors:
            resolutions[key] = (None, NO_INCUMBENT)
        else:
            # Every copy is an incumbent — the already-corrupted case. Dropping
            # them all lets a later window re-extract the right owner instead of
            # freezing the conflict in place forever.
            resolutions[key] = (None, AMBIGUOUS_INCUMBENT)

    return resolutions


def _filter_entries(
    entries: list[Entry],
    get_text: Callable[[Entry], str],
    nick: str,
    field: str,
    resolutions: dict[str, tuple[str | None, str]],
) -> tuple[list[Entry], list[DropRecord]]:
    """Splits one participant list into survivors and drops.

    Survivors are rebuilt by filtering so the original order — which `trim()`
    relies on, taking the tail — is preserved.
    """
    kept, drops = [], []
    for entry in entries:
        text = get_text(entry)
        resolution = resolutions.get(normalize(text))
        if resolution is None or resolution[0] == nick:
            kept.append(entry)
            continue

        kept_owner, reason = resolution
        drops.append(DropRecord(
            text=text,
            owner=nick,
            field=field,
            kept_owner=kept_owner,
            reason=reason,
        ))
    return kept, drops


def _drop_cross_participant(
    updated: StructuredMemory,
    current: StructuredMemory | None,
) -> list[DropRecord]:
    """Drops copies of a fact held by the wrong participant."""
    resolutions = _resolve_owners(updated, current)
    if not resolutions:
        return []

    drops = []
    for nick, info in updated.participants.items():
        traits, trait_drops = _filter_entries(
            info.traits, lambda trait: trait, nick, TRAITS_FIELD, resolutions
        )
        recent, recent_drops = _filter_entries(
            info.recent, lambda item: item.text, nick, RECENT_FIELD, resolutions
        )
        info.traits = traits
        info.recent = recent
        drops.extend(trait_drops)
        drops.extend(recent_drops)
    return drops


def resolve_attribution_conflicts(
    updated: StructuredMemory,
    current: StructuredMemory | None,
) -> list[DropRecord]:
    """Enforces that one fact belongs to exactly one participant.

    Runs the within-participant `traits` ↔ `recent` pass first, so each nick
    holds a key at most once before cross-participant ownership is adjudicated.
    When ownership cannot be adjudicated every copy is dropped: a lost fact
    recurs in a later window, a misattributed one is re-emitted forever.

    Only `participants` is touched. `state` passes through untouched on purpose,
    since the memory prompt deliberately allows one fact in both places.

    Args:
        updated: Memory just returned by the model. Mutated in place.
        current: Last saved memory, used as the incumbent map. May be `None`.

    Returns:
        Every entry removed, in removal order.
    """
    drops = _drop_traits_recent_overlap(updated)
    drops.extend(_drop_cross_participant(updated, current))
    return drops
