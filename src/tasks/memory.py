from src import settings
from src.logs import logger
from src.memory.handlers import delete_old_memories


async def run_memory_cleanup():
    logger.info('Running scheduled memory cleanup')
    try:
        await delete_old_memories(settings.MEMORY_RETENTION_DAYS)
    except Exception as e:
        logger.error(f'Failed to run memory cleanup: {e}', exc_info=True)
