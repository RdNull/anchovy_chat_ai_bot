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
dropped it» — which is the whole question `vanished` exists to answer. It is a
*coincidence count, not a pairing*: with no shared key there is nothing to match a
new trait against a lost entry, so every trait born on a nick that lost a `recent`
entry is reported, and the churn log prints `lost_recent` beside it so the two
numbers can be read against each other. Claiming a specific pair would be inventing
a link the data does not contain.

Cycles, not wall clock, are the native unit here. The memory update is triggered
by a message count, so a wall-clock TTL empties a quiet chat's memory every cycle
or two while never binding at all in a busy one — it bites exactly where it does
the most damage.
"""

from datetime import datetime, timezone

from src import settings
from src.memory.keys import RECENT_FIELD, TRAITS_FIELD, normalize
from src.memory.models import Decay, DecayRecord, StructuredMemory
from src.models import BaseModel, format_ts

BIRTH = 'birth'
CARRY = 'carry'
VANISH = 'vanish'
PROMOTE = 'promote'
PROMOTE_CANDIDATE = 'promote_candidate'

# The whole contract for `ChurnRecord.event`. Consumers derive their counters from
# this rather than restating a subset that then falls behind.
EVENTS = (BIRTH, CARRY, VANISH, PROMOTE, PROMOTE_CANDIDATE)

CAP_REASON = 'cap'
CYCLES_REASON = 'cycles'
# The policy would have kept this entry and the positional baseline dropped it
# anyway. Only reachable while decay is disabled, and it is the measurement that
# phase exists for: the size of the gap between what runs and what is proposed.
BASELINE_REASON = 'baseline'


class ChurnRecord(BaseModel):
    """One entry's fate across a single memory cycle."""

    nick: str
    key: str
    text: str
    field: str  # 'traits' | 'recent' — for a vanish, the list it lived in last cycle
    event: str  # one of `EVENTS`


class EvictionRecord(BaseModel):
    """One entry eviction removed, or would have removed.

    `applied` says whether the entry actually left memory this cycle. During the
    log-only phase the policy is computed but the positional baseline is what runs,
    so the two disagree in both directions: a policy drop the baseline kept is
    recorded with `applied=False`, and a baseline drop the policy would have kept is
    recorded with `reason=baseline` and `applied=True`.
    """

    nick: str
    field: str
    text: str
    reason: str  # 'cap' | 'cycles' | 'baseline'
    applied: bool


class DecayCaps(BaseModel):
    """Every limit eviction applies, in one carrier.

    One object rather than seven parameters, and one place the deployment values are
    read — `settings` is the only source, per the project rule, and nothing here
    carries a default that could quietly become a second one.
    """

    enabled: bool
    traits_keep: int
    recent_keep: int
    max_cycles: int
    topics_keep: int
    questions_keep: int
    jokes_keep: int

    @classmethod
    def from_settings(cls) -> 'DecayCaps':
        return cls(
            enabled=settings.ENABLE_MEMORY_DECAY,
            traits_keep=settings.TRAITS_KEEP,
            recent_keep=settings.RECENT_KEEP,
            max_cycles=settings.RECENT_MAX_CYCLES,
            topics_keep=settings.TOPICS_KEEP,
            questions_keep=settings.QUESTIONS_KEEP,
            jokes_keep=settings.JOKES_KEEP,
        )


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
    info = memory.participants.get(nick)
    for entry in [*info.traits, *info.recent] if info else []:
        if normalize(entry) == key:
            return entry
    return key


