from datetime import datetime, timezone
from unittest.mock import ANY, AsyncMock, MagicMock, call

from qdrant_client.http.models import QueryResponse, ScoredPoint

from src.embeddings.client import EmbeddingsClient, chunk_messages
from src.models import Message, MessageMedia, RelatedMessagesData, UserRole
from src.processors.context.embeddings import search_related_messages, update_chat_embeddings


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
    assert len(chunks[0]) == 8
    assert len(chunks[1]) == 5
    assert chunks[0][-1].text == 'text 7'
    assert chunks[1][0].text == 'text 5'

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
    assert len(chunks[0]) == 3


async def test_embeddings_client_save_embeddings(mocker):
    # Mock collection_exists and create_collection
    mock_qdrant = MagicMock()
    mock_qdrant.collection_exists = AsyncMock(return_value=False)
    mock_qdrant.create_collection = AsyncMock()
    mock_qdrant.upsert = AsyncMock()

    mocker.patch('src.embeddings.client.AsyncQdrantClient', return_value=mock_qdrant)

    client = EmbeddingsClient('test_collection', 'test_model', 128)

    # Mock the internal API call
    mock_embedding = [0.1] * 128
    client._get_embedding_vectors = AsyncMock(return_value=mock_embedding)

    messages = [create_mock_message(i, f'text {i}') for i in range(5)]

    await client.save_embeddings(messages)

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

    client = EmbeddingsClient('test_collection', 'test_model', 128)
    client._get_embedding_vectors = AsyncMock(return_value=[0.1] * 128)

    # Mock get_messages from history
    mock_messages = [
        create_mock_message(1, 'text 1'),
        create_mock_message(2, 'text 2')
    ]
    mock_get_messages = mocker.patch(
        'src.embeddings.client.get_messages_by_ids', AsyncMock(return_value=mock_messages)
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
    mock_client.save_embeddings = AsyncMock()

    await update_chat_embeddings(123)

    assert mock_get_messages.call_count == 1
    assert mock_get_messages.call_args == call(123, size=ANY, from_date=None)
    assert mock_client.save_embeddings.call_count == 1
    assert mock_client.save_embeddings.call_args == call(messages)
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
    client = EmbeddingsClient('test', 'test_model', 128)

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
