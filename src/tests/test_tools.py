import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from src.characters.tools import get_user_facts, search_messages, search_web
from src.characters.tools.answer import answer_text, set_reaction
from src.characters.tools.context import _web_search_limiter
from src.characters.tools.context import search_messages as search_messages_direct
from src.models import Message, RelatedMessagesData, UserFact, UserRole
from src.tools import ToolContext, ToolRegistry


def make_context(chat_id=123):
    return ToolContext(chat_id=chat_id, replier=MagicMock())


@pytest.fixture(autouse=True)
def reset_web_search_limiter():
    # The limiter is a module-level singleton, so its window leaks between tests.
    _web_search_limiter._call_times.clear()
    yield
    _web_search_limiter._call_times.clear()


def mock_web_search_model(mocker, content='обрывок', chat_id=123):
    search_web.metadata = {'context': make_context(chat_id)}
    model = MagicMock()
    if isinstance(content, Exception):
        model.ainvoke = AsyncMock(side_effect=content)
    else:
        model.ainvoke = AsyncMock(return_value=AIMessage(content=content))
    mocker.patch(
        'src.characters.tools.context.ai.get_web_search_model', return_value=model
    )
    return model


async def test_search_messages_tool(mocker):
    context = make_context()
    search_messages.metadata = {'context': context}

    msg = Message(
        chat_id=123,
        nickname='bob',
        role=UserRole.USER,
        text='hello world',
        created_at=datetime.now()
    )

    related = [RelatedMessagesData(messages=[msg], score=0.9)]
    mock_search = mocker.patch(
        'src.characters.tools.context.messages_embeddings_client.search',
        AsyncMock(return_value=related)
    )

    result = await search_messages.ainvoke({'search_query': 'test query', 'limit': 2})

    assert len(result) == 1
    assert result[0]['score'] == 0.9
    assert 'bob: hello world' in result[0]['messages']
    assert mock_search.call_count == 1
    assert mock_search.call_args == call(123, 'test query', limit=2)


async def test_search_messages_tool_limit_validation(mocker):
    context = make_context()
    search_messages.metadata = {'context': context}

    mock_search = mocker.patch(
        'src.characters.tools.context.messages_embeddings_client.search',
        AsyncMock(return_value=[])
    )

    await search_messages.ainvoke({'search_query': 'test', 'limit': 10})
    assert mock_search.call_args == call(123, 'test', limit=3)

    await search_messages.ainvoke({'search_query': 'test', 'limit': -1})
    assert mock_search.call_args == call(123, 'test', limit=3)


async def test_get_user_facts_tool(mocker):
    facts = [
        UserFact(nickname='bob', text='likes pizza', confidence=0.9),
        UserFact(nickname='bob', text='is tall', confidence=0.7)
    ]
    mock_get = mocker.patch(
        'src.characters.tools.context.get_facts',
        AsyncMock(return_value=facts)
    )

    result = await get_user_facts.ainvoke({'nickname': '@bob', 'limit': 10})

    assert result == [
        {'text': 'likes pizza', 'confidence': 0.9},
        {'text': 'is tall', 'confidence': 0.7}
    ]
    assert mock_get.call_count == 1
    assert mock_get.call_args == call('bob', limit=10)


async def test_get_user_facts_tool_limit_validation(mocker):
    mock_get = mocker.patch('src.characters.tools.context.get_facts', AsyncMock(return_value=[]))

    await get_user_facts.ainvoke({'nickname': 'bob', 'limit': 25})
    assert mock_get.call_args == call('bob', limit=5)

    await get_user_facts.ainvoke({'nickname': 'bob', 'limit': -1})
    assert mock_get.call_args == call('bob', limit=5)


