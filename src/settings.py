import os

APP_NAME = 'shizo_ded_bot'
BOT_PERSISTENCE_FILE = f'data/{APP_NAME}.tg'
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
BOT_NICKNAME = os.environ.get('BOT_NICKNAME', 'ShizoDedAnchovyBot')
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
    'qwen3-vl': {
        'model': 'qwen3-vl:2b',
        'base_url': os.environ.get('AI_API_BASE_URL'),
        'model_provider': 'ollama',
    },
    'llama3.2': {
        'model': 'llama3.2',
        'base_url': os.environ.get('AI_API_BASE_URL'),
        'model_provider': 'ollama',
    }
}
AI_RECAP_MODEL_LOCAL = AI_MODELS_LOCAL[
    os.environ.get('AI_RECAP_MODEL_LOCAL', 'deepseek-r1')
]
AI_IMAGE_DESCRIPTOR_MODEL_LOCAL = AI_MODELS_LOCAL[
    os.environ.get('AI_IMAGE_DESCRIPTOR_MODEL_LOCAL', 'qwen3-vl')
]

AI_MODELS_CLOUD = {
    'glm-4.5': {
        'model_provider': 'openrouter',
        'model': 'z-ai/glm-4.5-air:free',
        'api_key': os.environ.get('OPENROUTER_API_KEY'),
        'max_tokens': 2048,
        'stream': False,
        'reasoning': {
            'effort': 'low'
        }
    },
    'arcee-ai': {
        'model_provider': 'openrouter',
        'model': 'arcee-ai/trinity-large-preview:free',
        'api_key': os.environ.get('OPENROUTER_API_KEY'),
        'max_tokens': 2048,
        'stream': False,
        'reasoning': {
            'effort': 'low'
        }
    },
    'hunter-alpha': {
        'model_provider': 'openrouter',
        'model': 'openrouter/hunter-alpha',
        'api_key': os.environ.get('OPENROUTER_API_KEY'),
        'max_tokens': 2048,
        'stream': False,
    },
    'openrouter-preset': {
        'model_provider': 'openrouter',
        'model': '@preset/shizo-ded-bot-preset',
        'api_key': os.environ.get('OPENROUTER_API_KEY'),
        'max_tokens': 2048,
        'stream': False,
        'reasoning': {
            'effort': 'low'
        }
    }
}
AI_RECAP_MODEL_CLOUD = {
    'model_provider': 'openrouter',
    'model': '@preset/shizo-ded-bot-sumamry-preset',
    'api_key': os.environ.get('OPENROUTER_API_KEY'),
    'max_tokens': 4096,
    'stream': False,
    'reasoning': {
        'effort': 'low'
    }
}
AI_IMAGE_DESCRIPTOR_MODEL_CLOUD = {
    'model_provider': 'openrouter',
    'model': '@preset/shizo-ded-bot-image-descriptor-preset',
    'api_key': os.environ.get('OPENROUTER_API_KEY'),
    'max_tokens': 2048,
    'stream': False,
}

AI_ANIMATION_DESCRIPTOR_MODEL_CLOUD = {
    'model_provider': 'openrouter',
    'model': '@preset/shizo-ded-bot-animation-descriptor-preset',
    'api_key': os.environ.get('OPENROUTER_API_KEY'),
    'max_tokens': 2048,
    'stream': False,
}
AI_ANIMATION_DESCRIPTOR_MODEL_LOCAL = AI_MODELS_LOCAL[
    os.environ.get('AI_ANIMATION_DESCRIPTOR_MODEL_LOCAL', 'qwen3-vl')
]

AI_MODELS = AI_MODELS_LOCAL if IS_LOCAL else AI_MODELS_CLOUD
AI_RECAP_MODEL = AI_RECAP_MODEL_LOCAL if IS_LOCAL else AI_RECAP_MODEL_CLOUD
AI_IMAGE_DESCRIPTOR_MODEL = AI_IMAGE_DESCRIPTOR_MODEL_LOCAL if IS_LOCAL else AI_IMAGE_DESCRIPTOR_MODEL_CLOUD
AI_ANIMATION_DESCRIPTOR_MODEL = AI_ANIMATION_DESCRIPTOR_MODEL_LOCAL if IS_LOCAL else AI_ANIMATION_DESCRIPTOR_MODEL_CLOUD

DEFAULT_AI_MODEL = list(AI_MODELS.keys())[0]

CHARACTERS_DIRECTORY = 'src/characters/repository'
ALLOWED_CHAT_IDS = [str(i) for i in os.environ.get('ALLOWED_CHAT_IDS', '').split(',') if i]
ALLOWED_USER_IDS = [str(i) for i in os.environ.get('ALLOWED_USER_IDS', '').split(',') if i]

RANDOM_REPLY_CHANCE = float(os.environ.get('RANDOM_REPLY_CHANCE', 0.05))
RANDOM_REPLY_COOLDOWN_MINUTES = int(os.environ.get('RANDOM_REPLY_COOLDOWN_MINUTES', 30))

AI_TIMEOUT = int(os.environ.get('AI_TIMEOUT', 60))

MESSAGES_RECAP_MAX_SIZE = int(os.environ.get('MESSAGES_RECAP_SIZE', 30))
LAST_MESSAGES_SIZE = int(os.environ.get('LAST_MESSAGES_SIZE', 20))
