from bson.codec_options import CodecOptions, TypeRegistry
from bson.decimal128 import DecimalDecoder
from pymongo import AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection

from src import settings

__all__ = (
    'chat_settings',
    'chats',
    'embedding_tasks',
    'ensure_indexes',
    'facts',
    'initiative_runs',
    'media_descriptions',
    'memory',
    'messages',
)

_codec_options = CodecOptions(type_registry=TypeRegistry([DecimalDecoder()]))

db_client = AsyncMongoClient(settings.DATABASE_URL)
db = db_client.get_database(settings.DATABASE_NAME, codec_options=_codec_options)

messages: AsyncCollection = db.messages
memory: AsyncCollection = db.memory
chats: AsyncCollection = db.chats
media_descriptions: AsyncCollection = db.media_descriptions
embedding_tasks: AsyncCollection = db.embedding_tasks
initiative_runs: AsyncCollection = db.initiative_runs
facts: AsyncCollection = db.facts
chat_settings: AsyncCollection = db.chat_settings


async def ensure_indexes() -> None:
    """Every index the bot relies on. `create_index` is a no-op when the index already exists."""
    await facts.create_index([('nickname', 1), ('status', 1)])
