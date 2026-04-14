import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from src.embeddings.client import messages_embeddings_client
from src.messages.history import get_history
from src.processors.context.embeddings import save_embedding_task

parser = argparse.ArgumentParser(description='Script so useful.')
parser.add_argument("--date-from", type=str)
parser.add_argument("--chat", type=int)


async def create_embeddings(chat_id: int, _from: datetime):
    messages = await get_history(
        chat_id,
        size=50,
        from_date=_from,
        sort_order=1,
    )
    if not messages:
        return

    await messages_embeddings_client.save_embeddings(messages)
    last_message_dt = messages[0].created_at
    await save_embedding_task(chat_id, last_message_dt)

    await create_embeddings(chat_id, last_message_dt)


if __name__ == '__main__':
    args = parser.parse_args()
    if date_from := args.date_from:
        date_from = datetime.fromisoformat(date_from)
    else:
        date_from = datetime.now(timezone.utc) - timedelta(days=1)

    chat = args.chat
    if not chat:
        raise ValueError("Chat ID must be provided")

    asyncio.run(create_embeddings(chat_id=chat, _from=date_from))
