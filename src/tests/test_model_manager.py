import json
import os
from unittest.mock import patch

import pytest

from src import settings
from src.model_manager import ModelManager


def test_resolve_env_vars():
    manager = ModelManager()
    config = {
        'api_key': 'env:TEST_API_KEY',
        'nested': {
            'value': 'env:TEST_NESTED_VAR'
        },
        'list': ['env:TEST_LIST_VAR', 'plain_string'],
        'plain': 'value'
    }

    test_env = {
        'TEST_API_KEY': 'secret',
        'TEST_NESTED_VAR': 'nested_secret',
        'TEST_LIST_VAR': 'list_secret',
    }
    with patch.dict(os.environ, test_env):
        resolved = manager._resolve_env_vars(config)

    assert resolved == {
        'api_key': 'secret',
        'nested': {
            'value': 'nested_secret'
        },
        'list': ['list_secret', 'plain_string'],
        'plain': 'value'
    }


def test_get_model_settings_local(tmp_path):
    # Setup temporary models directory
    models_dir = tmp_path / 'models'
    task_dir = models_dir / 'local' / 'test_task'
    task_dir.mkdir(parents=True)

    config = {'model': 'local-model', 'temperature': 0}
    (task_dir / 'v1.json').write_text(json.dumps(config))

    manager = ModelManager(models_dir=str(models_dir))

    with patch.object(settings, 'IS_LOCAL', True):
        settings_result = manager.get_model_settings('test_task', 'v1')

    assert settings_result == config


def test_get_model_settings_cloud(tmp_path):
    # Setup temporary models directory
    models_dir = tmp_path / 'models'
    task_dir = models_dir / 'cloud' / 'test_task'
    task_dir.mkdir(parents=True)

    config = {'model': 'cloud-model', 'api_key': 'env:CLOUD_KEY'}
    (task_dir / 'v1.json').write_text(json.dumps(config))

    manager = ModelManager(models_dir=str(models_dir))

    with patch.object(settings, 'IS_LOCAL', False):
        with patch.dict(os.environ, {'CLOUD_KEY': 'secret_key'}):
            settings_result = manager.get_model_settings('test_task', 'v1')

    assert settings_result == {'model': 'cloud-model', 'api_key': 'secret_key'}


def test_get_model_settings_fallback(tmp_path):
    models_dir = tmp_path / 'models'
    task_dir = models_dir / 'cloud' / 'test_task'
    task_dir.mkdir(parents=True)

    config_v1 = {'model': 'v1-model'}
    (task_dir / 'v1.json').write_text(json.dumps(config_v1))

    manager = ModelManager(models_dir=str(models_dir))

    with patch.object(settings, 'IS_LOCAL', False):
        # Request v2, should fallback to v1
        settings_result = manager.get_model_settings('test_task', 'v2')

    assert settings_result == config_v1


def test_get_model_settings_not_found(tmp_path):
    models_dir = tmp_path / 'models'
    (models_dir / 'cloud').mkdir(parents=True)

    manager = ModelManager(models_dir=str(models_dir))

    with patch.object(settings, 'IS_LOCAL', False):
        with pytest.raises(ValueError) as excinfo:
            manager.get_model_settings('non_existent_task', 'v1')

    assert 'No model settings found' in str(excinfo.value)
    assert 'non_existent_task' in str(excinfo.value)
