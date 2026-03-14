import os

APP_NAME = 'shizo_ded_bot'
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
BOT_NICKNAME = 'rdnull_test_bot'
DATABASE_URL = os.environ['DATABASE_URL']

AI_INIT_PARAMS = {
    # 'model': 'qwen3.5:9b',
    'model': 'deepseek-r1',
    # 'model': 'llama3.2',
    'base_url': os.environ['AI_API_BASE_URL'],
    'model_provider': 'ollama',
}

CHARACTERS_DIRECTORY = 'src/characters/repository'
ALLOWED_CHAT_IDS = [str(i) for i in os.environ.get('ALLOWED_CHAT_IDS', '').split(',') if i]
ALLOWED_USER_IDS = [str(i) for i in os.environ.get('ALLOWED_USER_IDS', '').split(',') if i]

RANDOM_REPLY_CHANCE = float(os.environ.get('RANDOM_REPLY_CHANCE', 0.05))
RANDOM_REPLY_COOLDOWN_MINUTES = int(os.environ.get('RANDOM_REPLY_COOLDOWN_MINUTES', 5))