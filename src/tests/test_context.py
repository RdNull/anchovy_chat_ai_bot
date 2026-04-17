from unittest.mock import AsyncMock, MagicMock, call

from src import settings
from src.memory.repository import get_last_memory
from src.messages.repository import save_message
from src.models import Message, UserRole
from src.processors.context import run_context_checks, update_chat_memory
from src.processors.context.embeddings import get_last_embedding_task, update_chat_embeddings
from src.processors.context.memory import StructuredMemory


def make_message(chat_id=1, role=UserRole.USER, text='hello', nickname='user1'):
    return Message(chat_id=chat_id, role=role, text=text, nickname=nickname)


def mock_memory_llm(mocker, return_value=None):
    result = return_value if return_value is not None else StructuredMemory()
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=result)
    mocker.patch('src.processors.context.memory.ai.get_memory_model', return_value=mock_llm)
    return mock_llm


def mock_embeddings_client(mocker):
    return mocker.patch(
        'src.processors.context.embeddings.messages_embeddings_client.save_embeddings',
        new_callable=AsyncMock,
    )


# --- run_context_checks threshold logic ---

async def test_run_context_checks_below_threshold_no_update(mocker):
    mocker.patch.object(settings, 'LAST_MESSAGES_SIZE', 2)
    mock_memory = mocker.patch('src.processors.context.update_chat_memory', new_callable=AsyncMock)
    mock_embed = mocker.patch('src.processors.context.update_chat_embeddings',
                              new_callable=AsyncMock)

    await save_message(make_message())  # 1 < threshold 2
    await run_context_checks(1)

    assert mock_memory.call_count == 0
    assert mock_embed.call_count == 0


async def test_run_context_checks_triggers_memory_update(mocker):
    mocker.patch.object(settings, 'LAST_MESSAGES_SIZE', 2)
    mock_memory = mocker.patch('src.processors.context.update_chat_memory', new_callable=AsyncMock)
    mocker.patch('src.processors.context.update_chat_embeddings', new_callable=AsyncMock)

    await save_message(make_message(text='msg1'))
    await save_message(make_message(text='msg2'))  # 2 >= threshold 2
    await run_context_checks(1)

    assert mock_memory.call_count == 1
    assert mock_memory.call_args == call(1)


async def test_run_context_checks_triggers_embedding_update(mocker):
    mocker.patch.object(settings, 'LAST_MESSAGES_SIZE', 2)
    mocker.patch('src.processors.context.update_chat_memory', new_callable=AsyncMock)
    mock_embed = mocker.patch('src.processors.context.update_chat_embeddings',
                              new_callable=AsyncMock)

    await save_message(make_message(text='msg1'))
    await save_message(make_message(text='msg2'))
    await run_context_checks(1)

    assert mock_embed.call_count == 1
    assert mock_embed.call_args == call(1)


# --- update_chat_memory ---

async def test_update_chat_memory_saves_to_db(mocker):
    expected = StructuredMemory(constraints=['updated'])
    mock_memory_llm(mocker, return_value=expected)

    await save_message(make_message())
    await update_chat_memory(1)

    result = await get_last_memory(1)
    assert result is not None
    assert result.chat_id == 1
    assert result.content.constraints == ['updated']


async def test_update_chat_memory_no_op_when_no_messages(mocker):
    llm = mock_memory_llm(mocker)

    await update_chat_memory(1)

    assert llm.with_structured_output.return_value.ainvoke.call_count == 0
    assert await get_last_memory(1) is None


# --- update_chat_embeddings ---

async def test_update_chat_embeddings_calls_save_embeddings(mocker):
    mock_save = mock_embeddings_client(mocker)

    await save_message(make_message())
    await update_chat_embeddings(1)

    assert mock_save.call_count == 1
    saved_messages = mock_save.call_args[0][0]
    assert len(saved_messages) == 1
    assert saved_messages[0].text == 'hello'


async def test_update_chat_embeddings_saves_task(mocker):
    mock_embeddings_client(mocker)

    await save_message(make_message())
    await update_chat_embeddings(1)

    task = await get_last_embedding_task(1)
    assert task is not None
    assert task.chat_id == 1


async def test_update_chat_embeddings_no_op_when_no_messages(mocker):
    mock_save = mock_embeddings_client(mocker)

    await update_chat_embeddings(1)

    assert mock_save.call_count == 0
    assert await get_last_embedding_task(1) is None


async def test_update_chat_memory_lock_held(mocker):
    # Mocking the MEMORY_LOCK in src.processors.context.memory
    mock_lock = AsyncMock()
    mock_lock.locked.return_value = True
    # Since it's used as 'async with MEMORY_LOCK', we need to mock __aenter__
    # asyncio.TimeoutError is not caught by 'except Exception', so use Exception
    mock_lock.__aenter__.side_effect = Exception("Lock timeout")

    mocker.patch("src.processors.context.memory.MEMORY_LOCK", mock_lock)
    mock_logger = mocker.patch("src.processors.context.memory.logger")

    await update_chat_memory(123)

    assert mock_logger.error.call_count == 1
    assert "Error updating memory for chat 123" in mock_logger.error.call_args[0][0]


async def test_update_chat_memory_db_error(mocker):
    # Mocking save_memory in src.processors.context.memory
    mock_save = mocker.patch(
        "src.processors.context.memory.save_memory",
        AsyncMock(side_effect=Exception("DB memory error"))
    )
    mock_logger = mocker.patch("src.processors.context.memory.logger")

    # Needs some messages to trigger update
    msg = MagicMock()
    mocker.patch("src.processors.context.memory.get_history", AsyncMock(return_value=[msg] * 10))
    mocker.patch("src.processors.context.memory.ai.get_memory_model", MagicMock())
    mocker.patch("src.processors.context.memory.prompt_manager.get_prompt", return_value="p")

    # Mock LLM return
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=StructuredMemory(facts=[])
    )
    mocker.patch("src.processors.context.memory.ai.get_memory_model", return_value=mock_llm)

    await update_chat_memory(123)

    assert mock_logger.error.call_count == 1
    assert "Error updating memory for chat 123" in mock_logger.error.call_args[0][0]
