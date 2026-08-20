"""Code-owned ageing and eviction for structured memory.

The extraction model no longer stamps or reads timestamps. It emits bare strings;
this module diffs each cycle's output against the sidecar of the previous one to
work out what was born, what carried, what vanished, and what got promoted, then
decides what to evict.

What the diff yields deterministically is **first written this cycle**, not
*mentioned again this cycle*: `v4.j2` instructs the model to carry everything
forward, so an entry present in both snapshots only means still stored. Decay is
therefore age-based, not recurrence-based — age is computable, recurrence is not.

The one recurrence signal is `recent → traits` promotion. It is a structural move,
but only a *verbatim* one is detectable as `promote`, and the model does not
promote verbatim: an event becomes a property, so «опоздал на созвон» is emitted
as «часто опаздывает», a different key. Measured against the extraction model,
the trait text differed from the source text in every observed promotion, so exact
`promote` fires approximately never.

`promote_candidate` covers that. A nick that loses a `recent` key and gains a
`traits` key in the same cycle is the reworded promotion, and counting it is what
separates «the model generalised a recurring fact» from «the model silently
dropped it» — which is the whole question `vanished` exists to answer.

Cycles, not wall clock, are the native unit here. The memory update is triggered
by a message count, so a wall-clock TTL empties a quiet chat's memory every cycle
or two while never binding at all in a busy one — it bites exactly where it does
the most damage.
"""

from datetime import datetime, timezone

from src.memory.keys import RECENT_FIELD, TRAITS_FIELD, normalize
from src.memory.models import DecayRecord, StructuredMemory
from src.models import BaseModel, format_ts

BIRTH = 'birth'
CARRY = 'carry'
VANISH = 'vanish'
PROMOTE = 'promote'
PROMOTE_CANDIDATE = 'promote_candidate'

CAP_REASON = 'cap'
CYCLES_REASON = 'cycles'

TOPICS_KEEP = 3  # matches «Максимум 3» in the extraction prompt
QUESTIONS_KEEP = 5
JOKES_KEEP = 5

Decay = dict[str, dict[str, DecayRecord]]


class ChurnRecord(BaseModel):
    """One entry's fate across a single memory cycle."""

    nick: str
    key: str
    text: str
    event: str  # 'birth' | 'carry' | 'vanish' | 'promote'


class EvictionRecord(BaseModel):
    """One entry the eviction policy selected for removal.

    `applied` is `False` during the log-only phase, where the policy is computed
    and reported but the baseline caps are what actually run.
    """

    nick: str
    field: str
    text: str
    reason: str  # 'cap' | 'cycles'
    applied: bool


def resolve_now(created_ats: list[datetime | None]) -> str:
    """Picks the timestamp this cycle is stamped with.

    The newest message in the window, matching the «сейчас» the extraction prompt
    used to define — which keeps tests deterministic and keeps sidecar ages on the
    same clock as the message log. Falls back to wall clock only when no message
    in the window carries a timestamp at all.
    """
    stamped = [value for value in created_ats if value]
    return format_ts(max(stamped) if stamped else datetime.now(timezone.utc))


def _emitted_fields(memory: StructuredMemory, nick: str) -> dict[str, str]:
    """Maps each of one participant's keys to the list holding it.

    `traits` wins a within-participant collision, matching the guard's
    `_drop_traits_recent_overlap`, which keeps the trait as the intended survivor.
    """
    info = memory.participants[nick]
    fields = {normalize(entry): RECENT_FIELD for entry in info.recent}
    fields.update({normalize(entry): TRAITS_FIELD for entry in info.traits})
    fields.pop('', None)
    return fields


def _sample_text(memory: StructuredMemory, nick: str, key: str) -> str:
    """Returns the raw text behind a normalized key, for logging."""
    info = memory.participants[nick]
    for entry in [*info.traits, *info.recent]:
        if normalize(entry) == key:
            return entry
    return key


def reconcile(
    updated: StructuredMemory,
    prior_decay: Decay,
    guard_records: list,
    now: str,
) -> tuple[Decay, list[ChurnRecord]]:
    """Ages the sidecar forward one cycle and reports what moved.

    `prior_decay` is the *sole* authority on what was stored last cycle. The
    sidecar holds a record for every stored entry, so its key set is the prior key
    set; deriving it a second way from the previous snapshot's content would let
    the two drift.

    Args:
        updated: Memory the model just emitted, after the attribution guard ran.
        prior_decay: Last cycle's sidecar.
        guard_records: `ConflictRecord`s from the guard. Entries it removed are
            already gone from `updated` and would otherwise be miscounted as
            `vanish`, so they are excluded.
        now: This cycle's timestamp, from `resolve_now`.

    Returns:
        The sidecar for this cycle and every churn event, in nick order. A nick that
        both loses a `recent` key and gains a `traits` key emits one extra
        `promote_candidate` record, since a reworded promotion cannot be matched by
        key and would otherwise read as an unexplained loss.
    """
    guard_dropped = {
        (record.owner, normalize(record.text)) for record in guard_records if record.removed
    }

    decay: Decay = {}
    churn: list[ChurnRecord] = []

    for nick in updated.participants:
        prior = prior_decay.get(nick, {})
        records: dict[str, DecayRecord] = {}

        for key, field in _emitted_fields(updated, nick).items():
            text = _sample_text(updated, nick, key)
            previous = prior.get(key)
            if previous is None:
                records[key] = DecayRecord(born=now, cycles=0, field=field)
                churn.append(ChurnRecord(nick=nick, key=key, text=text, event=BIRTH))
                continue

            records[key] = DecayRecord(
                born=previous.born, cycles=previous.cycles + 1, field=field
            )
            promoted = previous.field == RECENT_FIELD and field == TRAITS_FIELD
            churn.append(ChurnRecord(
                nick=nick, key=key, text=text, event=PROMOTE if promoted else CARRY
            ))

        decay[nick] = records

    born_traits = {
        nick: {
            key for key, record in records.items()
            if record.field == TRAITS_FIELD and record.cycles == 0
        }
        for nick, records in decay.items()
    }

    for nick, prior in prior_decay.items():
        survivors = decay.get(nick, {})
        lost_recent = False
        for key in prior:
            if key in survivors or (nick, key) in guard_dropped:
                continue
            churn.append(ChurnRecord(nick=nick, key=key, text=key, event=VANISH))
            lost_recent = lost_recent or prior[key].field == RECENT_FIELD

        candidates = born_traits.get(nick) or set()
        if lost_recent and candidates:
            churn.append(ChurnRecord(
                nick=nick,
                key=sorted(candidates)[0],
                text=_sample_text(updated, nick, sorted(candidates)[0]),
                event=PROMOTE_CANDIDATE,
            ))

    return decay, churn


