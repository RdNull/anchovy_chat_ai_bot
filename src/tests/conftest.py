import pytest

from src import mongo


@pytest.fixture(autouse=True)
async def clean_collections():
    yield
    await mongo.messages.drop()
    await mongo.memory.drop()
    await mongo.chats.drop()
    await mongo.facts.drop()
    await mongo.embedding_tasks.drop()
    await mongo.media_descriptions.drop()