async def test_tool_registry_execute_success(mocker):
    mock_tool = MagicMock()
    mock_tool.name = 'test_tool'
    mock_tool.ainvoke = AsyncMock(return_value='tool result')

    context = make_context()
    registry = ToolRegistry(context_tools=[mock_tool], direct_tools=[], context=context)

    tool_call = {
        'name': 'test_tool',
        'args': {'arg1': 'val1'},
        'id': 'call_123'
    }

    result = await registry.execute(tool_call)

    assert isinstance(result, ToolMessage)
    assert result.content == 'tool result'
    assert result.tool_call_id == 'call_123'
    assert mock_tool.metadata == {'context': context}
    assert mock_tool.ainvoke.call_count == 1
    assert mock_tool.ainvoke.call_args == call({'arg1': 'val1'})


async def test_tool_registry_execute_unknown_tool():
    context = make_context()
    registry = ToolRegistry(context_tools=[], direct_tools=[], context=context)

    tool_call = {
        'name': 'unknown_tool',
        'args': {},
        'id': 'call_456'
    }

    with pytest.raises(ValueError, match='Unknown tool: unknown_tool'):
        await registry.execute(tool_call)


async def test_tool_registry_execute_logging(mocker):
    mock_tool = MagicMock()
    mock_tool.name = 'test_tool'
    mock_tool.ainvoke = AsyncMock(return_value='res')

    mock_logger = mocker.patch('src.tools.logger')

    context = make_context()
    registry = ToolRegistry(context_tools=[mock_tool], direct_tools=[], context=context)

    tool_call = {
        'name': 'test_tool',
        'args': {'p': 1},
        'id': 'id1'
    }

    await registry.execute(tool_call)

    assert mock_logger.info.call_count == 1
    assert "Executing tool: test_tool with arguments: {'p': 1}" in mock_logger.info.call_args[0][0]


async def test_tool_registry_is_return_direct_for_direct_tool():
    context = make_context()
    registry = ToolRegistry(context_tools=[], direct_tools=[answer_text], context=context)

    tool_call = {'name': 'answer_text', 'args': {'text': 'hi'}, 'id': 'tc1'}
    assert registry.is_return_direct(tool_call) is True


async def test_tool_registry_is_return_direct_for_context_tool():
    context = make_context()
    registry = ToolRegistry(context_tools=[search_messages_direct], direct_tools=[],
                            context=context)

    tool_call = {'name': 'search_messages', 'args': {'search_query': 'x', 'limit': 1}, 'id': 'tc2'}
    assert registry.is_return_direct(tool_call) is False


async def test_answer_text_tool_calls_replier():
    mock_replier = MagicMock()
    mock_replier.reply_message = AsyncMock()
    context = ToolContext(chat_id=1, replier=mock_replier)
    answer_text.metadata = {'context': context}

    await answer_text.ainvoke({'text': 'hello'})

    assert mock_replier.reply_message.call_count == 1
    assert mock_replier.reply_message.call_args[0][0] == 'hello'


async def test_answer_text_tool_skips_empty_text():
    mock_replier = MagicMock()
    mock_replier.reply_message = AsyncMock()
    context = ToolContext(chat_id=1, replier=mock_replier)
    answer_text.metadata = {'context': context}

    await answer_text.ainvoke({'text': ''})

    assert mock_replier.reply_message.call_count == 0


async def test_set_reaction_tool_calls_replier():
    mock_replier = MagicMock()
    mock_replier.reply_reaction = AsyncMock()
    context = ToolContext(chat_id=1, replier=mock_replier)
    set_reaction.metadata = {'context': context}

    await set_reaction.ainvoke({'emoji': '🤡'})

    assert mock_replier.reply_reaction.call_count == 1
    assert mock_replier.reply_reaction.call_args[0][0] == '🤡'
    assert mock_replier.reply_reaction.call_args[1]['is_big'] is True


