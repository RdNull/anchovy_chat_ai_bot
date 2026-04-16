import pymongo

from src.mongo import db_client


async def test_connection():
    with pymongo.timeout(1):
        assert await db_client.list_databases()
