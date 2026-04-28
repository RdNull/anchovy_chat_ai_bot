import os

APP_NAME = 'anchovy_chat_ai_bot'
BOT_PERSISTENCE_FILE = f'data/{APP_NAME}.tg'
CHARACTERS_DIRECTORY = 'src/characters/repository'
PROMPTS_DIR = 'src/prompts'

TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
BOT_NICKNAME = os.environ.get('BOT_NICKNAME', 'AnchovyAiBot')
DATABASE_URL = os.environ['DATABASE_URL']
DATABASE_NAME = os.environ.get('DATABASE_NAME', 'data')

IS_LOCAL = os.environ.get('IS_LOCAL', 'false').lower() == 'true'

ALLOWED_CHAT_IDS = [str(i) for i in os.environ.get('ALLOWED_CHAT_IDS', '').split(',') if i]
ALLOWED_USER_IDS = [str(i) for i in os.environ.get('ALLOWED_USER_IDS', '').split(',') if i]

RANDOM_REPLY_CHANCE = float(os.environ.get('RANDOM_REPLY_CHANCE', 0.05))
RANDOM_REPLY_COOLDOWN_MINUTES = int(os.environ.get('RANDOM_REPLY_COOLDOWN_MINUTES', 30))

AI_TIMEOUT = int(os.environ.get('AI_TIMEOUT', 90))
CHAT_RATE_LIMIT = int(os.environ.get('CHAT_RATE_LIMIT', 5))  # calls per minute per chat

EMBEDDINGS_SEARCH_MAX_SIZE = int(os.environ.get('EMBEDDINGS_SEARCH_MAX_SIZE', 3))
MESSAGES_EMBEDDINGS_MAX_SIZE = int(os.environ.get('MESSAGES_EMBEDDINGS_MAX_SIZE', 40))

MESSAGES_MEMORY_MAX_SIZE = int(os.environ.get('MESSAGES_MEMORY_MAX_SIZE', 40))
LAST_MESSAGES_SIZE = int(os.environ.get('LAST_MESSAGES_SIZE', 40))

OPENROUTER_API_URL = os.environ.get('OPENROUTER_API_URL', 'https://openrouter.ai/api/v1')
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY')
QDRANT_URL = os.environ.get('QDRANT_URL', 'http://qdrant:6333')

EMBEDDINGS_MODEL_SETTINGS = {
    'model_name': os.environ.get('EMBEDDINGS_MODEL_NAME', 'text-embedding-3-small'),
    'vector_size': int(os.environ.get('EMBEDDINGS_VECTOR_SIZE', 1536)),
}
