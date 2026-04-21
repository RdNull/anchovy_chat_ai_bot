from dataclasses import dataclass

from src import settings
from src.embeddings.client import ChunkData, EmbeddingsClient
from src.models import UserFact
from src.processors.context.facts import get_fact_by_id


@dataclass
class FactsSearchResult:
    fact: UserFact
    score: float


class FactsEmbeddingClient(EmbeddingsClient):
    async def save_fact(self, fact: UserFact):
        chunks = [
            ChunkData(
                chunk_id=str(fact.id),
                payload=fact.text,
                metadata={
                    'id': str(fact.id),
                    'nickname': fact.nickname,
                    'confidence': fact.confidence,
                    'timestamp': fact.created_at.timestamp() if fact.created_at else None,
                },
            )
        ]
        await self._save(chunks)

    async def search_facts(self, nickname: str, text, limit=5) -> list[FactsSearchResult]:
        results = await self._search(text, limit=limit, nickname=nickname, score_threshold=0.7)
        if not results:
            return []

        results = []
        for result in results:
            if fact := await get_fact_by_id(result.payload['id']):
                results.append(FactsSearchResult(fact, result.score))

        return sorted((r for r in results if r.fact), key=lambda r: r.score, reverse=True)


facts_embedding_client = FactsEmbeddingClient(
    collection_name='facts',
    model_name=settings.EMBEDDINGS_MODEL_SETTINGS['model_name'],
    vector_size=settings.EMBEDDINGS_MODEL_SETTINGS['vector_size'],
)
