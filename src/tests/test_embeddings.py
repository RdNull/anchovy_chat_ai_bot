from datetime import datetime, timezone
from unittest.mock import ANY, AsyncMock, MagicMock, call
from uuid import UUID

from qdrant_client.http.models import QueryResponse, ScoredPoint

from src import settings
from src.embeddings.facts import FactsEmbeddingClient, FactsSearchResult
from src.embeddings.messages import MessageEmbeddingsClient, chunk_messages
from src.embeddings.stickers import (
    StickerEmbeddingsClient, StickerSearchResult, _point_id, sticker_embedding_text,
)
from src.models import (
    MediaDescription, Message, MessageMedia, MessageMediaStatus, MessageMediaTypes,
    RelatedMessagesData, UserFact, UserRole,
)
from src.processors.context.embeddings import search_related_messages, update_chat_embeddings


def make_description(
    unique_id='sticker_uid',
    description='кот танцует',
    ocr_text=None,
    emoji='🔥',
    status=MessageMediaStatus.READY,
):
    return MediaDescription(
        media_id=unique_id,
        description=description,
        ocr_text=ocr_text,
        type=MessageMediaTypes.STICKER,
        status=status,
        sticker_emoji=emoji,
    )


def make_sticker_client(mocker, qdrant):
    mocker.patch('src.embeddings.client.AsyncQdrantClient', return_value=qdrant)
    client = StickerEmbeddingsClient('stickers', 'test_model', 128)
    client._get_embedding_vectors = AsyncMock(return_value=[0.1] * 128)
    return client


def create_mock_message(mid, text, chat_id=123, nickname='user'):
    return Message(
        _id=str(mid),
        chat_id=chat_id,
        role=UserRole.USER,
        text=text,
        nickname=nickname,
        created_at=datetime.now(timezone.utc)
    )


def test_chunk_messages():
    messages = [create_mock_message(i, f'text {i}') for i in range(10)]

    # window=8, overlap=3
    # Chunk 1: [0, 1, 2, 3, 4, 5, 6, 7]
    # Next start: 8 - 3 = 5
    # Chunk 2: [5, 6, 7, 8, 9]
    chunks = chunk_messages(messages, window=8, overlap=3)
    assert len(chunks) == 2
    assert isinstance(chunks[0].chunk_id, UUID)
    assert len(chunks[0].metadata['message_ids']) == 8
    assert len(chunks[1].metadata['message_ids']) == 5
    assert 'text 7' in chunks[0].payload
    assert chunks[1].payload.startswith('') and 'text 5' in chunks[1].payload

    # Test small overlap handling
    # window=4, overlap=2
    # Chunk 1: [0, 1, 2, 3]
    # Next start: 4 - 2 = 2
    # Chunk 2: [2, 3, 4, 5]
    # Next start: 6 - 2 = 4
    # Chunk 3: [4, 5, 6, 7]
    # Next start: 8 - 2 = 6
    # Chunk 4: [6, 7, 8, 9]
    # Next start: 10 - 2 = 8
    # Chunk 5: [8, 9]
    chunks = chunk_messages(messages, window=4, overlap=2)
    assert len(chunks) == 5

    # Test message list smaller than window
    chunks = chunk_messages(messages[:3], window=8, overlap=3)
    assert len(chunks) == 1
    assert len(chunks[0].metadata['message_ids']) == 3


async def test_embeddings_client_save_embeddings(mocker):
    # Mock collection_exists and create_collection
    mock_qdrant = MagicMock()
    mock_qdrant.collection_exists = AsyncMock(return_value=False)
    mock_qdrant.create_collection = AsyncMock()
    mock_qdrant.upsert = AsyncMock()

    mocker.patch('src.embeddings.client.AsyncQdrantClient', return_value=mock_qdrant)

    client = MessageEmbeddingsClient('test_collection', 'test_model', 128)

    # Mock the internal API call
    mock_embedding = [0.1] * 128
    client._get_embedding_vectors = AsyncMock(return_value=mock_embedding)

    messages = [create_mock_message(i, f'text {i}') for i in range(5)]

    await client.save(messages)

    # Verify collection check and creation
    assert mock_qdrant.collection_exists.call_count == 1
    assert mock_qdrant.collection_exists.call_args == call('test_collection')
    assert mock_qdrant.create_collection.call_count == 1

    # With default window=8, all 5 messages should be in 1 chunk
    assert mock_qdrant.upsert.call_count == 1
    args, kwargs = mock_qdrant.upsert.call_args
    assert kwargs['collection_name'] == 'test_collection'
    points = kwargs['points']
    assert len(points) == 1
    point = points[0]
    assert isinstance(point.id, UUID)
    assert point.vector == mock_embedding
    assert point.payload['chat_id'] == 123
    assert point.payload['message_ids'] == [str(m.id) for m in messages]
    assert point.payload['participants'] == ['user']
    assert isinstance(point.payload['timestamp'], float)


