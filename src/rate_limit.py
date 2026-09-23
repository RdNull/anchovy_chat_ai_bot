import time
from collections import defaultdict, deque

from src.logs import event, logger

_WINDOW = 60  # seconds


class SlidingWindowRateLimiter:
    """Per-chat sliding-window limiter, one instance per budget it guards.

    `name` is what tells two exhausted budgets apart in the log — the character's
    reply limit and the web-search limit are both keyed by chat, so the chat id
    alone does not say which one bound.
    """

    def __init__(self, rate_limit: int = 1, name: str = 'chat') -> None:
        self._call_times: dict[int, deque] = defaultdict(deque)
        self.rate_limit = rate_limit
        self.name = name

    def is_exceeded(self, chat_id: int) -> bool:
        now = time.monotonic()
        call_times = self._call_times[chat_id]
        while call_times and now - call_times[0] > _WINDOW:
            call_times.popleft()

        if len(call_times) >= self.rate_limit:
            # chat_id comes from the bound context; `limiter` (not `name`) is what tells two
            # exhausted budgets apart, since `name` is a reserved LogRecord attribute.
            logger.warning(
                'Rate limit exceeded',
                extra=event('RATE_LIMIT_EXCEEDED', limiter=self.name, limit=self.rate_limit),
            )
            return True

        call_times.append(now)
        return False
