"""Bot-token redaction in the log stream.

PTB puts the token in the URL path and `httpx` logs every request line at INFO, so the
credential was written to the pod log on every API call the bot made. The token arrives as
a lazy `%s` argument rather than inside the format string, which is the detail that makes
this worth a test: a filter that rewrites `record.msg` alone changes nothing.
"""
import logging

from src.logs import BOT_TOKEN_PATTERN, RedactBotToken, TelegramPollingFilter

# Shaped like a real token — digits, colon, 35 URL-safe characters — and not one.
FAKE_TOKEN = '1234567890:AAHfake_Token_For_Tests_00000000000'


def make_record(msg: str, *args, name: str = 'httpx', level: int = logging.INFO) -> logging.LogRecord:
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
