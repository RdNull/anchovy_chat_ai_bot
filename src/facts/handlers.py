import re
import time
from datetime import date, datetime, UTC

from src import settings
from src.embeddings.facts import facts_embedding_client
from src.facts.models import FactKind, FactOp, FactOpType, FactOutcome, FactStatus, UserFact
from src.facts.processors import extract_facts, number_facts
from src.facts.repository import (
    add_sighting,
    create_fact,
    delete_fact,
    get_facts,
    list_overflow,
    replace_fact,
    set_status,
)
from src.logs import elapsed_ms, event, logger
from src.messages.models import Message, UserRole

_MENTION = re.compile(r'@(\w+)')
_VECTOR_FALLBACK = 'vector'
_VECTOR_FALLBACK_THRESHOLD = 0.6
_OWN_TEXT_MAX_CHARS = 2000


def is_bot_nickname(nickname: str) -> bool:
    """The bare bot nickname or any tagged form of it (`AnchovyAiBot[<code>]`)."""
    bot = settings.BOT_NICKNAME
    return nickname == bot or nickname.startswith(f'{bot}[')


def window_participants(messages: list[Message]) -> list[str]:
    """Authors, reply targets and @mentions of the window, bare, bot excluded, sorted."""
    seen = set()
    for message in messages:
        seen.add(message.nickname)
        if message.reply:
            seen.add(message.reply.nickname)

        seen.update(_MENTION.findall(message.text or ''))

    return sorted(n for n in seen if n and not is_bot_nickname(n))


async def load_existing(messages: list[Message]) -> dict[str, list[UserFact]]:
    """Per participant: every confirmed fact plus the candidates closest to their own messages."""
    existing = {}
    for nickname in window_participants(messages):
        facts = await get_facts(nickname, status=FactStatus.CONFIRMED)
        own_text = '\n'.join(m.text for m in messages if m.nickname == nickname and m.text)
        if own_text:
            results = await facts_embedding_client.search_facts(
                nickname,
                own_text[-_OWN_TEXT_MAX_CHARS:],
                limit=settings.FACTS_PROMPT_CANDIDATES,
                status=FactStatus.CANDIDATE,
                score_threshold=0.0,
            )
            facts = facts + [r.fact for r in results]

        existing[nickname] = facts

    return existing


_KIND_ORDER = {FactKind.BIO: 0, FactKind.HABIT: 1, FactKind.JOKE: 2}
_JOKE_PREFIX = 'шутка: '


def select_fact_users(target: Message | None, last_messages: list[Message]) -> list[str]:
    """Who a reply is about, most relevant first: bare nicknames, bot excluded, deduped."""
    if target:
        candidates = [target.nickname]
        if target.reply:
            candidates.append(target.reply.nickname)

        candidates.extend(_MENTION.findall(target.text or ''))
    else:
        users = [m for m in last_messages if m.role == UserRole.USER]
        candidates = [m.nickname for m in reversed(users)]

    selected = []
    for candidate in candidates:
        nickname = (candidate or '').replace('@', '')
        if nickname and not is_bot_nickname(nickname) and nickname not in selected:
            selected.append(nickname)

    return selected


def _fact_sort_key(fact: UserFact) -> tuple[int, float]:
    seen = fact.last_seen_at.timestamp() if fact.last_seen_at else 0.0
    return _KIND_ORDER[fact.kind], -seen


async def facts_for_reply(
    target: Message | None, last_messages: list[Message]
) -> dict[str, list[UserFact]]:
    """Confirmed facts of up to `FACTS_INJECT_MAX_USERS` people the reply is about."""
    result = {}
    for nickname in select_fact_users(target, last_messages):
        if len(result) >= settings.FACTS_INJECT_MAX_USERS:
            break

        facts = await get_facts(nickname, status=FactStatus.CONFIRMED)
        if facts:
            result[nickname] = sorted(facts, key=_fact_sort_key)

    return result


def format_facts(facts: dict[str, list[UserFact]] | None) -> str | None:
    """One line per person; jokes are marked so the model doesn't take them as real."""
    lines = []
    for nickname, person_facts in (facts or {}).items():
        parts = []
        for fact in person_facts:
            prefix = _JOKE_PREFIX if fact.kind == FactKind.JOKE else ''
            parts.append(f'{prefix}{fact.text}')

        if parts:
            lines.append(f'@{nickname}: {"; ".join(parts)}')

    return '\n'.join(lines) or None


def _confirm_days(kind: FactKind) -> int:
    return settings.JOKE_CONFIRM_DAYS if kind == FactKind.JOKE else settings.FACT_CONFIRM_DAYS


def _is_promoted(kind: FactKind, self_stated: bool, sightings: int) -> bool:
    if kind == FactKind.BIO and self_stated:
        return True

    return sightings >= _confirm_days(kind)


