from unittest.mock import AsyncMock, MagicMock, call

from src import settings
from src.memory.models import ChatState, ParticipantInfo, RecentItem
from src.memory.processors import StructuredMemory, extract_memory
from src.memory.repository import get_last_memory
from src.messages.repository import save_message
from src.processors.context.embeddings import get_last_embedding_task, update_chat_embeddings
from src.processors.context.handlers import run_context_checks, update_chat_context
from src.tests.test_utils import make_message


def mock_memory_llm(mocker, return_value=None):
    result = return_value if return_value is not None else StructuredMemory()
    mock_llm = MagicMock()
    mock_ainvoke = AsyncMock(return_value=result)
    mock_llm.__or__.return_value.with_retry.return_value.ainvoke = mock_ainvoke
    mocker.patch('src.memory.processors.ai.get_memory_model', return_value=mock_llm)
    return mock_ainvoke


def mock_embeddings_client(mocker):
    return mocker.patch(
        'src.processors.context.embeddings.messages_embeddings_client.save',
        new_callable=AsyncMock,
    )


# --- run_context_checks threshold logic ---

async def test_run_context_checks_below_threshold_no_update(mocker):
    mocker.patch.object(settings, 'LAST_MESSAGES_SIZE', 2)
    mock_memory = mocker.patch('src.processors.context.handlers.update_chat_context')
    mock_embed = mocker.patch('src.processors.context.handlers.update_chat_embeddings')

    await save_message(make_message())  # 1 < threshold 2
    await run_context_checks(1)

    assert mock_memory.call_count == 0
    assert mock_embed.call_count == 0


async def test_run_context_checks_triggers_memory_update(mocker):
    mocker.patch.object(settings, 'LAST_MESSAGES_SIZE', 2)
    mock_memory = mocker.patch('src.processors.context.handlers.update_chat_context')
    mocker.patch('src.processors.context.handlers.update_chat_embeddings')

    await save_message(make_message(text='msg1'))
    await save_message(make_message(text='msg2'))  # 2 >= threshold 2
    await run_context_checks(1)

    assert mock_memory.call_count == 1
    assert mock_memory.call_args == call(1)


async def test_run_context_checks_triggers_embedding_update(mocker):
    mocker.patch.object(settings, 'LAST_MESSAGES_SIZE', 2)
    mocker.patch('src.processors.context.handlers.update_chat_context')
    mock_embed = mocker.patch('src.processors.context.handlers.update_chat_embeddings')

    await save_message(make_message(text='msg1'))
    await save_message(make_message(text='msg2'))
    await run_context_checks(1)

    assert mock_embed.call_count == 1
    assert mock_embed.call_args == call(1)


# --- update_chat_memory ---

async def test_update_chat_memory_saves_to_db(mocker):
    mocker.patch.object(settings, 'LAST_MESSAGES_MIN_SIZE', 1)
    expected = StructuredMemory(
        state=ChatState(open_questions=['oppa'])
    )
    mock_memory_llm(mocker, return_value=expected)
    mocker.patch('src.facts.processors.extract_facts')

    await save_message(make_message())
    await update_chat_context(1)

    result = await get_last_memory(1)
    assert result is not None
    assert result.chat_id == 1
    assert result.content.state.open_questions == ['oppa']


async def test_update_chat_memory_no_op_when_no_messages(mocker):
    mock_ainvoke = mock_memory_llm(mocker)

    await update_chat_context(1)

    assert mock_ainvoke.call_count == 0
    assert await get_last_memory(1) is None


async def test_update_chat_memory_no_op_below_min_size(mocker):
    mocker.patch.object(settings, 'LAST_MESSAGES_MIN_SIZE', 3)
    mock_ainvoke = mock_memory_llm(mocker)
    mocker.patch('src.facts.processors.extract_facts')

    await save_message(make_message(text='msg1'))
    await save_message(make_message(text='msg2'))  # 2 < min_size 3
    await update_chat_context(1)

    assert mock_ainvoke.call_count == 0
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


async def test_update_chat_context_lock_held(mocker):
    # Mocking the CHAT_CONTEXT_LOCK in src.processors.context.memory
    mock_lock = AsyncMock()
    mock_lock.locked.return_value = True
    # Since it's used as 'async with CHAT_CONTEXT_LOCK', we need to mock __aenter__
    # asyncio.TimeoutError is not caught by 'except Exception', so use Exception
    mock_lock.__aenter__.side_effect = Exception('Lock timeout')

    mocker.patch('src.processors.context.handlers.CHAT_CONTEXT_LOCK', mock_lock)
    mock_logger = mocker.patch('src.processors.context.handlers.logger')

    await update_chat_context(123)

    assert mock_logger.error.call_count == 1
    assert 'Error updating memory for chat 123' in mock_logger.error.call_args[0][0]


async def test_update_chat_memory_db_error(mocker):
    mocker.patch(
        'src.memory.processors.save_memory',
        AsyncMock(side_effect=Exception('DB memory error'))
    )
    mock_logger = mocker.patch('src.memory.processors.logger')
    mocker.patch('src.memory.processors.prompt_manager.get_prompt', return_value='p')

    mock_memory_llm(mocker, return_value=StructuredMemory())
    mocker.patch(
        'src.processors.context.handlers.get_messages',
        AsyncMock(return_value=[make_message(text='hi')] * 10)
    )
    mocker.patch('src.facts.processors.extract_facts')

    await update_chat_context(123)

    assert mock_logger.error.call_count == 1
    assert 'Failed to parse memory JSON for chat 123' in mock_logger.error.call_args[0][0]


# --- extract_memory ---

async def test_extract_memory_trims_oversized_lists(mocker):
    data_items = [str(i) for i in range(8)]
    bloated = StructuredMemory(
        participants={
            '@alice': ParticipantInfo(
                traits=data_items,
                recent=[RecentItem(text=str(i), last_seen_at='26-05-01 10:00') for i in range(8)],
            )
        },
        state=ChatState(
            active_topics=data_items,
            open_questions=data_items,
            running_jokes=data_items,
        ),
    )
    mock_memory_llm(mocker, return_value=bloated)
    mocker.patch('src.memory.processors.prompt_manager.get_prompt', return_value='p')

    await extract_memory(chat_id=1, current_memory=None, new_messages=[make_message()])

    saved = await get_last_memory(1)
    assert saved is not None
    assert saved.content.participants['@alice'].traits == ['3', '4', '5', '6', '7']
    assert [r.text for r in saved.content.participants['@alice'].recent] == [
        '3', '4', '5', '6', '7'
    ]
    assert saved.content.state.active_topics == ['3', '4', '5', '6', '7']
    assert saved.content.state.open_questions == ['3', '4', '5', '6', '7']
    assert saved.content.state.running_jokes == ['3', '4', '5', '6', '7']


async def test_extract_memory_no_op_when_llm_returns_falsy(mocker):
    mock_llm = MagicMock()
    mock_llm.__or__.return_value.with_retry.return_value.ainvoke = AsyncMock(return_value=None)
    mocker.patch('src.memory.processors.ai.get_memory_model', return_value=mock_llm)
    mocker.patch('src.memory.processors.prompt_manager.get_prompt', return_value='p')
    mock_logger = mocker.patch('src.memory.processors.logger')

    await extract_memory(chat_id=1, current_memory=None, new_messages=[make_message()])

    assert await get_last_memory(1) is None
    assert mock_logger.error.call_count == 1
