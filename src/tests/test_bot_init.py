import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.bot import main, setup_scheduler


async def test_main_initialization(mocker):
    # Mock all external dependencies of main()
    mock_persistence = mocker.patch("src.bot.PicklePersistence")
    mock_loop = mocker.patch("asyncio.new_event_loop")
    mock_set_loop = mocker.patch("asyncio.set_event_loop")
    mock_builder = mocker.patch("src.bot.ApplicationBuilder")

    mock_app = MagicMock()
    mock_builder.return_value.token.return_value.persistence.return_value.http_version.return_value.build.return_value = mock_app

    # Execute main
    main()

    # Verify app initialization
    assert mock_builder.return_value.token.call_count == 1
    assert mock_app.add_handler.call_count >= 8  # start, info, list, random, select, mention, media, conversation
    assert mock_app.add_error_handler.call_count == 1
    assert mock_app.run_polling.call_count == 1

    # Verify scheduler task creation
    assert mock_loop.return_value.create_task.call_count == 1


async def test_setup_scheduler(mocker):
    mock_scheduler = mocker.patch("src.bot.Scheduler")
    # Mocking asyncio.sleep with an AsyncMock to behave like a coroutine
    mock_sleep = mocker.patch("asyncio.sleep", new_callable=AsyncMock)
    # Side effect: first call returns None, second call raises CancelledError
    mock_sleep.side_effect = [None, asyncio.CancelledError()]

    try:
        # Wrap in wait_for to prevent infinite loop
        await asyncio.wait_for(setup_scheduler(), timeout=2.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass

    assert mock_scheduler.call_count == 1
    assert mock_scheduler.return_value.hourly.call_count == 1
