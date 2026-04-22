from bson.codec_options import CodecOptions, TypeRegistry
from bson.decimal128 import DecimalDecoder
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

_codec_options = CodecOptions(type_registry=TypeRegistry([DecimalDecoder()]))

db_client = AsyncMongoClient(settings.DATABASE_URL)
db = db_client.get_database(settings.DATABASE_NAME, codec_options=_codec_options)

messages: AsyncCollection = db.messages
memory: AsyncCollection = db.memory
chats: AsyncCollection = db.chats
media_descriptions: AsyncCollection = db.media_descriptions
embedding_tasks: AsyncCollection = db.embedding_tasks
facts: AsyncCollection = db.facts
