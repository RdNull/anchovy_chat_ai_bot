import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from src.embeddings.client import messages_embeddings_client
from src.messages.repository import get_history
from src.processors.context.embeddings import save_embedding_task

parser = argparse.ArgumentParser(description='Script so useful.')
parser.add_argument("--date-from", type=str)
parser.add_argument("--chat", type=int)


async def create_embeddings(chat_id: int, _from: datetime):
    overlap_messages = []
    current_from = _from

    while True:
        messages = await get_history(
            chat_id,
            size=100,
            from_date=current_from,
            sort_order=1,
        )

        if not messages:
            break

        # messages are returned reversed by get_history (newest first)
        # We need them in chronological order
        messages.sort(key=lambda m: m.created_at)

        # Prepend overlap messages from previous batch
        batch_messages = overlap_messages + messages

        # Save embeddings
        await messages_embeddings_client.save_embeddings(batch_messages)

        # Update current_from for next iteration
        last_message_dt = messages[-1].created_at
        current_from = last_message_dt

        # Checkpoint: Save progress
        await save_embedding_task(chat_id, last_message_dt)

        # Prepare overlap for next batch
        overlap_messages = messages[-3:]

        if len(messages) < 20:
            break


if __name__ == '__main__':  # pragma: no cover
    args = parser.parse_args()
    if date_from := args.date_from:
        date_from = datetime.fromisoformat(date_from)
    else:
        date_from = datetime.now(timezone.utc) - timedelta(days=1)

    chat = args.chat
    if not chat:
        raise ValueError("Chat ID must be provided")

    asyncio.run(create_embeddings(chat_id=chat, _from=date_from))
