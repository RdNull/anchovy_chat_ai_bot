"""Bot-token redaction in the log stream, and the JSON formatter / `event()` contract.

PTB puts the token in the URL path and `httpx` logs every request line at INFO, so the
credential was written to the pod log on every API call the bot made. The token arrives as
a lazy `%s` argument rather than inside the format string, which is the detail that makes
this worth a test: a filter that rewrites `record.msg` alone changes nothing.
"""

import json
import logging
import sys

import pytest

from src.logs import (
    BOT_TOKEN_PATTERN,
    RedactBotToken,
    TelegramPollingFilter,
    _formatter,
    event,
)

# Shaped like a real token — digits, colon, 35 URL-safe characters — and not one.
FAKE_TOKEN = '1234567890:AAHfake_Token_For_Tests_00000000000'


def make_record(
    msg: str, *args, name: str = 'httpx', level: int = logging.INFO
) -> logging.LogRecord:
    """A record built the way `httpx` builds one: the URL is an argument, not the message."""
    return logging.LogRecord(
        name=name,
        level=level,
        pathname='httpx/_client.py',
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


def test_the_token_is_masked_in_a_telegram_request_line():
    record = make_record(
        'HTTP Request: %s %s "%s"',
        'POST',
        f'https://api.telegram.org/bot{FAKE_TOKEN}/sendMessage',
        'HTTP/2 200 OK',
    )

    RedactBotToken().filter(record)

    assert FAKE_TOKEN not in record.getMessage()
    assert 'bot1234567890:<redacted>/sendMessage' in record.getMessage()


def test_the_bot_id_survives_the_redaction():
    """Only the half after the colon is a credential, and the id keeps the line diagnostic."""
    record = make_record('HTTP Request: %s', f'https://api.telegram.org/bot{FAKE_TOKEN}/getMe')

    RedactBotToken().filter(record)

    assert 'bot1234567890:' in record.getMessage()


def test_redaction_survives_a_second_format_pass():
    """The filter collapses args into msg; formatting it again must not raise or unmask."""
    record = make_record('HTTP Request: %s', f'https://api.telegram.org/bot{FAKE_TOKEN}/getMe')

    RedactBotToken().filter(record)

    assert record.getMessage() == record.getMessage()
    assert record.args == ()
    assert FAKE_TOKEN not in record.getMessage()


def test_a_token_inside_an_exception_message_is_masked_too():
    """The filter sits on the handler, not the `httpx` logger, so tracebacks are covered."""
    record = make_record(
        f'telegram.error.NetworkError: https://api.telegram.org/bot{FAKE_TOKEN}/getUpdates failed',
        name='telegram.ext',
        level=logging.ERROR,
    )

    RedactBotToken().filter(record)

    assert FAKE_TOKEN not in record.getMessage()


def test_an_unrelated_record_keeps_its_lazy_arguments():
    """Only Telegram lines are rewritten — everything else keeps deferred formatting."""
    record = make_record('Getting embedding for chat %s', -1002814232184)

    RedactBotToken().filter(record)

    assert record.args == (-1002814232184,)
    assert record.getMessage() == 'Getting embedding for chat -1002814232184'


def test_every_record_passes_the_filter():
    """A redaction filter must never drop a line — it is not a level filter."""
    telegram = make_record('HTTP Request: %s', f'https://api.telegram.org/bot{FAKE_TOKEN}/getMe')
    other = make_record('STICKER_CORPUS size=%s', 36)

    assert RedactBotToken().filter(telegram) is True
    assert RedactBotToken().filter(other) is True


def test_the_pattern_ignores_a_url_that_merely_starts_with_bot():
    """`bot` followed by too few digits or too short a secret is not a token."""
    assert BOT_TOKEN_PATTERN.search('https://api.telegram.org/bot123:short/getMe') is None


def test_polling_lines_are_still_demoted():
    """The pre-existing filter is unaffected by the new one sitting on the handler."""
    record = make_record('HTTP Request: %s', f'https://api.telegram.org/bot{FAKE_TOKEN}/getUpdates')

    surfaced = TelegramPollingFilter().filter(record)

    assert record.levelname == 'DEBUG'
    assert surfaced is False


def test_the_filter_is_wired_into_the_root_handlers():
    """Wiring, not behaviour: the filter is inert if `basicConfig` stops receiving it.

    Asserted as "at least one" rather than "exactly one" on purpose. Under pytest the root
    logger already carries the plugin's own handler by the time `src.logs` is imported, so
    the loop there legitimately attaches to two; run outside pytest there is one handler
    holding one filter. Either way no handler emits unredacted, which is the claim.
    """
    attached = [
        f
        for handler in logging.getLogger().handlers
        for f in handler.filters
        if isinstance(f, RedactBotToken)
    ]

    assert attached


def format_record(record: logging.LogRecord) -> dict:
    """Runs a record through the same `JsonFormatter` the handler carries, and parses it."""
    return json.loads(_formatter.format(record))


def test_event_round_trips_an_int_not_a_string():
    """`chat_id` must stay a queryable int in Axiom, not stringify into a text column."""
    record = make_record('handled', name='bot', level=logging.INFO)
    record.__dict__.update(event('SOME_EVENT', chat_id=1))

    formatted = format_record(record)

    assert formatted['chat_id'] == 1
    assert isinstance(formatted['chat_id'], int)


@pytest.mark.parametrize(
    ('bad_fields', 'match'),
    [
        ({'module': 1}, 'reserved'),  # reserved LogRecord attribute
        ({'log': 1}, 'collector'),  # owned by the collector's container operator
        ({'args': 1}, 'reserved'),  # reserved LogRecord attribute
        ({'Foo': 1}, 'not a valid field name'),  # not [a-z][a-z0-9_]*
    ],
)
def test_event_rejects_a_bad_field_name(bad_fields, match):
    with pytest.raises(ValueError, match=match):
        event('SOME_EVENT', **bad_fields)


def test_exc_info_produces_a_single_line_with_a_populated_stack():
    """A traceback must land as one field on one event, not split across records."""
    try:
        raise RuntimeError('boom')
    except RuntimeError:
        record = logging.LogRecord(
            name='bot',
            level=logging.ERROR,
            pathname='x.py',
            lineno=1,
            msg='failed',
            args=(),
            exc_info=sys.exc_info(),
        )

    formatted = format_record(record)

    assert isinstance(formatted['stack'], str)
    assert 'Traceback (most recent call last)' in formatted['stack']


def test_a_non_serializable_value_formats_without_raising():
    from bson import ObjectId

    record = make_record('handled', name='bot', level=logging.INFO)
    record.__dict__.update(event('SOME_EVENT', oid=ObjectId()))

    formatted = format_record(record)

    assert isinstance(formatted['oid'], str)


def test_cyrillic_survives_unescaped():
    record = make_record('handled', name='bot', level=logging.INFO)
    record.__dict__.update(event('SOME_EVENT', nick='@толя'))

    raw = _formatter.format(record)

    assert '@толя' in raw
    assert '\\u' not in raw


def test_redact_bot_token_still_redacts_after_json_formatting():
    """The filter runs on the record; the formatter runs after it, so order matters."""
    record = make_record('HTTP Request: %s', f'https://api.telegram.org/bot{FAKE_TOKEN}/getMe')

    RedactBotToken().filter(record)
    formatted = format_record(record)

    assert FAKE_TOKEN not in json.dumps(formatted)
    assert 'bot1234567890:<redacted>' in formatted['msg']


def test_logger_field_is_present_and_names_a_third_party_logger():
    record = make_record('HTTP Request: %s', 'https://example.com', name='httpx')

    formatted = format_record(record)

    assert formatted['logger'] == 'httpx'
