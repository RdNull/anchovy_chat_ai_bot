import hashlib

from src import settings
from src.embeddings.client import ChunkData, EmbeddingsClient
from src.logs import logger
from src.messages.repository import get_messages_by_ids
from src.models import Message, RelatedMessagesData


def chunk_messages(messages: list[Message], window=8, overlap=3) -> list[ChunkData]:
    chat_id = messages[0].chat_id

    def get_chunk_id(_chunk: list[Message]):
        key = f'{chat_id}-{"-".join(str(m.id) for m in _chunk)}'
        return hashlib.md5(key.encode()).hexdigest()

    chunks = []
    for i in range(0, len(messages), window - overlap):
        chunk_messages_ = messages[i:i + window]
        if len(chunk_messages_) < overlap // 2:
            continue

        chunks.append(
            ChunkData(
                chunk_id=get_chunk_id(chunk_messages_),
                payload='\n'.join(m.ai_format for m in chunk_messages_),
                metadata={
                    'chat_id': chat_id,
                    'message_ids': [str(m.id) for m in chunk_messages_],
                    'timestamp': chunk_messages_[-1].created_at.timestamp(),
                    'participants': list({m.nickname for m in chunk_messages_}),
                },
            )
        )

    return chunks


class MessageEmbeddingsClient(EmbeddingsClient):
    async def search(self, chat_id: int, query, limit=5) -> list[RelatedMessagesData]:
        message_search = await self._search_related_message_ids(chat_id, query, limit)
        if not message_search:
            return []

        result = []
        for search_result in message_search:
            messages = await get_messages_by_ids(
                ids=search_result['message_ids'],
                size=100,
                sort_order=-1,
            )
            result.append(
                RelatedMessagesData(
                    messages=messages,
                    score=search_result['score']
                )
            )

        return result

    async def save(self, messages: list[Message]) -> None:
        chunks = chunk_messages(messages)
        chat_id = messages[0].chat_id
        logger.info(
            f"Saving embedding for chat {chat_id} with {len(messages)} in {len(chunks)} chunks"
        )
        await self._save(chunks)

    async def _search_related_message_ids(self, chat_id, query, limit=5) -> list[dict]:
        search_results = await self._search(query, limit=limit, chat_id=chat_id)
        return [
            {'message_ids': result.payload['message_ids'], 'score': result.score}
            for result in search_results
        ]


messages_embeddings_client = MessageEmbeddingsClient(
    collection_name='messages',
    model_name=settings.EMBEDDINGS_MODEL_SETTINGS['model_name'],
    vector_size=settings.EMBEDDINGS_MODEL_SETTINGS['vector_size'],
)
