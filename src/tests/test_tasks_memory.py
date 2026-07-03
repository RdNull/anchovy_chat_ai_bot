from unittest.mock import AsyncMock, call

from src import settings
from src.tasks.memory import run_memory_cleanup


async def test_run_memory_cleanup_calls_delete(mocker):
    mock_delete = mocker.patch(
        'src.tasks.memory.delete_old_memories',
        AsyncMock()
    )

    await run_memory_cleanup()

    assert mock_delete.call_count == 1
    assert mock_delete.call_args == call(settings.MEMORY_RETENTION_DAYS)


async def test_run_memory_cleanup_handles_errors(mocker):
    mocker.patch(
        'src.tasks.memory.delete_old_memories',
        AsyncMock(side_effect=RuntimeError('db error'))
    )
    mock_logger = mocker.patch('src.tasks.memory.logger')

    await run_memory_cleanup()

    assert mock_logger.error.call_count == 1
    assert 'db error' in mock_logger.error.call_args[0][0]