def reconcile(updated: StructuredMemory, prior_decay: Decay, now: str) -> Decay:
    """Ages the sidecar forward one cycle.

    `prior_decay` is the *sole* authority on what was stored last cycle. The
    sidecar holds a record for every stored entry, so its key set is the prior key
    set; deriving it a second way from the previous snapshot's content would let
    the two drift.

    Reports nothing: churn is `summarize_churn`'s job, and it has to run *after*
    `apply_decay` or it counts entries that eviction deletes moments later.

    Args:
        updated: Memory the model just emitted, after the attribution guard ran.
        prior_decay: Last cycle's sidecar.
        now: This cycle's timestamp, from `resolve_now`.

    Returns:
        The sidecar for this cycle, before eviction prunes it.
    """
    decay: Decay = {}

    for nick in updated.participants:
        prior = prior_decay.get(nick, {})
        records: dict[str, DecayRecord] = {}

        for key, field in _emitted_fields(updated, nick).items():
            previous = prior.get(key)
            if previous is None:
                records[key] = DecayRecord(born=now, cycles=0, field=field)
            else:
                records[key] = DecayRecord(
                    born=previous.born, cycles=previous.cycles + 1, field=field
                )

        decay[nick] = records

    return decay


def _cut(size: int, keep: int) -> int:
    """Index the positional cap splits a list at.

    Spelled out rather than `[-keep:]` because `keep == 0` makes that slice return
    the whole list — a misconfiguration that would silently disable the cap.
    """
    return max(size - keep, 0)


def _policy_selection(
    entries: list[str],
    records: dict[str, DecayRecord],
    keep: int,
    max_cycles: int,
) -> tuple[set[int], set[int]]:
    """Selects the `recent` entries the real policy would keep.

    Newest `keep` by `born` rather than by list position, plus a hard drop of
    anything that has outlived `max_cycles`.

    Works in list indices, not entry text: the model can emit the same string twice,
    and a set of survivors keyed by text would then account for one of them twice
    and the other never.

    Returns:
        `(kept, expired)`, both sets of indices into `entries`.
    """
    expired = {
        index for index, entry in enumerate(entries)
        if (record := records.get(normalize(entry))) and record.cycles > max_cycles
    }

    ranked = []
    for index, entry in enumerate(entries):
        if index in expired:
            continue
        record = records.get(normalize(entry))
        # An entry with no record was just born, so it sorts newest; `index` breaks
        # ties in emission order.
        ranked.append((record.born if record else '￿', index))

    ranked.sort(reverse=True)
    return {index for _, index in ranked[:keep]}, expired


def apply_decay(
    updated: StructuredMemory,
    decay: Decay,
    caps: DecayCaps,
) -> list[EvictionRecord]:
    """Evicts overflow from `updated` and prunes the sidecar to the survivors.

    Computes two survivor sets and applies one:

    - **baseline**, applied while decay is off: positional caps on every list, the
      corrected form of the deleted `StructuredMemory.trim()`.
    - **policy**, applied when `caps.enabled`: `recent` keeps the newest N by `born`
      and drops anything past `max_cycles`. `traits` and `state` use the baseline
      caps unchanged.

    Every entry either set removes is recorded, tagged with whether it actually left
    memory. Recording only the policy's picks made the log-only phase report an
    eviction that did not happen and hide the baseline drop that did.

    Trait eviction is deliberately left as the positional baseline. With neither a
    confidence nor a recurrence signal every available rule is bad — oldest-first
    kills the most established trait, newest-first ossifies the list so nothing new
    can enter. Raising the cap to 10 fixes the damage; the rule waits for the
    overflow numbers this phase collects. It is still *recorded*, so the phase sees
    which traits the placeholder rule cost.

    Args:
        updated: Memory to evict from. Mutated in place.
        decay: This cycle's sidecar, from `reconcile`. Pruned in place.
        caps: The limits to apply, and whether the policy is live.

    Returns:
        Every entry removed, plus every entry the policy would have removed.
    """
    evictions: list[EvictionRecord] = []

    for nick, info in updated.participants.items():
        records = decay.get(nick, {})

        policy_kept, expired = _policy_selection(
            info.recent, records, caps.recent_keep, caps.max_cycles
        )
        baseline_kept = set(range(_cut(len(info.recent), caps.recent_keep), len(info.recent)))
        kept = policy_kept if caps.enabled else baseline_kept

        for index, entry in enumerate(info.recent):
            if index in policy_kept and index in kept:
                continue
            if index in policy_kept:
                reason = BASELINE_REASON
            else:
                reason = CYCLES_REASON if index in expired else CAP_REASON
            evictions.append(EvictionRecord(
                nick=nick,
                field=RECENT_FIELD,
                text=entry,
                reason=reason,
                applied=index not in kept,
            ))

        trait_cut = _cut(len(info.traits), caps.traits_keep)
        evictions.extend(
            EvictionRecord(
                nick=nick, field=TRAITS_FIELD, text=trait, reason=CAP_REASON, applied=True
            )
            for trait in info.traits[:trait_cut]
        )

        info.traits = info.traits[trait_cut:]
        info.recent = [entry for index, entry in enumerate(info.recent) if index in kept]

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
    state.active_topics = state.active_topics[_cut(len(state.active_topics), caps.topics_keep):]
    state.open_questions = state.open_questions[
        _cut(len(state.open_questions), caps.questions_keep):
    ]
    state.running_jokes = state.running_jokes[_cut(len(state.running_jokes), caps.jokes_keep):]

    for nick in set(decay) - set(updated.participants):
        decay.pop(nick)

    return evictions


