import time

from src import settings
from src.rate_limit import SlidingWindowRateLimiter


def test_rate_limiter_allows_calls_under_limit():
    rl = SlidingWindowRateLimiter(rate_limit=3)
    assert not rl.is_exceeded(chat_id=1)
    assert not rl.is_exceeded(chat_id=1)
    assert not rl.is_exceeded(chat_id=1)


def test_rate_limiter_blocks_when_limit_reached(mocker):
    mocker.patch.object(settings, 'CHAT_RATE_LIMIT', 2)
    rl = SlidingWindowRateLimiter()
    rl.is_exceeded(1)
    rl.is_exceeded(1)
    assert rl.is_exceeded(1)


def test_rate_limiter_independent_per_chat(mocker):
    mocker.patch.object(settings, 'CHAT_RATE_LIMIT', 1)
    rl = SlidingWindowRateLimiter()
    rl.is_exceeded(1)
    assert rl.is_exceeded(1)
    assert not rl.is_exceeded(2)


def test_rate_limiter_allows_after_window_expires(mocker):
    mocker.patch.object(settings, 'CHAT_RATE_LIMIT', 1)
    rl = SlidingWindowRateLimiter()
    rl._call_times[1].append(time.monotonic() - 61)
    assert not rl.is_exceeded(1)
