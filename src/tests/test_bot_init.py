import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

from src import settings
from src.bot import ContextBindingApplication, log_sticker_corpus, main, setup_scheduler
from src.log_context import LogContextFilter


async def test_main_initialization(mocker):
    mock_loop = mocker.patch('asyncio.new_event_loop')
    mock_loop.return_value.create_task.side_effect = lambda coro: coro.close()
    mocker.patch('asyncio.set_event_loop')
    mock_builder = mocker.patch('src.bot.ApplicationBuilder')

    mock_app = MagicMock()
    chain = (
        mock_builder.return_value.token.return_value
        .application_class.return_value.http_version.return_value
    )
    chain.post_init.return_value.build.return_value = mock_app

    main()

    assert mock_builder.return_value.token.call_count == 1
    assert chain.post_init.call_count == 1
    assert mock_app.add_handler.call_count >= 9
    assert mock_app.add_error_handler.call_count == 1
    assert mock_app.run_polling.call_count == 1
    # the sticker-corpus boot log and the scheduler
    assert mock_loop.return_value.create_task.call_count == 2


async def test_setup_scheduler(mocker):
    mock_scheduler = mocker.patch('src.bot.Scheduler')
    mock_sleep = mocker.patch('asyncio.sleep', new_callable=AsyncMock)
    mock_sleep.side_effect = [None, asyncio.CancelledError()]

    try:
        await asyncio.wait_for(setup_scheduler(), timeout=2.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass

    assert mock_scheduler.call_count == 1
    assert mock_scheduler.return_value.weekly.call_count == 1
    assert mock_scheduler.return_value.daily.call_count == 1


async def test_log_sticker_corpus_reports_size_and_flag(mocker):
    mocker.patch('src.bot.sticker_corpus_size', return_value=7)
    mocker.patch.object(settings, 'ENABLE_STICKER_REPLIES', True)
    mock_logger = mocker.patch('src.bot.logger')

    await log_sticker_corpus()

    assert 'STICKER_CORPUS size=7 enabled=True' in mock_logger.info.call_args[0][0]


async def test_log_sticker_corpus_on_a_cold_start(mocker):
    mocker.patch('src.bot.sticker_corpus_size', return_value=0)
    mocker.patch.object(settings, 'ENABLE_STICKER_REPLIES', False)
    mock_logger = mocker.patch('src.bot.logger')

    await log_sticker_corpus()

    assert 'STICKER_CORPUS size=0 enabled=False' in mock_logger.info.call_args[0][0]


async def test_context_binding_application_binds_ids_before_delegating(mocker):
    """Only our own override is under test here -- not PTB's dispatch internals, which
    `create_task`'s own context-copying (already pinned down in test_log_context.py)
    is what makes correct for every handler group and the error handler alike."""
    observed = {}

    async def fake_process_update(self, update):
        record = logging.LogRecord(
            name='bot', level=logging.INFO, pathname='x.py', lineno=1,
            msg='handled', args=(), exc_info=None,
        )
        LogContextFilter().filter(record)
        observed['chat_id'] = getattr(record, 'chat_id', None)
        observed['user_id'] = getattr(record, 'user_id', None)
        observed['request_id'] = getattr(record, 'request_id', None)

    mocker.patch('telegram.ext.Application.process_update', fake_process_update)

    update = MagicMock()
    update.effective_chat.id = 222
    update.effective_user.id = 111

    app = object.__new__(ContextBindingApplication)  # bypass Application.__init__
    await app.process_update(update)

    assert observed['chat_id'] == 222
    assert observed['user_id'] == 111
    assert observed['request_id'] is not None