def summarize_churn(
    updated: StructuredMemory,
    prior_decay: Decay,
    decay: Decay,
    guard_records: list,
    evictions: list[EvictionRecord],
) -> list[ChurnRecord]:
    """Reports what moved this cycle, measured against what was actually saved.

    Runs *after* `apply_decay`, on the pruned sidecar. Running it before counted an
    entry eviction deleted two lines later as `carried`, and then pruning erased its
    record so it never surfaced as a `vanish` on any later cycle either — the loss
    was invisible in both directions.

    Args:
        updated: Memory as it will be saved, after guard and eviction.
        prior_decay: Last cycle's sidecar — the authority on what was stored.
        decay: This cycle's sidecar, pruned by `apply_decay` to the survivors.
        guard_records: `ConflictRecord`s from the guard.
        evictions: Records from `apply_decay`.

    Returns:
        Every churn event, in nick order: survivors first, then losses. Entries the
        guard or eviction removed are not `vanish` — they are accounted for in their
        own log lines, and counting them here would report a known loss as an
        unexplained one.
    """
    accounted = {
        (record.owner, normalize(record.text)) for record in guard_records if record.removed
    }
    accounted.update(
        (record.nick, normalize(record.text)) for record in evictions if record.applied
    )

    churn: list[ChurnRecord] = []

    for nick in updated.participants:
        prior = prior_decay.get(nick, {})
        for key, record in decay.get(nick, {}).items():
            text = _sample_text(updated, nick, key)
            if record.cycles == 0:
                churn.append(ChurnRecord(
                    nick=nick, key=key, text=text, field=record.field, event=BIRTH
                ))
                continue

            previous = prior.get(key)
            promoted = (
                previous is not None
                and previous.field == RECENT_FIELD
                and record.field == TRAITS_FIELD
            )
            churn.append(ChurnRecord(
                nick=nick,
                key=key,
                text=text,
                field=record.field,
                event=PROMOTE if promoted else CARRY,
            ))

    for nick, prior in prior_decay.items():
        survivors = decay.get(nick, {})
        lost_recent = 0
        for key, record in prior.items():
            if key in survivors or (nick, key) in accounted:
                continue
            churn.append(ChurnRecord(
                nick=nick, key=key, text=key, field=record.field, event=VANISH
            ))
            if record.field == RECENT_FIELD:
                lost_recent += 1

        if not lost_recent:
            continue

        # Every trait born this cycle, not one picked out of them: nothing pairs a
        # reworded trait to the entry it replaced, so reporting a single candidate
        # made a nick that generalised three events look like it lost two.
        born_traits = sorted(
            key for key, record in survivors.items()
            if record.field == TRAITS_FIELD and record.cycles == 0
        )
        churn.extend(
            ChurnRecord(
                nick=nick,
                key=key,
                text=_sample_text(updated, nick, key),
                field=TRAITS_FIELD,
                event=PROMOTE_CANDIDATE,
            )
            for key in born_traits
        )

    return churn