async def test_search_web_returns_fragments(mocker):
    model = mock_web_search_model(mocker, 'цена ~120к тенге\nвышел 14 марта\nвыиграл Аякс 3:1')

    result = await search_web.ainvoke({'query': 'сколько стоит', 'limit': 3})

    assert result == ['цена ~120к тенге', 'вышел 14 марта', 'выиграл Аякс 3:1']
    assert model.ainvoke.call_count == 1


async def test_search_web_limit_out_of_range_defaults_to_two(mocker):
    model = mock_web_search_model(mocker, 'один\nдва\nтри')

    for bad_limit in (0, 4, -1):
        result = await search_web.ainvoke({'query': 'что-то', 'limit': bad_limit})
        assert result == ['один', 'два']

    assert model.ainvoke.call_count == 3


async def test_search_web_limit_in_range_passes_through(mocker):
    mock_web_search_model(mocker, 'один\nдва\nтри')

    assert await search_web.ainvoke({'query': 'x', 'limit': 1}) == ['один']
    assert await search_web.ainvoke({'query': 'x', 'limit': 2}) == ['один', 'два']
    assert await search_web.ainvoke({'query': 'x', 'limit': 3}) == ['один', 'два', 'три']


async def test_search_web_truncates_to_limit(mocker):
    mock_web_search_model(mocker, 'первый\nвторой\nтретий')

    result = await search_web.ainvoke({'query': 'x', 'limit': 1})

    assert result == ['первый']


async def test_search_web_strips_urls(mocker):
    mock_web_search_model(
        mocker,
        'цена 120к тенге https://kaspi.kz/shop/item\nвышел 14 марта, example.com',
    )

    result = await search_web.ainvoke({'query': 'x', 'limit': 2})

    assert result == ['цена 120к тенге', 'вышел 14 марта']


async def test_search_web_strips_bullets(mocker):
    mock_web_search_model(mocker, '- цена 120к\n* вышел 14 марта\n• выиграл 3:1')

    result = await search_web.ainvoke({'query': 'x', 'limit': 3})

    assert result == ['цена 120к', 'вышел 14 марта', 'выиграл 3:1']


async def test_search_web_model_not_found_is_not_doubled(mocker):
    mock_web_search_model(mocker, 'не нашлось')

    result = await search_web.ainvoke({'query': 'x', 'limit': 2})

    assert result == ['не нашлось']


async def test_search_web_empty_response(mocker):
    mock_web_search_model(mocker, '   \n\n  ')

    result = await search_web.ainvoke({'query': 'x', 'limit': 2})

    assert result == ['не нашлось']


async def test_search_web_normalises_list_content(mocker):
    mock_web_search_model(
        mocker,
        [{'type': 'text', 'text': 'цена 120к'}, {'type': 'text', 'text': 'вышел 14 марта'}],
    )

    result = await search_web.ainvoke({'query': 'x', 'limit': 2})

    assert result == ['цена 120к', 'вышел 14 марта']


async def test_search_web_timeout_returns_not_found(mocker):
    model = mock_web_search_model(mocker, asyncio.TimeoutError())

    result = await search_web.ainvoke({'query': 'x', 'limit': 2})

    assert result == ['не нашлось']
    assert model.ainvoke.call_count == 1


async def test_search_web_error_returns_not_found(mocker):
    model = mock_web_search_model(mocker, RuntimeError('openrouter exploded'))
    mock_logger = mocker.patch('src.characters.tools.context.logger')

    result = await search_web.ainvoke({'query': 'x', 'limit': 2})

    assert result == ['не нашлось']
    assert model.ainvoke.call_count == 1
    assert mock_logger.error.call_count == 1
    assert 'openrouter exploded' in mock_logger.error.call_args[0][0]


async def test_search_web_rate_limited_skips_the_model(mocker):
    model = mock_web_search_model(mocker, 'обрывок')
    limit = _web_search_limiter.rate_limit

    for _ in range(limit):
        assert await search_web.ainvoke({'query': 'x', 'limit': 1}) == ['обрывок']

    result = await search_web.ainvoke({'query': 'x', 'limit': 1})

    assert result == ['не нашлось']
    assert model.ainvoke.call_count == limit


