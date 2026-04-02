import os

APP_NAME = 'shizo_ded_bot'
BOT_PERSISTENCE_FILE = f'data/{APP_NAME}.tg'
CHARACTERS_DIRECTORY = 'src/characters/repository'
PROMPTS_DIR = 'src/prompts'

TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
BOT_NICKNAME = os.environ.get('BOT_NICKNAME', 'ShizoDedAnchovyBot')
DATABASE_URL = os.environ['DATABASE_URL']

IS_LOCAL = os.environ.get('IS_LOCAL', 'false').lower() == 'true'

ALLOWED_CHAT_IDS = [str(i) for i in os.environ.get('ALLOWED_CHAT_IDS', '').split(',') if i]
ALLOWED_USER_IDS = [str(i) for i in os.environ.get('ALLOWED_USER_IDS', '').split(',') if i]

RANDOM_REPLY_CHANCE = float(os.environ.get('RANDOM_REPLY_CHANCE', 0.05))
RANDOM_REPLY_COOLDOWN_MINUTES = int(os.environ.get('RANDOM_REPLY_COOLDOWN_MINUTES', 30))

AI_TIMEOUT = int(os.environ.get('AI_TIMEOUT', 60))

MESSAGES_RECAP_MAX_SIZE = int(os.environ.get('MESSAGES_RECAP_SIZE', 30))
LAST_MESSAGES_SIZE = int(os.environ.get('LAST_MESSAGES_SIZE', 20))