async def test_embeddings_client_search(mocker):
    mock_qdrant = MagicMock()
    mock_qdrant.collection_exists = AsyncMock(return_value=True)

    # Mock query_points response
    mock_scored_point = ScoredPoint(
        id='chunk_id',
        version=1,
        score=0.9,
        payload={
            'message_ids': ['1', '2'],
            'chat_id': 123,
        }
    )
    mock_response = QueryResponse(points=[mock_scored_point])
    mock_qdrant.query_points = AsyncMock(return_value=mock_response)

    mocker.patch('src.embeddings.client.AsyncQdrantClient', return_value=mock_qdrant)

    client = MessageEmbeddingsClient('test_collection', 'test_model', 128)
    client._get_embedding_vectors = AsyncMock(return_value=[0.1] * 128)

    # Mock get_messages from history
    mock_messages = [
        create_mock_message(1, 'text 1'),
        create_mock_message(2, 'text 2')
    ]
    mock_get_messages = mocker.patch(  # todo replace by real db fetching
        'src.embeddings.messages.get_messages_by_ids', AsyncMock(return_value=mock_messages)
    )

    results = await client.search(123, 'test query', limit=5)

    assert len(results) == 1
    assert isinstance(results[0], RelatedMessagesData)
    assert results[0].score == 0.9
    assert results[0].messages == mock_messages

    assert mock_get_messages.call_count == 1
    assert mock_get_messages.call_args == call(ids=['1', '2'], size=100, sort_order=-1)


async def test_update_chat_embeddings(mocker):
    # Mock DB
    mock_db = mocker.patch('src.processors.context.embeddings.db')

    # No last task
    mock_db.embedding_tasks.find_one = AsyncMock(return_value=None)
    mock_db.embedding_tasks.insert_one = AsyncMock()

    # Mock get_messages
    messages = [create_mock_message(1, 'text 1', chat_id=123)]
    mock_get_messages = mocker.patch(
        'src.processors.context.embeddings.get_messages', AsyncMock(return_value=messages)
    )

    # Mock client
    mock_client = mocker.patch('src.processors.context.embeddings.messages_embeddings_client')
    mock_client.save = AsyncMock()

    await update_chat_embeddings(123)

    assert mock_get_messages.call_count == 1
    assert mock_get_messages.call_args == call(123, size=ANY, from_date=None, sort_order=1)
    assert mock_client.save.call_count == 1
    assert mock_client.save.call_args == call(messages)
    assert mock_db.embedding_tasks.insert_one.call_count == 1

    # Check what was saved to DB
    insert_args = mock_db.embedding_tasks.insert_one.call_args[0][0]
    assert insert_args['chat_id'] == 123
    assert insert_args['last_message_time'] == messages[0].created_at.timestamp()


async def test_search_related_messages_media(mocker):
    media = MessageMedia(
        media_id='m1',
        unique_id='mu1',
        description='cat on a mat',
        ocr_text='MEOW'
    )
    user_message = create_mock_message(1, 'look at this', chat_id=123)
    user_message.media = media

    mock_client = mocker.patch('src.processors.context.embeddings.messages_embeddings_client')
    mock_client.search = AsyncMock(return_value=[])

    await search_related_messages(user_message)

    expected_query = 'look at this|cat on a mat|MEOW'
    assert mock_client.search.call_count == 1
    assert mock_client.search.call_args == call(
        chat_id=123, query=expected_query, limit=ANY
    )


async def test_get_embedding_vectors_api(mocker):
    # This tests the httpx call in _get_embedding_vectors
    client = MessageEmbeddingsClient('test', 'test_model', 128)

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "data": [{"embedding": [0.5] * 128}]
    }
    mock_response.raise_for_status = MagicMock()

    mocker.patch.object(client.api_client, 'post', AsyncMock(return_value=mock_response))

    result = await client._get_embedding_vectors("test text")

    assert result == [0.5] * 128
    assert client.api_client.post.call_count == 1
    assert client.api_client.post.call_args == call(
        '/embeddings',
        json={
            "model": "test_model",
            "input": "test text",
            "encoding_format": "float"
        }
    )
    # cache
    await client._get_embedding_vectors("test text")
    assert client.api_client.post.call_count == 1
    assert client.embeddings_cache.misses == 1
    assert client.embeddings_cache.hits == 1


