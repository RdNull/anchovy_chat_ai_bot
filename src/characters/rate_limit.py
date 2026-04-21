import time
from collections import defaultdict, deque

from src.logs import logger

_WINDOW = 60  # seconds


class ChatRateLimiter:
    def __init__(self, rate_limit: int = 1):
        self._call_times: dict[int, deque] = defaultdict(deque)
        self.rate_limit = rate_limit

    def is_exceeded(self, chat_id: int) -> bool:
        now = time.monotonic()
        call_times = self._call_times[chat_id]
        while call_times and now - call_times[0] > _WINDOW:
            call_times.popleft()

        if len(call_times) >= self.rate_limit:
            logger.warning(f'Rate limit exceeded for chat {chat_id}')
            return True

        call_times.append(now)
        return False
