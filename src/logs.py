import logging
import re
import time

from pythonjsonlogger.json import JsonFormatter

from src.log_context import LogContextFilter

# `bot<id>:<secret>`, the path prefix of every Telegram API call. Only the half after the
# colon is a credential; the id stays visible because it costs nothing and keeps the line
# diagnostic.
BOT_TOKEN_PATTERN = re.compile(r'(bot\d{6,}:)[\w-]{30,}')


class RedactBotToken(logging.Filter):
    """Masks the bot token wherever a record reaches the handler.

    PTB puts the token in the URL path and `httpx` logs every request line at INFO, so
    the credential was written to the pod log on every API call the bot made — readable
    by anything that later ships those logs somewhere else. Attached to the handler
    rather than to the `httpx` logger so a traceback quoting a URL is covered too.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if 'api.telegram.org' in message:
            # Collapsing args into msg is what makes the redaction stick: the token
            # arrives as a lazy `%s` argument, so rewriting `msg` alone would leave it.
            record.msg = BOT_TOKEN_PATTERN.sub(r'\1<redacted>', message)
            record.args = ()
        return True


class TelegramPollingFilter(logging.Filter):
    """Demotes Telegram long-polling request logs to DEBUG.

    `httpx` logs every `getUpdates` call at INFO, which floods the log with one
    line per polling cycle. The record is kept, but only surfaces when the
    handler itself is set to DEBUG.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno != logging.INFO:
            return True
        message = record.getMessage()
        if 'api.telegram.org' in message and 'getUpdates' in message:
            record.levelno = logging.DEBUG
            record.levelname = 'DEBUG'
            return logging.getLogger().getEffectiveLevel() <= logging.DEBUG
        return True


# One JSON object per line to stdout. No OTLP from the app: this keeps `kubectl logs` and
# Axiom showing the same bytes, and the collector is a DaemonSet with no Service to point
# an exporter at anyway. A `json_parser` operator in the collector lifts these keys into
# OTLP attributes (see manifests/otel-collector-config.yaml).
#
# No `%(asctime)s`: the CRI envelope already stamps every line and the collector puts it in
# `_time`, so a formatted timestamp here was ~24 redundant bytes per event. `%(name)s` is
# what finally makes `httpx`, `telegram.ext` and `asyncio` separable from our own records —
# though since every module does `from src.logs import logger`, `record.name` is `'bot'`
# for all of our own call sites and only separates *us* from third-party loggers.
_JSON_FORMAT = '%(levelname)s %(name)s %(module)s %(lineno)d %(message)s'

_RENAME_FIELDS = {
    'levelname': 'level',
    'name': 'logger',
    'lineno': 'line',
    'message': 'msg',  # `msg`, not `message`: the collector moves this key back into `body`.
    'exc_info': 'stack',
}

_formatter = JsonFormatter(
    _JSON_FORMAT,
    rename_fields=_RENAME_FIELDS,
    # The chat is Russian; escaping Cyrillic to \uXXXX triples the byte size of any line
    # carrying chat text and makes the Axiom UI unreadable.
    json_ensure_ascii=False,
    # A stray ObjectId, datetime or pydantic model in an `extra` must never raise inside
    # logging itself.
    json_default=repr,
)

_stream_handler = logging.StreamHandler()
_stream_handler.setFormatter(_formatter)
logging.basicConfig(level=logging.INFO, handlers=[_stream_handler])

# `basicConfig` does nothing at all when the root logger already has a handler, so whether
# the redaction applies would otherwise depend on whether anything logged before this module
# was imported. Attaching to the handlers that actually exist makes it unconditional. It has
# to be a *handler* filter rather than a logger one: a filter on the root logger never sees
# records propagated up from `httpx`, which is every record that carries a token.
for _handler in logging.getLogger().handlers:
    _handler.addFilter(RedactBotToken())
    # Same reasoning as RedactBotToken: records propagated from `httpx` and `telegram.ext`
    # should carry the bound chat context too, and a logger-level filter would never see them.
    _handler.addFilter(LogContextFilter())
logging.getLogger('httpx').addFilter(TelegramPollingFilter())
logger = logging.getLogger('bot')

# Keys `logging.LogRecord` already owns. Passing one through `extra=` raises
# "Attempt to overwrite 'x' in LogRecord" — a crash inside a log line.
_RESERVED = frozenset(
    {
        'name',
        'msg',
        'args',
        'levelname',
        'levelno',
        'pathname',
        'filename',
        'module',
        'exc_info',
        'exc_text',
        'stack_info',
        'lineno',
        'funcName',
        'created',
        'msecs',
        'relativeCreated',
        'thread',
        'threadName',
        'processName',
        'process',
        'message',
        'asctime',
        'taskName',
    }
)
# Owned by the `container` operator in the collector; a collision silently overwrites the CRI
# metadata during the attributes merge.
_COLLECTOR_OWNED = frozenset({'log', 'logtag'})
_FIELD_NAME_PATTERN = re.compile(r'[a-z][a-z0-9_]*')


def event(name: str, **fields) -> dict:
    """Builds the `extra=` payload for one structured log line.

    Usage: `logger.info('Memory updated', extra=event('MEMORY_EXTRACT', outcome='ok'))`.
    `logger` stays the stdlib logger, so `exc_info=`, `stacklevel=` and lazy `%s` args keep
    working untouched.

    Raises `ValueError` on a reserved/collector-owned key or a malformed field name — this
    is the one place a silent Axiom-side corruption (an overwritten CRI attribute, a crashed
    log call) can be introduced, so it fails loudly rather than quietly.
    """
    for key in fields:
        if key in _RESERVED:
            raise ValueError(f'{key!r} is a reserved LogRecord attribute')
        if key in _COLLECTOR_OWNED:
            raise ValueError(f'{key!r} is owned by the collector container operator')
        if not _FIELD_NAME_PATTERN.fullmatch(key):
            raise ValueError(f'{key!r} is not a valid field name')
    return {'event': name, **fields}


def elapsed_ms(started: float) -> int:
    """Milliseconds since `started` (a `time.monotonic()` timestamp).

    One helper rather than fifteen ad-hoc `(time.monotonic() - started) * 1000` expressions,
    so every `elapsed_ms` field in Axiom means the same thing.
    """
    return round((time.monotonic() - started) * 1000)
