"""Backfill message embeddings for a chat, with optional start date filter."""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from src.embeddings.messages import messages_embeddings_client
from src.messages.repository import get_messages
from src.processors.context.embeddings import save_embedding_task

parser = argparse.ArgumentParser(description='Backfill message embeddings for a chat.')
parser.add_argument('--date-from', type=str)
parser.add_argument('--chat', type=int, required=True)


async def create_embeddings(chat_id: int, date_from: datetime):
    """Generate embeddings for messages in a chat.

    Args:
        chat_id: The chat ID to process.
        date_from: Start date for the backfill (inclusive). Messages before this date are skipped.
    """
    overlap_messages = []
    current_from = date_from

    while True:
        messages = await get_messages(
            chat_id,
            size=100,
            from_date=current_from,
            sort_order=1,
        )

        if not messages:
            break

        # Prepend overlap messages from previous batch
        batch_messages = overlap_messages + messages

        # Save embeddings
        await messages_embeddings_client.save(batch_messages)

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
    asyncio.run(create_embeddings(chat_id=chat, date_from=date_from))
