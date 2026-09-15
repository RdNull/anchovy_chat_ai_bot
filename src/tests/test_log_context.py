"""`log_context` binding and its propagation through `asyncio.create_task`.

The whole point of a `ContextVar` over a parameter is that `asyncio.create_task` copies the
current context into the new task — these tests are what pin that behaviour down.
"""
import asyncio
import logging

from src.log_context import LogContextFilter, log_context


def make_record(name: str = 'bot') -> logging.LogRecord:
    return logging.LogRecord(
        name=name, level=logging.INFO, pathname='x.py', lineno=1,
        msg='handled', args=(), exc_info=None,
    )


def test_log_context_reaches_a_record_logged_inside_the_block():
    with log_context(chat_id=7):
        record = make_record()
        LogContextFilter().filter(record)

        assert record.chat_id == 7


def test_log_context_does_not_leak_after_the_block_exits():
    with log_context(chat_id=7):
        pass

    record = make_record()
    LogContextFilter().filter(record)

    assert not hasattr(record, 'chat_id')


def test_log_context_reaches_a_task_started_within_the_block():
    async def scenario():
        results = {}

        async def logged_from_task():
            record = make_record()
            LogContextFilter().filter(record)
            results['chat_id'] = getattr(record, 'chat_id', None)

        with log_context(chat_id=7):
            task = asyncio.create_task(logged_from_task())
            await task

        return results

    results = asyncio.run(scenario())

    assert results['chat_id'] == 7


def test_log_context_does_not_reach_a_task_started_outside_the_block():
    async def scenario():
        results = {}

        async def logged_from_task():
            record = make_record()
            LogContextFilter().filter(record)
            results['chat_id'] = getattr(record, 'chat_id', None)

        with log_context(chat_id=7):
            pass

        task = asyncio.create_task(logged_from_task())
        await task

        return results

    results = asyncio.run(scenario())

    assert results['chat_id'] is None


def test_an_explicit_extra_beats_the_context_value():
    with log_context(chat_id=7):
        record = make_record()
        record.chat_id = 9  # what an explicit `extra={'chat_id': 9}` would have set
        LogContextFilter().filter(record)

        assert record.chat_id == 9


def test_nested_log_context_shadows_without_mutating_the_outer_binding():
    with log_context(chat_id=7, task='outer'):
        with log_context(task='inner'):
            inner_record = make_record()
            LogContextFilter().filter(inner_record)

        outer_record = make_record()
        LogContextFilter().filter(outer_record)

    assert inner_record.chat_id == 7
    assert inner_record.task == 'inner'
    assert outer_record.task == 'outer'
