import os
from collections import defaultdict

import yaml
from jinja2 import Template
from src import settings


class PromptManager:
    def __init__(self, prompts_dir: str = settings.PROMPTS_DIR):
        self.prompts_dir = prompts_dir
        self._prompts = defaultdict(dict)
        self.load_prompts()

    def load_prompts(self):
        for root, _, files in os.walk(self.prompts_dir):
            for filename in files:
                if filename.endswith('.yaml') or filename.endswith('.yml'):
                    path = os.path.join(root, filename)
                    with open(path, 'r', encoding='utf-8') as f:
                        data = yaml.safe_load(f)
                        task, version = data.get('task'), data.get('version')
                        template_str = data.get('template')
                        if not (task and version and template_str):
                            continue

                        self._prompts[task][version] = template_str

    def get_prompt(self, task: str, version: str = 'v1', **kwargs) -> str:
        if task not in self._prompts:
            raise ValueError(f"Task '{task}' not found in prompts.")

        if version not in self._prompts[task]:
            raise ValueError(f"Version '{version}' not found for task '{task}'.")

        template_str = self._prompts[task][version]
        template = Template(template_str)
        return template.render(**kwargs)


prompt_manager = PromptManager()
