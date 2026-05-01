import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.bot import main, setup_scheduler


async def test_main_initialization(mocker):
    mock_persistence = mocker.patch('src.bot.PicklePersistence')
    mock_loop = mocker.patch('asyncio.new_event_loop')
    mock_loop.return_value.create_task.side_effect = lambda coro: coro.close()
    mocker.patch('asyncio.set_event_loop')
    mock_builder = mocker.patch('src.bot.ApplicationBuilder')

    mock_app = MagicMock()
    mock_builder.return_value.token.return_value.persistence.return_value.http_version.return_value.build.return_value = mock_app

    main()

    assert mock_persistence.call_count == 1
    assert mock_builder.return_value.token.call_count == 1
    assert mock_app.add_handler.call_count >= 8
    assert mock_app.add_error_handler.call_count == 1
    assert mock_app.run_polling.call_count == 1
    assert mock_loop.return_value.create_task.call_count == 1


async def test_setup_scheduler(mocker):
    mock_scheduler = mocker.patch('src.bot.Scheduler')
    mock_sleep = mocker.patch('asyncio.sleep', new_callable=AsyncMock)
    mock_sleep.side_effect = [None, asyncio.CancelledError()]

    try:
        await asyncio.wait_for(setup_scheduler(), timeout=2.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass

    assert mock_scheduler.call_count == 1
    assert mock_scheduler.return_value.hourly.call_count == 1
    assert mock_scheduler.return_value.weekly.call_count == 1
