import logging


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
logging.getLogger('httpx').addFilter(TelegramPollingFilter())
logger = logging.getLogger('bot')
