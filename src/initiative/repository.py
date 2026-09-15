from datetime import datetime, timezone

from src import mongo as db
from src.initiative.models import InitiativeRun
from src.logs import event, logger


async def get_last_initiative_run(chat_id: int) -> InitiativeRun | None:
    initiative_run = await db.initiative_runs.find_one(
        {'chat_id': chat_id}, sort=[('created_at', -1)]
    )
    if not initiative_run:
        return None

    return InitiativeRun(**initiative_run)


async def save_initiative_run(chat_id: int, last_message_time: datetime):
    logger.debug('Saving initiative run', extra=event('INITIATIVE_RUN_SAVED'))
    data = {
        'chat_id': chat_id,
        'last_message_time': last_message_time.timestamp(),
        'created_at': datetime.now(timezone.utc).timestamp()
    }
    await db.initiative_runs.insert_one(data)
