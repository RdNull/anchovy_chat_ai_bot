from src.logs import logger
from src.processors.context.facts import decay_all_facts


async def run_fact_decay():
    logger.info("Running scheduled fact confidence decay")
    try:
        await decay_all_facts()
    except Exception as e:
        logger.error(f"Failed to run fact decay: {e}", exc_info=True)
