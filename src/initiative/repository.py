from datetime import datetime, timezone

from src import mongo as db
from src.initiative.models import InitiativeRun
from src.logs import logger


async def get_last_initiative_run(chat_id: int) -> InitiativeRun | None:
    initiative_run = await db.initiative_runs.find_one(
        {'chat_id': chat_id}, sort=[('created_at', -1)]
    )
    if not initiative_run:
        return None

    return InitiativeRun(**initiative_run)


async def save_initiative_run(chat_id: int, last_message_time: datetime):
    logger.info(f'Saving initiative run for chat {chat_id}')
    data = {
        'chat_id': chat_id,
        'last_message_time': last_message_time.timestamp(),
        'created_at': datetime.now(timezone.utc).timestamp()
    }
    await db.initiative_runs.insert_one(data)
