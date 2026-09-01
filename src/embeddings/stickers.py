import hashlib
from dataclasses import dataclass
from uuid import UUID

from src import settings
from src.embeddings.client import ChunkData, EmbeddingsClient
from src.logs import logger
from src.models import MediaDescription, MessageMediaStatus


@dataclass
class StickerSearchResult:
    """A hydrated candidate. Carries no similarity score on purpose.

    Candidates now arrive fused from several probes, and cosine scores from different
    query embeddings are not calibrated against each other — keeping the field would
    invite exactly the score-sorted merge that fusion exists to avoid.
    """
    unique_id: str
    emoji: str | None
    description: str
    ocr_text: str | None


def sticker_embedding_text(description: MediaDescription) -> str:
    """The text a sticker is retrieved by.

    The emoji comes first because it is a free human-authored sentiment label and a
    long vision description would otherwise average it away.
    """
    parts = [description.sticker_emoji, description.description, description.ocr_text]
    return ' | '.join(part for part in parts if part)


def _point_id(unique_id: str) -> UUID:
    """Derives the point id from the sticker's identity, so a re-run upserts.

    `facts.py` uses `uuid4()` here and therefore duplicates every point on each
    backfill re-run; this follows `chunk_messages` instead.
    """
    return UUID(hashlib.md5(unique_id.encode()).hexdigest())


class StickerEmbeddingsClient(EmbeddingsClient):
    async def save_sticker(self, description: MediaDescription) -> None:
        text = sticker_embedding_text(description)
        if not text:
            logger.info(f'Nothing to embed for sticker {description.media_id}, skipping')
            return

        if description.status != MessageMediaStatus.READY:
            logger.info(f'Sticker {description.media_id} is not ready, skipping')
            return

        # The payload carries identity only. The sendable `file_id` is resolved from
        # `messages` at send time, so a re-issued id never needs a reindex.
        await self._save([
            ChunkData(
                chunk_id=_point_id(description.media_id),
                payload=text,
                metadata={'unique_id': description.media_id},
            )
        ])

    async def search_sticker_ids(self, query: str, limit: int) -> list[str]:
        """One probe's ranked identities, most relevant first.

        Deliberately does not touch Mongo: several probes are fused before anything is
        read back, so hydrating here would read the same row once per probe that
        returned it and once more for every candidate the fusion then discards.
        """
        search_results = await self._search(
            query, limit=limit, score_threshold=settings.STICKER_SCORE_THRESHOLD,
        )

        found = []
        for result in search_results:
            unique_id = result.payload.get('unique_id')
            if unique_id and unique_id not in found:
                found.append(unique_id)

        return found

    async def get_sticker(self, unique_id: str) -> StickerSearchResult | None:
        """Reads a fused candidate back, or `None` if the index outran the store."""
        description = await self._get_description(unique_id)
        if not description or description.status != MessageMediaStatus.READY:
            # The row was deleted or regressed; a point can outlive its description.
            return None

        return StickerSearchResult(
            unique_id=unique_id,
            emoji=description.sticker_emoji,
            description=description.description,
            ocr_text=description.ocr_text,
        )

    @staticmethod
    async def _get_description(unique_id: str) -> MediaDescription | None:
        # Imported here, not at module scope: `src/messages/media/__init__.py` eagerly
        # imports `pipeline`, which imports this module, so a module-level import would
        # make the cycle bite whenever `src.embeddings.stickers` is imported first —
        # the backfill script does exactly that. `facts.py` gets away with the
        # module-level form only because `src/facts/__init__.py` is empty.
        from src.messages.media.repository import get_media_description_by_media_id

        return await get_media_description_by_media_id(unique_id)

    async def drop_sticker(self, unique_id: str) -> None:
        logger.info(f'Dropping sticker {unique_id} from the index')
        await self.qdrant_client.delete(
            collection_name=self.collection_name,
            points_selector=[_point_id(unique_id)],
        )


stickers_embedding_client = StickerEmbeddingsClient(
    collection_name='stickers',
    model_name=settings.EMBEDDINGS_MODEL_SETTINGS['model_name'],
    vector_size=settings.EMBEDDINGS_MODEL_SETTINGS['vector_size'],
)
