from pymongo import AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection

from src import settings

__all__ = (
    'messages',
    'memory',
    'chats',
    'media_descriptions',
    'embedding_tasks',
)

db_client = AsyncMongoClient(settings.DATABASE_URL)
db = db_client[settings.DATABASE_NAME]

messages: AsyncCollection = db.messages
memory: AsyncCollection = db.memory
chats: AsyncCollection = db.chats
media_descriptions: AsyncCollection = db.media_descriptions
embedding_tasks: AsyncCollection = db.embedding_tasks
facts: AsyncCollection = db.facts
