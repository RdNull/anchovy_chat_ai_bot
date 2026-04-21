from dataclasses import dataclass
from typing import Any

from httpx import AsyncClient
from qdrant_client import AsyncQdrantClient
from qdrant_client.grpc import VectorParams
from qdrant_client.http.models import FieldCondition, Filter, MatchValue, QueryResponse
from qdrant_client.models import (Distance, PointStruct, VectorParams)

from src import settings
from src.logs import logger
from src.settings import QDRANT_URL


@dataclass
class ChunkData:
    chunk_id: str
    payload: str
    metadata: dict


@dataclass
class EmbeddingSearchDataItem:
    score: float
    payload: dict[str, Any]


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

    async def _save(self, chunks: list[ChunkData]):
        await self._check_collection()
        for chunk in chunks:
            embedding = await self._get_embedding_vectors(chunk.payload)
            await self.qdrant_client.upsert(
                collection_name=self.collection_name,
                points=[
                    PointStruct(
                        id=chunk.chunk_id,
                        vector=embedding,
                        payload=chunk.metadata,
                    )
                ]
            )
            logger.info(f"Saved embedding for chunk {chunk.chunk_id}")

    async def _search(self, query: str, limit=5, score_threshold=0.2,  **filters) -> list[EmbeddingSearchDataItem]:
        await self._check_collection()
        search_embedding = await self._get_embedding_vectors(query)

        result: QueryResponse = await self.qdrant_client.query_points(
            collection_name=self.collection_name,
            query=search_embedding,
            limit=limit,
            score_threshold=score_threshold,
            query_filter=Filter(
                must=[
                    FieldCondition(
                        key=filter_key,
                        match=MatchValue(value=value)
                    ) for filter_key, value in filters.items()
                ]
            )
        )
        return [
            EmbeddingSearchDataItem(payload=p.payload or {}, score=p.score)
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
