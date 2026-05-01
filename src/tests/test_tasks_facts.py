from unittest.mock import AsyncMock

from src.tasks.facts import run_fact_decay


async def test_run_fact_decay_calls_decay(mocker):
    mock_decay = mocker.patch(
        'src.tasks.facts.decay_all_facts',
        AsyncMock()
    )

    await run_fact_decay()

    assert mock_decay.call_count == 1


async def test_run_fact_decay_handles_errors(mocker):
    mocker.patch(
        'src.tasks.facts.decay_all_facts',
        AsyncMock(side_effect=RuntimeError('db error'))
    )
    mock_logger = mocker.patch('src.tasks.facts.logger')

    await run_fact_decay()

    assert mock_logger.error.call_count == 1
    assert 'db error' in mock_logger.error.call_args[0][0]
