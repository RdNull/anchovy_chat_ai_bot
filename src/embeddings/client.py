import hashlib

from httpx import AsyncClient
from qdrant_client import AsyncQdrantClient
from qdrant_client.grpc import VectorParams
from qdrant_client.http.models import FieldCondition, Filter, MatchValue, QueryResponse
from qdrant_client.models import (Distance, PointStruct, VectorParams)

from src import settings
from src.logs import logger
from src.messages.history import get_messages
from src.models import Message, RelatedMessagesData
from src.settings import QDRANT_URL


def chunk_messages(messages: list[Message], window=8, overlap=3) -> list[list[Message]]:
    chunks = []
    for i in range(0, len(messages), window - overlap):
        chunk = messages[i:i + window]
        if len(chunk) < overlap // 2:
            continue

        chunks.append(chunk)

    return chunks


class EmbeddingsClient:
    def __init__(self, collection_name: str, model_name: str, vector_size: int):
        self.collection_name = collection_name
        self.model_name = model_name
        self.vector_size = vector_size
        self.qdrant_client: AsyncQdrantClient = AsyncQdrantClient(QDRANT_URL)
        self.api_client = AsyncClient(base_url=settings.OPENROUTER_API_URL, headers={
            "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        })

    async def _check_collection(self):
        if await self.qdrant_client.collection_exists(self.collection_name):
            return

        await self.qdrant_client.create_collection(
            collection_name=self.collection_name,
            vectors_config=VectorParams(
                size=self.vector_size,
                distance=Distance.COSINE
            ),
        )

    async def search(self, chat_id: int, query, limit=5) -> list[RelatedMessagesData]:
        message_search = await self._search_related_message_ids(chat_id, query, limit)
        if not message_search:
            return []

        result = []
        for search_result in message_search:
            messages = await get_messages(chat_id, ids=search_result['message_ids'])
            result.append(
                RelatedMessagesData(
                    messages=messages,
                    score=search_result['score']
                )
            )

        return result

    async def save_embeddings(self, messages: list[Message]) -> None:
        await self._check_collection()
        chunks = chunk_messages(messages)
        chat_id = messages[0].chat_id

        def get_chunk_id(_chunk: list[Message]):
            key = f'{chat_id}-{"-".join(str(m.id) for m in _chunk)}'
            return hashlib.md5(key.encode()).hexdigest()

        logger.info(
            f"Saving embedding for chat {chat_id} with {len(messages)} in {len(chunks)} chunks"
        )
        for chunk in chunks:
            compressed_messages = '\n'.join([c.ai_format for c in chunk])
            embedding = await self._get_embedding_vectors(compressed_messages)
            chunk_id = get_chunk_id(chunk)
            await self.qdrant_client.upsert(
                collection_name=self.collection_name,
                points=[
                    PointStruct(
                        id=chunk_id,
                        vector=embedding,
                        payload={
                            'message_ids': [str(m.id) for m in chunk],
                            'chat_id': chat_id,
                            'timestamp': chunk[-1].created_at.timestamp(),
                            'participants': list({m.nickname for m in chunk}),
                        }
                    )
                ]
            )
            logger.info(f"Saved embedding for chunk {chunk_id}")

    async def _search_related_message_ids(self, chat_id, query, limit=5) -> list[dict]:
        search_embedding = await self._get_embedding_vectors(query)

        result: QueryResponse = await self.qdrant_client.query_points(
            collection_name=self.collection_name,
            query=search_embedding,
            limit=limit,
            score_threshold=0.2,
            query_filter=Filter(
                must=FieldCondition(
                    key='chat_id',
                    match=MatchValue(value=chat_id)
                )
            )
        )

        return [
            {'message_ids': p.payload['message_ids'], 'score': p.score}
            for p in sorted(result.points, key=lambda p: p.score, reverse=True)
        ]

    async def _get_embedding_vectors(self, text: str) -> list[float]:
        response = await self.api_client.post(
            '/embeddings',
            json={
                "model": self.model_name,
                "input": text,
                "encoding_format": "float"
            })

        response.raise_for_status()
        data = response.json()

        return data["data"][0]["embedding"]



messages_embeddings_client = EmbeddingsClient(
    collection_name='messages',
    model_name=settings.EMBEDDINGS_MODEL_SETTINGS['model_name'],
    vector_size=settings.EMBEDDINGS_MODEL_SETTINGS['vector_size'],
)