async def test_search_web_rate_limit_is_per_chat(mocker):
    model = mock_web_search_model(mocker, 'обрывок', chat_id=1)
    limit = _web_search_limiter.rate_limit

    for _ in range(limit + 1):
        await search_web.ainvoke({'query': 'x', 'limit': 1})

    search_web.metadata = {'context': make_context(chat_id=2)}
    result = await search_web.ainvoke({'query': 'x', 'limit': 1})

    assert result == ['обрывок']
    assert model.ainvoke.call_count == limit + 1


async def test_search_web_failed_search_consumes_its_slot(mocker):
    # is_exceeded debits on check, so a search that fails still spent the budget.
    model = mock_web_search_model(mocker, 'обрывок')
    model.ainvoke.side_effect = [RuntimeError('boom'), AIMessage(content='обрывок')]
    limit = _web_search_limiter.rate_limit

    assert await search_web.ainvoke({'query': 'x', 'limit': 1}) == ['не нашлось']
    assert await search_web.ainvoke({'query': 'x', 'limit': 1}) == ['обрывок']

    model.ainvoke.side_effect = None
    model.ainvoke.return_value = AIMessage(content='обрывок')
    for _ in range(limit - 2):
        await search_web.ainvoke({'query': 'x', 'limit': 1})

    result = await search_web.ainvoke({'query': 'x', 'limit': 1})

    assert result == ['не нашлось']
    assert model.ainvoke.call_count == limit


async def test_search_web_logs_the_house_format(mocker):
    mock_web_search_model(mocker, 'цена 120к\nвышел 14 марта')
    mock_logger = mocker.patch('src.characters.tools.context.logger')

    await search_web.ainvoke({'query': 'почем айфон', 'limit': 2})

    logged = mock_logger.info.call_args[0][0]
    assert 'TOOL_WEB_SEARCH chat_id=123 query=почем айфон results=2 outcome=ok' in logged
    assert 'elapsed_ms=' in logged


async def test_search_web_strips_markdown_link_citations(mocker):
    # Verbatim from a real extractor response: the model cited sources as markdown
    # links despite the prompt forbidding it, and the bare-domain pattern ate the
    # `](href)` while stranding the opening `[`. The third line has no closing
    # paren, so nothing balances it.
    mock_web_search_model(
        mocker,
        '- 1 BTC = 77.512,47 $ [revolut.com](https://www.revolut.com/ru-LV/crypto/price/btc/usd/?amount-to=1)\n'
        '- Спрос/Предложение: 78.362 / 78.371 $ [investing.com](https://ru.investing.com/crypto/bitcoin/btc-usd)\n'
        '- За последний час: -0,32%, за сутки: -0,58% [revolut.com](https://www.revolut.com/ru-LV/crypto/price/btc/usd/?',
    )

    result = await search_web.ainvoke({'query': 'курс биткоина', 'limit': 3})

    assert result == [
        '1 BTC = 77.512,47 $',
        'Спрос/Предложение: 78.362 / 78.371 $',
        'За последний час: -0,32%, за сутки: -0,58%',
    ]


async def test_search_web_strips_citation_refs(mocker):
    mock_web_search_model(mocker, 'цена ~120к тенге [1]\nвышел 14 марта [источник]')

    result = await search_web.ainvoke({'query': 'x', 'limit': 2})

    assert result == ['цена ~120к тенге', 'вышел 14 марта']


async def test_search_web_keeps_leading_minus_sign(mocker):
    # The bullet stripper must not read a negative sign as a list marker.
    mock_web_search_model(mocker, '-0,58% за сутки\n- -1,2% за неделю')

    result = await search_web.ainvoke({'query': 'x', 'limit': 2})

    assert result == ['-0,58% за сутки', '-1,2% за неделю']
