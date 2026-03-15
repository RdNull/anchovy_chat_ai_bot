import os

APP_NAME = 'shizo_ded_bot'
BOT_PERSISTENCE_FILE = f'data/{APP_NAME}.tg'
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
BOT_NICKNAME = 'ShizoDedAnchovyBot'
DATABASE_URL = os.environ['DATABASE_URL']

IS_LOCAL = os.environ.get('IS_LOCAL', 'false').lower() == 'true'

AI_MODELS_LOCAL = {
    'deepseek-r1': {
        'model': 'deepseek-r1',
        'base_url': os.environ.get('AI_API_BASE_URL'),
        'model_provider': 'ollama',
    },
    'qwen3.5': {
        'model': 'qwen3.5:9b',
        'base_url': os.environ.get('AI_API_BASE_URL'),
        'model_provider': 'ollama',
    },
    'llama3.2': {
        'model': 'llama3.2',
        'base_url': os.environ.get('AI_API_BASE_URL'),
        'model_provider': 'ollama',
    }
}

AI_MODELS_CLOUD = {
    'glm-4.5': {
        'model_provider': 'openrouter',
        'model': 'z-ai/glm-4.5-air:free',
        'api_key': os.environ.get('OPENROUTER_API_KEY'),
        'max_tokens': 2048,
        'stream': False,
        'reasoning': 'none',
    },
    'arcee-ai': {
        'model_provider': 'openrouter',
        'model': 'arcee-ai/trinity-large-preview:free',
        'api_key': os.environ.get('OPENROUTER_API_KEY'),
        'max_tokens': 2048,
        'stream': False,
        'reasoning': 'none',
    },
    'hunter-alpha': {
        'model_provider': 'openrouter',
        'model': 'openrouter/hunter-alpha',
        'api_key': os.environ.get('OPENROUTER_API_KEY'),
        'max_tokens': 2048,
        'stream': False,
    },
}

AI_MODELS = AI_MODELS_LOCAL if IS_LOCAL else AI_MODELS_CLOUD
DEFAULT_AI_MODEL = list(AI_MODELS.keys())[0]

CHARACTERS_DIRECTORY = 'src/characters/repository'
ALLOWED_CHAT_IDS = [str(i) for i in os.environ.get('ALLOWED_CHAT_IDS', '').split(',') if i]
ALLOWED_USER_IDS = [str(i) for i in os.environ.get('ALLOWED_USER_IDS', '').split(',') if i]

RANDOM_REPLY_CHANCE = float(os.environ.get('RANDOM_REPLY_CHANCE', 0.05))
RANDOM_REPLY_COOLDOWN_MINUTES = int(os.environ.get('RANDOM_REPLY_COOLDOWN_MINUTES', 30))

AI_TIMEOUT = int(os.environ.get('AI_TIMEOUT', 60))

MESSAGES_RECAP_SIZE = 30
LAST_MESSAGES_SIZE = 20
