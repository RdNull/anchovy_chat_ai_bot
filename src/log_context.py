"""Per-task logging context, bound once and inherited by everything it sets in motion.

Uses a `ContextVar` rather than a parameter because `asyncio.create_task` copies the
current context into the new task. `handle_conversation` fires
`create_task(run_followups(chat_id))` and `create_task(handle_media_message(...))`;
`run_initiative_checks` fires `create_task(_run_initiative_reply(...))`. All of those
inherit whatever `log_context` bound at the top of the call chain **for free** — which is
exactly the causal chain that cannot be reconstructed from Axiom's flat event list otherwise.

One consequence worth stating plainly: a memory extraction is a detached task triggered by
whichever message tipped the `MEMORY_TRIGGER_SIZE` counter, so it inherits *that* message's
`request_id`, not the request_id of any particular user turn inside the window it processes.
That is causally correct and deliberate — `request_id` means "one update and everything it
set in motion", not "one user turn" — but it means a memory-extraction event's `request_id`
does not identify every message it touched, only the one that scheduled the run.

No import from `src` here: `src/logs.py` is a leaf module imported by almost every other
module in the project, and this module is imported by `src/logs.py` in turn.
"""

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Generator
from uuid import uuid4

_LOG_CONTEXT: ContextVar[dict[str, object]] = ContextVar('log_context', default={})


def _with_request_id(current: dict[str, object], fields: dict[str, object]) -> dict[str, object]:
    """`request_id` is generated here rather than by every call site: the first bind in a
    chain (the one where `request_id` is absent from both the current context and `fields`)
    mints one, and every nested bind inherits it unchanged. A call site that wants a fresh
    id of its own — none do today — can still pass `request_id=` explicitly.
    """
    if 'request_id' not in current and 'request_id' not in fields:
        return {**fields, 'request_id': uuid4().hex[:8]}
    return fields


@contextmanager
def log_context(**fields: object) -> Generator[None]:
    """Binds `fields` onto every log record emitted within the block (and its child tasks),
    restoring the prior binding on exit.

    Sets a *new* dict rather than mutating the current one, so a nested `log_context` call
    only ever adds to or shadows the outer binding for the duration of its own block, and
    the outer binding is restored exactly on exit — concurrent tasks sharing an ancestor
    context never see each other's nested bindings.

    Use this, not `push_log_context`, at any site that can run more than once *inside one
    task* — the reset is what lets `request_id` regenerate on the next run instead of
    sticking forever. Both `python-telegram-bot`'s own update loop (`max_concurrent_updates`
    defaults to 1) and this project's `scheduler` jobs work exactly that way: one task
    created once, looping `while ...: await handler(...)` internally rather than spawning a
    fresh task per update or per firing. `ContextBindingApplication.process_update` and
    `tasks/facts.py`/`tasks/memory.py` bind here for that reason.
    """
    current = _LOG_CONTEXT.get()
    fields = _with_request_id(current, fields)
    token = _LOG_CONTEXT.set({**current, **fields})
    try:
        yield
    finally:
        _LOG_CONTEXT.reset(token)


def push_log_context(**fields: object) -> None:
    """Binds `fields` for the rest of the current task, with no cleanup and no `with` block.

    Only safe where nothing meaningful runs afterward, *in this same task*, that must not
    see the binding — a task spawned fresh for one purpose and then left to finish: a
    boot-time background task, one backfill script's own process, one HTTP request's own
    task (uvicorn calls `loop.create_task` per request, not per connection, so keep-alive
    requests on the same connection still get separate tasks and separate contexts).
    `log_sticker_corpus`, `BearerAuth.__call__` and the backfill scripts use this.

    Not safe at a site that can fire more than once inside one long-lived task — see
    `log_context`'s docstring for the two verified cases that shape this split.
    """
    current = _LOG_CONTEXT.get()
    fields = _with_request_id(current, fields)
    _LOG_CONTEXT.set({**current, **fields})


class LogContextFilter(logging.Filter):
    """Stamps the bound context fields onto every record that reaches the handler.

    An explicit `extra=` on the call site always wins over the ambient context — checked
    with `hasattr` rather than overwriting, since the log call is closer to the fact being
    logged than whatever happened to be bound around it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in _LOG_CONTEXT.get().items():
            if not hasattr(record, key):
                setattr(record, key, value)
        return True
