from pymongo import AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection

from src import settings

__all__ = (
    'messages',
)

db_client = AsyncMongoClient(settings.DATABASE_URL)

messages: AsyncCollection = db_client.data.messages
