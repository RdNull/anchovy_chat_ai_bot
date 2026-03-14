import os

APP_NAME = 'shizo_ded_bot'
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
BOT_NICKNAME = 'rdnull_test_bot'
DATABASE_URL = os.environ['DATABASE_URL']

AI_INIT_PARAMS = {
    'model': 'deepseek-r1',
    # 'model': 'llama3.2',
    'base_url': os.environ['AI_API_BASE_URL'],
    'think': False,
    'model_provider': 'ollama'
}

CHARACTERS_DIRECTORY = 'src/characters/repository'