def _policy_survivors(entries: list[str], records: dict[str, DecayRecord], keep: int, max_cycles: int):
    """Selects the `recent` entries the real policy would keep.

    Newest `keep` by `born` rather than by list position, plus a hard drop of
    anything that has outlived `max_cycles`. Survivors come back in the order the
    model emitted them: sorting the stored list would flip it newest-first and make
    the next cycle's positional baseline keep the wrong end.

    Returns:
        `(survivors, evicted)` where `evicted` is `[(entry, reason)]`.
    """
    aged = []
    for index, entry in enumerate(entries):
        record = records.get(normalize(entry))
        # An entry with no record was just born, so it sorts newest; `index` breaks
        # ties in emission order.
        aged.append((record.born if record else '￿', index, entry))

    expired = {
        index for _, index, entry in aged
        if (record := records.get(normalize(entry))) and record.cycles > max_cycles
    }
    ranked = [item for item in aged if item[1] not in expired]
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    kept = {index for _, index, _ in ranked[:keep]}

    survivors, evicted = [], []
    for index, entry in enumerate(entries):
        if index in kept:
            survivors.append(entry)
        else:
            evicted.append((entry, CYCLES_REASON if index in expired else CAP_REASON))
    return survivors, evicted


def apply_decay(
    updated: StructuredMemory,
    decay: Decay,
    enabled: bool,
    traits_keep: int,
    recent_keep: int,
    max_cycles: int,
) -> list[EvictionRecord]:
    """Evicts overflow from `updated` and prunes the sidecar to the survivors.

    Computes two survivor sets and applies one:

    - **baseline**, always applied: positional caps on every list, the corrected
      form of the deleted `StructuredMemory.trim()`.
    - **policy**, applied only when `enabled`: `recent` keeps the newest N by
      `born` and drops anything past `max_cycles`. `traits` and `state` use the
      baseline caps unchanged.

    Everything the *policy* would remove is recorded either way, so the log-only
    phase still reports exactly what the real policy would have done.

    Trait eviction is deliberately left as the positional baseline. With neither a
    confidence nor a recurrence signal every available rule is bad — oldest-first
    kills the most established trait, newest-first ossifies the list so nothing new
    can enter. Raising the cap to 10 fixes the damage; the rule waits for the
    overflow numbers this phase collects.

    Args:
        updated: Memory to evict from. Mutated in place.
        decay: This cycle's sidecar, from `reconcile`. Pruned in place.
        enabled: Whether to apply the policy or only report it.
        traits_keep: Cap on `traits`.
        recent_keep: Cap on `recent`.
        max_cycles: Age past which a `recent` entry is dropped.

    Returns:
        Every entry the policy selected for removal.
    """
    evictions: list[EvictionRecord] = []

    for nick, info in updated.participants.items():
        records = decay.get(nick, {})

        survivors, evicted = _policy_survivors(info.recent, records, recent_keep, max_cycles)
        evictions.extend(
            EvictionRecord(nick=nick, field=RECENT_FIELD, text=entry, reason=reason, applied=enabled)
            for entry, reason in evicted
        )

        info.traits = info.traits[-traits_keep:]
        info.recent = survivors if enabled else info.recent[-recent_keep:]

        # Prune to what survived, so the sidecar cannot accumulate records forever
        # and, worse, resurrect a stale `born`/`cycles` onto an entry the model
        # re-emits after eviction — which would re-evict it immediately, every time.
        live = {normalize(entry) for entry in [*info.traits, *info.recent]}
        live.discard('')
        pruned = {key: record for key, record in records.items() if key in live}
        if pruned:
            decay[nick] = pruned
        else:
            decay.pop(nick, None)

    state = updated.state
    state.active_topics = state.active_topics[-TOPICS_KEEP:]
    state.open_questions = state.open_questions[-QUESTIONS_KEEP:]
    state.running_jokes = state.running_jokes[-JOKES_KEEP:]

    for nick in set(decay) - set(updated.participants):
        decay.pop(nick)

    return evictions
