import re
import time
from datetime import date, datetime, UTC

from src import settings
from src.embeddings.facts import facts_embedding_client
from src.facts.models import FactKind, FactOp, FactStatus, UserFact
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
from src.messages.models import Message

_MENTION = re.compile(r'@(\w+)')
_VECTOR_FALLBACK_THRESHOLD = 0.6
_OWN_TEXT_MAX_CHARS = 2000


def _today() -> date:
    return datetime.now(UTC).date()


def is_bot_nickname(nickname: str) -> bool:
    """The bare bot nickname or any tagged form of it (`AnchovyAiBot[<code>]`)."""
    bot = settings.BOT_NICKNAME
    return nickname == bot or nickname.startswith(f'{bot}[')


def window_participants(messages: list[Message]) -> list[str]:
    """Authors, reply targets and @mentions of the window, bare, bot excluded, in first-seen order."""
    seen: dict[str, None] = {}
    for message in messages:
        seen[message.nickname] = None
        if message.reply:
            seen[message.reply.nickname] = None

        for mention in _MENTION.findall(message.text or ''):
            seen[mention] = None

    return [n for n in seen if n and not is_bot_nickname(n)]


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


def _confirm_days(kind: FactKind) -> int:
    return settings.JOKE_CONFIRM_DAYS if kind == FactKind.JOKE else settings.FACT_CONFIRM_DAYS


def _is_promoted(kind: FactKind, self_stated: bool, sightings: int) -> bool:
    if kind == FactKind.BIO and self_stated:
        return True

    return sightings >= _confirm_days(kind)


def _status_for(kind: FactKind, self_stated: bool, sightings: int) -> FactStatus:
    promoted = _is_promoted(kind, self_stated, sightings)
    return FactStatus.CONFIRMED if promoted else FactStatus.CANDIDATE


def _log_op(op: FactOp, nickname: str, outcome: str, fallback: str | None = None) -> None:
    logger.info(
        'Fact op',
        extra=event(
            'FACT_OP',
            op=op.op,
            kind=op.kind.value,
            nickname=nickname,
            outcome=outcome,
            reason=op.reason,
            fallback=fallback,
        ),
    )


async def _confirm(fact: UserFact, op: FactOp, today: date) -> str:
    updated = await add_sighting(fact.id, today)
    if updated is None:
        return 'invalid_target'

    is_candidate = updated.status == FactStatus.CANDIDATE
    if is_candidate and _is_promoted(updated.kind, op.self_stated, len(updated.sightings)):
        await set_status(updated.id, FactStatus.CONFIRMED)
        promoted = updated.model_copy(update={'status': FactStatus.CONFIRMED})
        await facts_embedding_client.save_fact(promoted)
        return 'promoted'

    return 'confirmed'


async def _add(op: FactOp, nickname: str, today: date) -> tuple[str, str | None]:
    similar = await facts_embedding_client.search_facts(
        nickname, op.text, limit=1, score_threshold=_VECTOR_FALLBACK_THRESHOLD
    )
    if similar:
        return await _confirm(similar[0].fact, op, today), 'vector'

    status = _status_for(op.kind, op.self_stated, 1)
    fact = await create_fact(nickname, op.kind, op.text, status, today)
    await facts_embedding_client.save_fact(fact)
    return 'created', None


async def _replace(fact: UserFact, op: FactOp, today: date) -> str:
    status = _status_for(op.kind, op.self_stated, 1)
    updated = await replace_fact(fact.id, op.kind, op.text, status, today)
    if updated is None:
        return 'invalid_target'

    await facts_embedding_client.save_fact(updated)
    return 'replaced'


async def apply_op(op: FactOp, fact_map: dict[str, UserFact], today: date) -> str | None:
    """Applies one op; returns the bare nickname it touched, or None when it was dropped."""
    nickname = op.nickname.replace('@', '')
    if is_bot_nickname(nickname):
        _log_op(op, nickname, 'bot_dropped')
        return None

    fact = None
    if op.op != 'add':
        fact = fact_map.get(op.target) if op.target else None
        if fact is None or fact.nickname != nickname:
            _log_op(op, nickname, 'invalid_target')
            return None

    fallback = None
    if op.op == 'add':
        outcome, fallback = await _add(op, nickname, today)
    elif op.op == 'confirm':
        outcome = await _confirm(fact, op, today)
    else:
        outcome = await _replace(fact, op, today)

    _log_op(op, nickname, outcome, fallback)
    return None if outcome == 'invalid_target' else nickname


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

        today = _today()
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
