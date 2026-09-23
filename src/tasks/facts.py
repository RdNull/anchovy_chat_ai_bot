import time

from src.log_context import log_context
from src.logs import elapsed_ms, event, logger
from src.facts.handlers import decay_all_facts


async def run_fact_decay():
    with log_context(task='fact_decay'):
        logger.info('Running scheduled task', extra=event('TASK_START'))
        started = time.monotonic()
        try:
            await decay_all_facts()
            logger.info(
                'Scheduled task finished',
                extra=event('TASK_DONE', elapsed_ms=elapsed_ms(started)),
            )
        except Exception:
            logger.error(
                'Failed to run fact decay',
                exc_info=True,
                extra=event('TASK_FAILED'),
            )
