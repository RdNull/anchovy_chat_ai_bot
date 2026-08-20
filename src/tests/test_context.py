from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, call

from src import settings
from src.memory.models import ChatState, DecayRecord, MemoryData, ParticipantInfo
from src.memory.processors import StructuredMemory, extract_memory
from src.memory.repository import get_last_memory
from src.messages.repository import save_message
from src.processors.context.embeddings import get_last_embedding_task, update_chat_embeddings
from src.processors.context.handlers import run_context_checks, update_chat_context
from src.tests.test_utils import make_message


def mock_memory_llm(mocker, return_value=None):
    result = return_value if return_value is not None else StructuredMemory()
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=result)
    mocker.patch('src.memory.processors.ai.get_memory_model', return_value=mock_llm)
    return mock_llm


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

    mock_memory_llm(
        mocker,
        return_value=StructuredMemory(state=ChatState(open_questions=['oppa']))
    )
    mocker.patch(
        'src.processors.context.handlers.get_messages',
        AsyncMock(return_value=[make_message(text='hi')] * 10)
    )
    mocker.patch('src.facts.processors.extract_facts')

    await update_chat_context(123)

    assert mock_logger.error.call_count == 1
    assert 'Failed to parse memory JSON for chat 123' in mock_logger.error.call_args[0][0]


# --- extract_memory ---

