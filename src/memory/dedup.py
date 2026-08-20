from collections import defaultdict

from src.memory.keys import RECENT_FIELD, TRAITS_FIELD, entry_keys, normalize
from src.memory.models import StructuredMemory
from src.models import BaseModel

TRAITS_RECENT_OVERLAP = 'traits_recent_overlap'
INCUMBENT_WINS = 'incumbent_wins'
NO_INCUMBENT = 'no_incumbent'
AMBIGUOUS_INCUMBENT = 'ambiguous_incumbent'

# `(kept_owner, reason, removes)` — the verdict for one contested key.
Resolution = tuple[str | None, str, bool]


class ConflictRecord(BaseModel):
    """A single participant entry flagged by the attribution guard.

    `removed` is `False` when the conflict was only reported and the entry stayed
    in place.
    """

    text: str
    owner: str
    field: str
    kept_owner: str | None
    reason: str
    removed: bool


def _key_owners(memory: StructuredMemory | None) -> dict[str, set[str]]:
    """Maps every participant key to the nicks holding it."""
    owners: dict[str, set[str]] = defaultdict(set)
    if not memory:
        return owners

    for nick, info in memory.participants.items():
        for key in entry_keys(info.traits, info.recent):
            owners[key].add(nick)
    return owners


def _drop_traits_recent_overlap(updated: StructuredMemory) -> list[ConflictRecord]:
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
        for entry in info.recent:
            if normalize(entry) not in trait_keys:
                kept.append(entry)
                continue

            drops.append(ConflictRecord(
                text=entry,
                owner=nick,
                field=RECENT_FIELD,
                kept_owner=nick,
                reason=TRAITS_RECENT_OVERLAP,
                removed=True,
            ))
        info.recent = kept
    return drops


def _resolve_owners(
    updated: StructuredMemory,
    current: StructuredMemory | None,
) -> dict[str, Resolution]:
    """Adjudicates every key held by more than one participant.

    Returns:
        A mapping of contested key to `(kept_owner, reason, removes)`, where a
        `kept_owner` of `None` means no copy is privileged and `removes` says
        whether the branch deletes the copies or merely reports them.
    """
    incumbent = _key_owners(current)
    resolutions: dict[str, Resolution] = {}

    for key, nicks in _key_owners(updated).items():
        if len(nicks) < 2:
            continue

        survivors = nicks & incumbent.get(key, set())
        if len(survivors) == 1:
            resolutions[key] = (next(iter(survivors)), INCUMBENT_WINS, True)
        elif not survivors:
            # The fact is new on everyone, and a string match cannot tell an
            # intake leak from parallel facts — two people who independently did
            # the same thing. Report the collision and keep every copy.
            resolutions[key] = (None, NO_INCUMBENT, False)
        else:
            # Every copy is an incumbent — the already-corrupted case. Dropping
            # them all lets a later window re-extract the right owner instead of
            # freezing the conflict in place forever.
            resolutions[key] = (None, AMBIGUOUS_INCUMBENT, True)

    return resolutions


def _filter_entries(
    entries: list[str],
    nick: str,
    field: str,
    resolutions: dict[str, Resolution],
) -> tuple[list[str], list[ConflictRecord]]:
    """Records every conflict on one participant list, removing what it must.

    An entry whose resolution does not remove is recorded and kept. Survivors are
    rebuilt by filtering so the original order — which `apply_decay` relies on for
    its positional caps — is preserved.
    """
    kept, records = [], []
    for entry in entries:
        resolution = resolutions.get(normalize(entry))
        if resolution is None or resolution[0] == nick:
            kept.append(entry)
            continue

        kept_owner, reason, removes = resolution
        records.append(ConflictRecord(
            text=entry,
            owner=nick,
            field=field,
            kept_owner=kept_owner,
            reason=reason,
            removed=removes,
        ))
        if not removes:
            kept.append(entry)
    return kept, records


def _resolve_cross_participant(
    updated: StructuredMemory,
    current: StructuredMemory | None,
) -> list[ConflictRecord]:
    """Reports facts held by several participants, dropping the wrong copies."""
    resolutions = _resolve_owners(updated, current)
    if not resolutions:
        return []

    records = []
    for nick, info in updated.participants.items():
        traits, trait_records = _filter_entries(info.traits, nick, TRAITS_FIELD, resolutions)
        recent, recent_records = _filter_entries(info.recent, nick, RECENT_FIELD, resolutions)
        info.traits = traits
        info.recent = recent
        records.extend(trait_records)
        records.extend(recent_records)
    return records


def resolve_attribution_conflicts(
    updated: StructuredMemory,
    current: StructuredMemory | None,
) -> list[ConflictRecord]:
    """Enforces that one stored fact belongs to exactly one participant.

    Runs the within-participant `traits` ↔ `recent` pass first, so each nick
    holds a key at most once before cross-participant ownership is adjudicated.
    Every copy is dropped only when the fact was already stored on someone: a
    lost fact recurs in a later window, a misattributed one is re-emitted
    forever. A fact new on everyone this cycle is reported and kept, since a
    string match cannot separate an intake leak from two participants who
    independently did the same thing.

    Only `participants` is touched. `state` passes through untouched on purpose,
    since the memory prompt deliberately allows one fact in both places.

    Args:
        updated: Memory just returned by the model. Mutated in place.
        current: Last saved memory, used as the incumbent map. May be `None`.

    Returns:
        Every conflict found, in detection order.
    """
    records = _drop_traits_recent_overlap(updated)
    records.extend(_resolve_cross_participant(updated, current))
    return records
