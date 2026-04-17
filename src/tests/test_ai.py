from unittest.mock import MagicMock, call

import pytest

from src import ai


@pytest.fixture(autouse=True)
def clear_llm_cache():
    ai._llm_cache.clear()
    yield
    ai._llm_cache.clear()


def test_get_model_caching(mocker):
    # Mock model_manager and init_chat_model
    mock_settings = {'model': 'test-model', 'provider': 'openai'}
    mocker.patch('src.ai.model_manager.get_model_settings', return_value=mock_settings)
    mock_init = mocker.patch('src.ai.init_chat_model', return_value=MagicMock())

    # First call - should initialize
    model1 = ai.get_model('v1')
    assert ai.model_manager.get_model_settings.call_count == 1
    assert ai.model_manager.get_model_settings.call_args == call('chat', 'v1')
    assert mock_init.call_count == 1
    assert mock_init.call_args == call(model='test-model', provider='openai')

    # Second call - should use cache
    model2 = ai.get_model('v1')
    assert ai.model_manager.get_model_settings.call_count == 1
    assert mock_init.call_count == 1
    assert model1 is model2

    # Different version - should initialize new
    model3 = ai.get_model('v2')
    assert ai.model_manager.get_model_settings.call_count == 2
    assert ai.model_manager.get_model_settings.call_args == call('chat', 'v2')
    assert mock_init.call_count == 2


def test_wrappers(mocker):
    # Just verify each wrapper calls _get_model with correct task
    mock_get = mocker.patch('src.ai._get_model', return_value=MagicMock())

    ai.get_model('v1')
    assert mock_get.call_args == call('chat', 'v1')

    ai.get_memory_model('v2')
    assert mock_get.call_args == call('memory', 'v2')

    ai.get_image_descriptor_model('v3')
    assert mock_get.call_args == call('image_describe', 'v3')

    ai.get_animation_descriptor_model('v4')
    assert mock_get.call_args == call('animation_describe', 'v4')
