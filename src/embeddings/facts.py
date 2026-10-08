import uuid
from dataclasses import dataclass

from src import settings
from src.embeddings.client import ChunkData, EmbeddingsClient
from src.facts.models import FactStatus, UserFact
from src.facts.repository import get_fact_by_id

_FACTS_NAMESPACE = uuid.UUID('6f1b1c0e-5a43-4c5e-9d1e-3f0a8c2b7a11')


def fact_point_id(fact_id: str) -> uuid.UUID:
    """One point per fact: a re-save overwrites it instead of duplicating."""
    return uuid.uuid5(_FACTS_NAMESPACE, str(fact_id))


@dataclass
class FactsSearchResult:
    fact: UserFact
    score: float


class FactsEmbeddingClient(EmbeddingsClient):
    async def save_fact(self, fact: UserFact):
        chunks = [
            ChunkData(
                chunk_id=fact_point_id(fact.id),
                payload=fact.text,
                metadata={
                    'id': str(fact.id),
                    'nickname': fact.nickname,
                    'kind': fact.kind.value,
                    'status': fact.status.value,
                },
            )
        ]
        await self._save(chunks)

    async def delete_fact(self, fact_id: str):
        await self._check_collection()
        await self.qdrant_client.delete(
            collection_name=self.collection_name,
            points_selector=[str(fact_point_id(fact_id))],
        )

    async def search_facts(
        self,
        nickname: str,
        text,
        limit=5,
        status: FactStatus | None = None,
        score_threshold=0.6,
    ) -> list[FactsSearchResult]:
        filters = {'nickname': nickname}
        if status is not None:
            filters['status'] = status.value

        search_results = await self._search(
            text, limit=limit, score_threshold=score_threshold, **filters
        )
        if not search_results:
            return []

        found = []
        for result in search_results:
            if fact := await get_fact_by_id(result.payload['id']):
                found.append(FactsSearchResult(fact, result.score))

        return sorted((r for r in found if r.fact), key=lambda r: r.score, reverse=True)


facts_embedding_client = FactsEmbeddingClient(
    collection_name='facts',
    model_name=settings.EMBEDDINGS_MODEL_SETTINGS['model_name'],
    vector_size=settings.EMBEDDINGS_MODEL_SETTINGS['vector_size'],
)
