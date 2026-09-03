import logging
import re

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


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)

# `basicConfig` does nothing at all when the root logger already has a handler, so whether
# the redaction applies would otherwise depend on whether anything logged before this module
# was imported. Attaching to the handlers that actually exist makes it unconditional. It has
# to be a *handler* filter rather than a logger one: a filter on the root logger never sees
# records propagated up from `httpx`, which is every record that carries a token.
for _handler in logging.getLogger().handlers:
    _handler.addFilter(RedactBotToken())
logging.getLogger('httpx').addFilter(TelegramPollingFilter())
logger = logging.getLogger('bot')
