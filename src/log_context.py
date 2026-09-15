"""Per-task logging context, bound once and inherited by everything it sets in motion.

Uses a `ContextVar` rather than a parameter because `asyncio.create_task` copies the
current context into the new task. `handle_conversation` fires
`create_task(run_context_checks(chat_id))` and `create_task(handle_media_message(...))`;
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
from typing import Generator

_LOG_CONTEXT: ContextVar[dict[str, object]] = ContextVar('log_context', default={})


@contextmanager
def log_context(**fields: object) -> Generator[None, None, None]:
    """Binds `fields` onto every log record emitted within the block (and its child tasks).

    Sets a *new* dict rather than mutating the current one, so a nested `log_context` call
    only ever adds to or shadows the outer binding for the duration of its own block, and
    the outer binding is restored exactly on exit — concurrent tasks sharing an ancestor
    context never see each other's nested bindings.
    """
    current = _LOG_CONTEXT.get()
    token = _LOG_CONTEXT.set({**current, **fields})
    try:
        yield
    finally:
        _LOG_CONTEXT.reset(token)


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
