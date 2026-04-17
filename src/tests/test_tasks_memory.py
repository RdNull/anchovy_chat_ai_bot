from unittest.mock import AsyncMock, call

from src.tasks.memory import update_all_chats_memory


async def test_update_all_chats_memory(mocker):
    # Mock active chats
    mock_get_active = mocker.patch(
        'src.tasks.memory.get_active_chats',
        AsyncMock(return_value=[123, 456])
    )

    # Mock memory update
    mock_update = mocker.patch(
        'src.tasks.memory.update_chat_memory',
        AsyncMock()
    )

    # Execute
    await update_all_chats_memory()

    # Assert
    assert mock_get_active.call_count == 1
    assert mock_update.call_count == 2
    assert mock_update.call_args_list == [call(123), call(456)]


async def test_update_all_chats_memory_with_errors(mocker):
    # Mock active chats
    mocker.patch(
        'src.tasks.memory.get_active_chats',
        AsyncMock(return_value=[123, 456, 789])
    )

    # Mock memory update with failure for one chat
    mock_update = AsyncMock()
    mock_update.side_effect = [None, ValueError("Boom"), None]
    mocker.patch('src.tasks.memory.update_chat_memory', mock_update)

    # Mock logger to verify error was logged
    mock_logger = mocker.patch('src.tasks.memory.logger')

    # Execute
    await update_all_chats_memory()

    # Assert
    assert mock_update.call_count == 3
    # Verify logger.error was called for the failing chat
    assert mock_logger.error.call_count == 1
    assert "Failed to update memory for 456: Boom" in mock_logger.error.call_args[0][0]