async def test_facts_embedding_client_save_fact(mocker):
    mock_qdrant = MagicMock()
    mock_qdrant.collection_exists = AsyncMock(return_value=False)
    mock_qdrant.create_collection = AsyncMock()
    mock_qdrant.upsert = AsyncMock()

    mocker.patch('src.embeddings.client.AsyncQdrantClient', return_value=mock_qdrant)

    client = FactsEmbeddingClient('facts', 'test_model', 128)
    client._get_embedding_vectors = AsyncMock(return_value=[0.1] * 128)

    fact = UserFact(_id='abc123', nickname='bob', text='likes pizza', confidence=0.8,
                    created_at=datetime(2024, 1, 1, tzinfo=timezone.utc))

    await client.save_fact(fact)

    assert mock_qdrant.upsert.call_count == 1
    _, kwargs = mock_qdrant.upsert.call_args
    assert kwargs['collection_name'] == 'facts'
    point = kwargs['points'][0]
    assert isinstance(point.id, UUID)
    assert point.vector == [0.1] * 128
    assert point.payload['id'] == 'abc123'
    assert point.payload['nickname'] == 'bob'
    assert point.payload['confidence'] == 0.8
    assert client._get_embedding_vectors.call_args == call('likes pizza')


async def test_facts_embedding_client_search_facts_empty(mocker):
    mocker.patch('src.embeddings.client.AsyncQdrantClient', return_value=MagicMock(
        collection_exists=AsyncMock(return_value=True),
        query_points=AsyncMock(return_value=QueryResponse(points=[]))
    ))

    client = FactsEmbeddingClient('facts', 'test_model', 128)
    client._get_embedding_vectors = AsyncMock(return_value=[0.1] * 128)

    results = await client.search_facts('bob', 'likes pizza', limit=5)

    assert results == []


async def test_facts_embedding_client_search_facts(mocker):
    fact = UserFact(_id='abc123', nickname='bob', text='likes pizza', confidence=0.8)

    mock_scored_point = ScoredPoint(
        id='abc123',
        version=1,
        score=0.85,
        payload={'id': 'abc123', 'nickname': 'bob', 'confidence': 0.8},
    )
    mock_qdrant = MagicMock()
    mock_qdrant.collection_exists = AsyncMock(return_value=True)
    mock_qdrant.query_points = AsyncMock(return_value=QueryResponse(points=[mock_scored_point]))

    mocker.patch('src.embeddings.client.AsyncQdrantClient', return_value=mock_qdrant)
    mocker.patch('src.embeddings.facts.get_fact_by_id', AsyncMock(return_value=fact))

    client = FactsEmbeddingClient('facts', 'test_model', 128)
    client._get_embedding_vectors = AsyncMock(return_value=[0.1] * 128)

    results = await client.search_facts('bob', 'likes pizza', limit=5)

    assert len(results) == 1
    assert isinstance(results[0], FactsSearchResult)
    assert results[0].fact == fact
    assert results[0].score == 0.85


# --- sticker_embedding_text ---

def test_sticker_embedding_text_puts_emoji_first():
    text = sticker_embedding_text(make_description(ocr_text='ЛОЛ'))

    assert text == '🔥 | кот танцует | ЛОЛ'


def test_sticker_embedding_text_drops_missing_parts():
    assert sticker_embedding_text(make_description(emoji=None)) == 'кот танцует'
    assert sticker_embedding_text(make_description(emoji=None, description='')) == ''


# --- _point_id ---

def test_point_id_is_stable_for_the_same_unique_id():
    assert _point_id('abc') == _point_id('abc')
    assert _point_id('abc') != _point_id('abd')


# --- save_sticker ---

async def test_save_sticker_indexes_identity_only(mocker):
    qdrant = MagicMock(
        collection_exists=AsyncMock(return_value=True),
        upsert=AsyncMock(),
    )
    client = make_sticker_client(mocker, qdrant)

    await client.save_sticker(make_description())

    assert qdrant.upsert.call_count == 1
    point = qdrant.upsert.call_args[1]['points'][0]
    assert point.payload == {'unique_id': 'sticker_uid'}
    assert 'file_id' not in point.payload
    assert client._get_embedding_vectors.call_args == call('🔥 | кот танцует')


async def test_save_sticker_skips_empty_composite(mocker):
    qdrant = MagicMock(collection_exists=AsyncMock(return_value=True), upsert=AsyncMock())
    client = make_sticker_client(mocker, qdrant)

    await client.save_sticker(make_description(emoji=None, description='', ocr_text=None))

    assert qdrant.upsert.call_count == 0


