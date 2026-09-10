from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage

from src import mongo


def pytest_sessionstart(session):
    """Refuses to run against anything but a test database.

    `clean_collections` drops six collections after every test, unconditionally, and
    the only thing pointing it at `test_data` is `[pytest_env]` in `pytest.toml`. A run
    that loses that — another config file, the env plugin disabled — would bind
    `src.mongo` to the real database and empty it one test at a time.
    """
    if not mongo.db.name.startswith('test'):
        pytest.exit(f'refusing to run: tests would drop collections in {mongo.db.name!r}', returncode=2)


@pytest.fixture(autouse=True)
async def clean_collections():
    yield
    await mongo.messages.drop()
    await mongo.memory.drop()
    await mongo.facts.drop()
    await mongo.embedding_tasks.drop()
    await mongo.media_descriptions.drop()
    await mongo.chat_settings.drop()


@pytest.fixture
def make_update():
    def _factory(
        message_id=1,
        text='hello',
        updated_text=None,
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
        update.message.message_id = message_id
        update.message.text = text
        update.message.caption = None
        update.message.reply_text = AsyncMock(return_value=MagicMock(message_id=999))
        update.message.reply_sticker = AsyncMock(return_value=MagicMock(message_id=998))
        update.message.set_reaction = AsyncMock(return_value=True)
        update.message.from_user.username = username
        update.message.from_user.first_name = username
        update.message.reply_to_message = reply_to_message
        update.message.photo = photo
        update.message.sticker = sticker
        update.message.animation = animation
        update.effective_message.reply_text = AsyncMock()

        if updated_text:
            update.edited_message.chat_id = chat_id
            update.edited_message.message_id = message_id
            update.edited_message.text = updated_text
        else:
            update.edited_message = None

        return update

    return _factory


@pytest.fixture
def make_context():
    ctx = MagicMock()
    ctx.bot.send_chat_action = AsyncMock()
    return ctx


@pytest.fixture
def mock_llm(mocker):
    llm = MagicMock()
    llm.bind_tools.return_value = llm
    llm.ainvoke = AsyncMock(return_value=AIMessage(
        content='',
        tool_calls=[{'id': 'mock_tc1', 'name': 'answer_text', 'args': {'text': 'мок ответ'}, 'type': 'tool_call'}]
    ))
    mocker.patch('src.characters.character.ai.get_model', return_value=llm)
    return llm

@pytest.fixture(autouse=True)
def mock_langsmith(mocker):
    run_tree = MagicMock()
    run_tree.tags = []
    mocker.patch('langsmith.get_current_run_tree', return_value=run_tree)
    return run_tree