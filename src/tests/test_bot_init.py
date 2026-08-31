import asyncio
from unittest.mock import AsyncMock, MagicMock

from src import settings
from src.bot import log_sticker_corpus, main, setup_scheduler


async def test_main_initialization(mocker):
    mock_loop = mocker.patch('asyncio.new_event_loop')
    mock_loop.return_value.create_task.side_effect = lambda coro: coro.close()
    mocker.patch('asyncio.set_event_loop')
    mock_builder = mocker.patch('src.bot.ApplicationBuilder')

    mock_app = MagicMock()
    mock_builder.return_value.token.return_value.http_version.return_value.build.return_value = mock_app

    main()

    assert mock_builder.return_value.token.call_count == 1
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


async def test_log_sticker_corpus_reports_an_open_gate(mocker):
    mocker.patch('src.bot.sticker_corpus_size', return_value=7)
    mocker.patch.object(settings, 'ENABLE_STICKER_REPLIES', True)
    mocker.patch.object(settings, 'STICKER_MIN_CORPUS', 5)
    mock_logger = mocker.patch('src.bot.logger')

    await log_sticker_corpus()

    logged = mock_logger.info.call_args[0][0]
    assert 'STICKER_CORPUS size=7 min=5 enabled=True gate_open=True' in logged


async def test_log_sticker_corpus_reports_a_closed_gate(mocker):
    mocker.patch('src.bot.sticker_corpus_size', return_value=2)
    mocker.patch.object(settings, 'ENABLE_STICKER_REPLIES', True)
    mocker.patch.object(settings, 'STICKER_MIN_CORPUS', 5)
    mock_logger = mocker.patch('src.bot.logger')

    await log_sticker_corpus()

    assert 'gate_open=False' in mock_logger.info.call_args[0][0]
