from pymongo import AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection

from src import settings

__all__ = (
    'messages',
    'recaps',
    'memory',
    'chats',
    'media_descriptions',
)

db_client = AsyncMongoClient(settings.DATABASE_URL)

messages: AsyncCollection = db_client.data.messages
recaps: AsyncCollection = db_client.data.recaps
memory: AsyncCollection = db_client.data.recaps
chats: AsyncCollection = db_client.data.chats
media_descriptions: AsyncCollection = db_client.data.media_descriptions