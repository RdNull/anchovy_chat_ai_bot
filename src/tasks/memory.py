import time
from uuid import uuid4

from src import settings
from src.log_context import log_context
from src.logs import elapsed_ms, event, logger
from src.memory.handlers import delete_old_memories


async def run_memory_cleanup():
    with log_context(task='memory_cleanup', request_id=uuid4().hex[:8]):
        logger.info('Running scheduled task', extra=event('TASK_START'))
        started = time.monotonic()
        try:
            await delete_old_memories(settings.MEMORY_RETENTION_DAYS)
            logger.info(
                'Scheduled task finished', extra=event('TASK_DONE', elapsed_ms=elapsed_ms(started)),
            )
        except Exception:
            logger.error(
                'Failed to run memory cleanup', exc_info=True, extra=event('TASK_FAILED'),
            )