async def test_extract_memory_caps_oversized_lists(mocker):
    data_items = [str(i) for i in range(8)]
    bloated = StructuredMemory(
        participants={
            '@alice': ParticipantInfo(
                traits=data_items,
                recent=[f'r{i}' for i in range(8)],
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
    # Under TRAITS_KEEP=10 the whole traits list survives — the inverted cap that
    # used to evict the oldest, most established trait is what this asserts is gone.
    assert saved.content.participants['@alice'].traits == data_items
    assert saved.content.participants['@alice'].recent == ['r3', 'r4', 'r5', 'r6', 'r7']
    assert saved.content.state.active_topics == ['5', '6', '7']
    assert saved.content.state.open_questions == ['3', '4', '5', '6', '7']
    assert saved.content.state.running_jokes == ['3', '4', '5', '6', '7']


async def test_extract_memory_no_op_when_llm_returns_falsy(mocker):
    mock_memory_llm(mocker, return_value=None)
    mocker.patch('src.memory.processors.prompt_manager.get_prompt', return_value='p')
    mock_logger = mocker.patch('src.memory.processors.logger')

    await extract_memory(chat_id=1, current_memory=None, new_messages=[make_message()])

    assert await get_last_memory(1) is None
    assert mock_logger.error.call_count == 1


async def test_extract_memory_disabled_saves_empty_memory(mocker):
    mocker.patch.object(settings, 'ENABLE_MEMORY_PROCESSING', False)
    mock_llm = mock_memory_llm(mocker)

    await extract_memory(chat_id=1, current_memory=None, new_messages=[make_message()])

    assert mock_llm.with_structured_output.return_value.ainvoke.call_count == 0
    saved = await get_last_memory(1)
    assert saved is not None
    assert saved.content == StructuredMemory()


async def test_extract_memory_enabled_runs_llm(mocker):
    mocker.patch.object(settings, 'ENABLE_MEMORY_PROCESSING', True)
    result = StructuredMemory(state=ChatState(active_topics=['topic']))
    mock_llm = mock_memory_llm(mocker, return_value=result)
    mocker.patch('src.memory.processors.prompt_manager.get_prompt', return_value='p')

    await extract_memory(chat_id=1, current_memory=None, new_messages=[make_message()])

    assert mock_llm.with_structured_output.return_value.ainvoke.call_count == 1
    saved = await get_last_memory(1)
    assert saved is not None
    assert saved.content.state.active_topics == ['topic']


async def test_extract_memory_resolves_attribution_before_eviction(mocker):
    valid = [f't{i}' for i in range(1, 11)]
    current = MemoryData(
        chat_id=1,
        created_at=datetime.now(timezone.utc),
        content=StructuredMemory(participants={'@bob': ParticipantInfo(traits=['дубль'])}),
    )
    llm_result = StructuredMemory(
        participants={
            '@alice': ParticipantInfo(traits=[*valid, 'Дубль!']),
            '@bob': ParticipantInfo(traits=['дубль']),
        }
    )
    mock_memory_llm(mocker, return_value=llm_result)
    mocker.patch('src.memory.processors.prompt_manager.get_prompt', return_value='p')
    mock_logger = mocker.patch('src.memory.processors.logger')

    await extract_memory(chat_id=1, current_memory=current, new_messages=[make_message()])

    saved = await get_last_memory(1)
    assert saved is not None
    # 11 traits, guard drops the one that belongs to @bob — so all ten valid traits
    # fit under the cap. Without the guard running first, t1 would have been evicted.
    assert saved.content.participants['@alice'].traits == valid
    assert saved.content.participants['@bob'].traits == ['дубль']

    conflict_logs = [
        c[0][0] for c in mock_logger.info.call_args_list
        if c[0][0].startswith('MEMORY_ATTRIBUTION_CONFLICT')
    ]
    assert len(conflict_logs) == 1
    assert 'chat_id=1' in conflict_logs[0]
    assert 'action=dropped' in conflict_logs[0]
    assert 'reason=incumbent_wins' in conflict_logs[0]
    assert 'owner=@alice' in conflict_logs[0]
    assert 'kept=@bob' in conflict_logs[0]
    assert 'field=traits' in conflict_logs[0]
    assert 'text=Дубль!' in conflict_logs[0]


async def test_extract_memory_logs_churn_and_would_evict(mocker):
    """The phase-1 instrument: the log is the deliverable, so assert its shape."""
    current = MemoryData(
        chat_id=1,
        created_at=datetime.now(timezone.utc),
        content=StructuredMemory(
            participants={'@alice': ParticipantInfo(recent=['ездил в Лондон', 'опоздал'])}
        ),
        decay={'@alice': {
            'ездил в лондон': DecayRecord(born='26-04-24 18:00', cycles=3, field='recent'),
            'опоздал': DecayRecord(born='26-04-30 18:00', cycles=1, field='recent'),
        }},
    )
    llm_result = StructuredMemory(
        participants={'@alice': ParticipantInfo(recent=['ездил в Лондон', 'купил велосипед'])}
    )
    mock_memory_llm(mocker, return_value=llm_result)
    mocker.patch('src.memory.processors.prompt_manager.get_prompt', return_value='p')
    mock_logger = mocker.patch('src.memory.processors.logger')
    mocker.patch.object(settings, 'RECENT_KEEP', 1)

    await extract_memory(chat_id=1, current_memory=current, new_messages=[make_message()])

    logs = [c[0][0] for c in mock_logger.info.call_args_list]
    churn = next(line for line in logs if line.startswith('MEMORY_CHURN '))
    assert churn == (
        'MEMORY_CHURN chat_id=1 nicks=1 carried=1 added=1 vanished=1 '
        'promoted=0 promote_candidates=0'
    )

    lost = [line for line in logs if line.startswith('MEMORY_CHURN_LOST')]
    assert lost == ['MEMORY_CHURN_LOST chat_id=1 nick=@alice text=опоздал']

    # Decay is off, so the older entry is only reported, never actually dropped.
    decayed = [line for line in logs if line.startswith('MEMORY_DECAY')]
    assert decayed == [
        'MEMORY_DECAY chat_id=1 nick=@alice field=recent action=would_evict '
        'reason=cap text=ездил в Лондон'
    ]
    saved = await get_last_memory(1)
    assert saved.content.participants['@alice'].recent == ['купил велосипед']


async def test_extract_memory_logs_trait_overflow(mocker):
    llm_result = StructuredMemory(
        participants={'@alice': ParticipantInfo(traits=[f't{i}' for i in range(12)])}
    )
    mock_memory_llm(mocker, return_value=llm_result)
    mocker.patch('src.memory.processors.prompt_manager.get_prompt', return_value='p')
    mock_logger = mocker.patch('src.memory.processors.logger')

    await extract_memory(chat_id=1, current_memory=None, new_messages=[make_message()])

    logs = [c[0][0] for c in mock_logger.info.call_args_list]
    assert [line for line in logs if line.startswith('MEMORY_TRAIT_OVERFLOW')] == [
        'MEMORY_TRAIT_OVERFLOW chat_id=1 nick=@alice count=12'
    ]
