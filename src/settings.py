import os

APP_NAME = 'shizo_ded_bot'
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
BOT_NICKNAME = 'ShizoDedAnchovyBot'
DATABASE_URL = os.environ['DATABASE_URL']

IS_LOCAL = os.environ.get('IS_LOCAL', 'false').lower() == 'true'

AI_LOCAL_INIT_PARAMS = {
    'model': 'deepseek-r1',
    'base_url': os.environ['AI_API_BASE_URL'],
    'model_provider': 'ollama',
}

AI_CLOUD_INIT_PARAMS = {
    'model_provider': 'openrouter',
    'model': os.environ.get('OPENROUTER_MODEL', 'z-ai/glm-4.5-air:free'),
    'api_key': os.environ.get('OPENROUTER_API_KEY'),
    'max_tokens': 1024,
    'stream': False,
    'reasoning': {
        'enabled': False
    }
}

CHARACTERS_DIRECTORY = 'src/characters/repository'
ALLOWED_CHAT_IDS = [str(i) for i in os.environ.get('ALLOWED_CHAT_IDS', '').split(',') if i]
ALLOWED_USER_IDS = [str(i) for i in os.environ.get('ALLOWED_USER_IDS', '').split(',') if i]

RANDOM_REPLY_CHANCE = float(os.environ.get('RANDOM_REPLY_CHANCE', 0.5))
RANDOM_REPLY_COOLDOWN_MINUTES = int(os.environ.get('RANDOM_REPLY_COOLDOWN_MINUTES', 1))