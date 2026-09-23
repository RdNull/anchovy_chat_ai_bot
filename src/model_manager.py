import json
import os
from typing import Any


class ModelManager:
    def __init__(self, models_dir: str = 'src/models'):
        self.models_dir = models_dir

    def get_model_settings(self, task: str, version: str = 'v1') -> dict[str, Any]:
        file_path = os.path.join(self.models_dir, task, f'{version}.json')

        if not os.path.exists(file_path):
            # Fallback to 'v1.json' if specific version file not found
            fallback_path = os.path.join(self.models_dir, task, 'v1.json')
            if os.path.exists(fallback_path):
                file_path = fallback_path
            else:
                raise ValueError(
                    f'No model settings found for task: {task}, version: {version} (looked in {file_path} and {fallback_path})'
                )

        with open(file_path) as f:
            model_config = json.load(f)

        # Resolve environment variables
        return self._resolve_env_vars(model_config)

    def _resolve_env_vars(self, config: Any) -> Any:
        if isinstance(config, dict):
            return {k: self._resolve_env_vars(v) for k, v in config.items()}
        if isinstance(config, list):
            return [self._resolve_env_vars(v) for v in config]
        if isinstance(config, str) and config.startswith('env:'):
            env_var = config[4:]
            return os.environ.get(env_var)
        return config


model_manager = ModelManager()