async def test_save_sticker_skips_non_ready_row(mocker):
    qdrant = MagicMock(collection_exists=AsyncMock(return_value=True), upsert=AsyncMock())
    client = make_sticker_client(mocker, qdrant)

    await client.save_sticker(make_description(status=MessageMediaStatus.PENDING))
    assert qdrant.upsert.call_count == 0

    await client.save_sticker(make_description(status=MessageMediaStatus.READY))
    assert qdrant.upsert.call_count == 1


async def test_save_sticker_twice_upserts_one_point(mocker):
    # `facts.py` uses uuid4() here and duplicates every point on a backfill re-run.
    qdrant = MagicMock(collection_exists=AsyncMock(return_value=True), upsert=AsyncMock())
    client = make_sticker_client(mocker, qdrant)

    await client.save_sticker(make_description())
    await client.save_sticker(make_description())

    assert qdrant.upsert.call_count == 2
    first = qdrant.upsert.call_args_list[0][1]['points'][0]
    second = qdrant.upsert.call_args_list[1][1]['points'][0]
    assert first.id == second.id


# --- search_stickers ---

async def test_search_stickers_empty_collection_returns_empty(mocker):
    qdrant = MagicMock(
        collection_exists=AsyncMock(return_value=True),
        query_points=AsyncMock(return_value=QueryResponse(points=[])),
    )
    client = make_sticker_client(mocker, qdrant)

    assert await client.search_stickers('что угодно', limit=5) == []


async def test_search_stickers_returns_rows_from_mongo(mocker):
    qdrant = MagicMock(
        collection_exists=AsyncMock(return_value=True),
        query_points=AsyncMock(return_value=QueryResponse(points=[
            ScoredPoint(id='p1', version=1, score=0.42, payload={'unique_id': 'sticker_uid'}),
        ])),
    )
    client = make_sticker_client(mocker, qdrant)
    mocker.patch.object(
        StickerEmbeddingsClient, '_get_description',
        AsyncMock(return_value=make_description(ocr_text='ЛОЛ')),
    )

    results = await client.search_stickers('кот', limit=5)

    assert len(results) == 1
    assert isinstance(results[0], StickerSearchResult)
    assert results[0].unique_id == 'sticker_uid'
    assert results[0].emoji == '🔥'
    assert results[0].description == 'кот танцует'
    assert results[0].ocr_text == 'ЛОЛ'
    assert results[0].score == 0.42


async def test_search_stickers_uses_the_configured_threshold(mocker):
    qdrant = MagicMock(
        collection_exists=AsyncMock(return_value=True),
        query_points=AsyncMock(return_value=QueryResponse(points=[])),
    )
    client = make_sticker_client(mocker, qdrant)
    mocker.patch.object(settings, 'STICKER_SCORE_THRESHOLD', 0.25)

    await client.search_stickers('кот', limit=5)

    assert qdrant.query_points.call_args[1]['score_threshold'] == 0.25


async def test_search_stickers_skips_missing_or_non_ready_rows(mocker):
    qdrant = MagicMock(
        collection_exists=AsyncMock(return_value=True),
        query_points=AsyncMock(return_value=QueryResponse(points=[
            ScoredPoint(id='p1', version=1, score=0.9, payload={'unique_id': 'gone'}),
            ScoredPoint(id='p2', version=1, score=0.8, payload={'unique_id': 'pending'}),
            ScoredPoint(id='p3', version=1, score=0.7, payload={'unique_id': 'ok'}),
        ])),
    )
    client = make_sticker_client(mocker, qdrant)

    async def fake_get(unique_id):
        if unique_id == 'gone':
            return None
        if unique_id == 'pending':
            return make_description(unique_id, status=MessageMediaStatus.PENDING)
        return make_description(unique_id)

    mocker.patch.object(StickerEmbeddingsClient, '_get_description', AsyncMock(side_effect=fake_get))

    results = await client.search_stickers('кот', limit=5)

    assert [r.unique_id for r in results] == ['ok']


# --- drop_sticker ---

async def test_drop_sticker_removes_the_point_for_that_unique_id(mocker):
    qdrant = MagicMock(collection_exists=AsyncMock(return_value=True), delete=AsyncMock())
    client = make_sticker_client(mocker, qdrant)

    await client.drop_sticker('sticker_uid')

    assert qdrant.delete.call_count == 1
    assert qdrant.delete.call_args[1]['collection_name'] == 'stickers'
    assert qdrant.delete.call_args[1]['points_selector'] == [_point_id('sticker_uid')]