def _status_for(kind: FactKind, self_stated: bool, sightings: int) -> FactStatus:
    promoted = _is_promoted(kind, self_stated, sightings)
    return FactStatus.CONFIRMED if promoted else FactStatus.CANDIDATE


def _log_op(op: FactOp, nickname: str, outcome: FactOutcome, fallback: str | None = None) -> None:
    logger.info(
        'Fact op',
        extra=event(
            'FACT_OP',
            op=op.op.value,
            kind=op.kind.value,
            nickname=nickname,
            outcome=outcome.value,
            reason=op.reason,
            fallback=fallback,
        ),
    )


async def _confirm(fact: UserFact, op: FactOp, today: date) -> FactOutcome:
    updated = await add_sighting(fact.id, today)
    if updated is None:
        return FactOutcome.INVALID_TARGET

    is_candidate = updated.status == FactStatus.CANDIDATE
    if is_candidate and _is_promoted(updated.kind, op.self_stated, len(updated.sightings)):
        await set_status(updated.id, FactStatus.CONFIRMED)
        promoted = updated.model_copy(update={'status': FactStatus.CONFIRMED})
        await facts_embedding_client.save_fact(promoted)
        return FactOutcome.PROMOTED

    return FactOutcome.CONFIRMED


async def _add(op: FactOp, nickname: str, today: date) -> tuple[FactOutcome, str | None]:
    similar = await facts_embedding_client.search_facts(
        nickname, op.text, limit=1, score_threshold=_VECTOR_FALLBACK_THRESHOLD
    )
    if similar:
        return await _confirm(similar[0].fact, op, today), _VECTOR_FALLBACK

    status = _status_for(op.kind, op.self_stated, 1)
    fact = await create_fact(nickname, op.kind, op.text, status, today)
    await facts_embedding_client.save_fact(fact)
    return FactOutcome.CREATED, None


async def _replace(fact: UserFact, op: FactOp, today: date) -> FactOutcome:
    status = _status_for(op.kind, op.self_stated, 1)
    updated = await replace_fact(fact.id, op.kind, op.text, status, today)
    if updated is None:
        return FactOutcome.INVALID_TARGET

    await facts_embedding_client.save_fact(updated)
    return FactOutcome.REPLACED


async def apply_op(op: FactOp, fact_map: dict[str, UserFact], today: date) -> str | None:
    """Applies one op; returns the bare nickname it touched, or None when it was dropped."""
    nickname = op.nickname.replace('@', '')
    if is_bot_nickname(nickname):
        _log_op(op, nickname, FactOutcome.BOT_DROPPED)
        return None

    fact = None
    if op.op != FactOpType.ADD:
        fact = fact_map.get(op.target) if op.target else None
        if fact is None or fact.nickname != nickname:
            _log_op(op, nickname, FactOutcome.INVALID_TARGET)
            return None

    fallback = None
    match op.op:
        case FactOpType.ADD:
            outcome, fallback = await _add(op, nickname, today)
        case FactOpType.CONFIRM:
            outcome = await _confirm(fact, op, today)
        case FactOpType.REPLACE:
            outcome = await _replace(fact, op, today)

    _log_op(op, nickname, outcome, fallback)
    return None if outcome == FactOutcome.INVALID_TARGET else nickname


async def _drop(fact: UserFact, reason: str) -> None:
    await delete_fact(fact.id)
    await facts_embedding_client.delete_fact(fact.id)
    logger.info(
        'Fact deleted',
        extra=event('FACT_DELETED', fact_id=fact.id, nickname=fact.nickname, reason=reason),
    )


async def enforce_caps(nicknames: set[str]) -> None:
    caps = {
        FactStatus.CONFIRMED: settings.FACTS_CONFIRMED_CAP,
        FactStatus.CANDIDATE: settings.FACTS_CANDIDATE_CAP,
    }
    for nickname in nicknames:
        for status, cap in caps.items():
            for fact in await list_overflow(nickname, status, cap):
                await _drop(fact, 'cap')


async def update_user_facts(new_messages: list[Message]) -> None:
    started = time.monotonic()
    try:
        existing = await load_existing(new_messages)
        fact_map = number_facts(existing)
        ops = await extract_facts(new_messages, existing)

        today = datetime.now(UTC).date()
        touched = set()
        for op in ops:
            if nickname := await apply_op(op, fact_map, today):
                touched.add(nickname)

        await enforce_caps(touched)

        logger.info(
            'Extracted and applied fact ops',
            extra=event(
                'FACT_EXTRACT',
                outcome='ok',
                count=len(ops),
                elapsed_ms=elapsed_ms(started),
            ),
        )
    except Exception:
        logger.error(
            'Error extracting facts from messages',
            exc_info=True,
            extra=event('FACT_EXTRACT', outcome='error'),
        )
