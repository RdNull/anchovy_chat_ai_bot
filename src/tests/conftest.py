from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage

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


@pytest.fixture
def make_update():
    def _factory(
        text='hello',
        user_id=111,
        chat_id=222,
        username='testuser',
        reply_to_message=None,
        photo=None,
        sticker=None,
        animation=None,
    ):
        update = MagicMock()
        update.effective_user.id = user_id
        update.effective_chat.id = chat_id
        update.message.text = text
        update.message.caption = None
        update.message.reply_text = AsyncMock()
        update.message.from_user.username = username
        update.message.from_user.first_name = username
        update.message.reply_to_message = reply_to_message
        update.message.photo = photo
        update.message.sticker = sticker
        update.message.animation = animation
        update.effective_message.reply_text = AsyncMock()
        return update

    return _factory


@pytest.fixture
def make_context():
    ctx = MagicMock()
    ctx.chat_data = {}
    ctx.bot.send_chat_action = AsyncMock()
    return ctx


@pytest.fixture
def mock_llm(mocker):
    llm = MagicMock()
    llm.bind_tools.return_value = llm
    llm.ainvoke = AsyncMock(return_value=AIMessage(content='мок ответ'))
    mocker.patch('src.characters.character.ai.get_model', return_value=llm)
    return llm
