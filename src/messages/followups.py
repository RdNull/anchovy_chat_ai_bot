from src.embeddings.handlers import run_embedding_checks
from src.initiative.handlers import run_initiative_checks
from src.logs import event, logger
from src.memory.handlers import run_memory_checks


async def run_followups(chat_id: int):
    """Runs after every message: initiative, then memory, then embeddings.

    Initiative is guarded on its own — it runs first, and the caller is a bare
    `create_task`, so an unhandled failure here would take the other two stages
    down with it and surface only as a `Task exception was never retrieved` at
    GC time.
    """
    try:
        await run_initiative_checks(chat_id)
    except Exception:
        logger.error(
            'Error running initiative checks',
            exc_info=True,
            extra=event('INITIATIVE_CHECK', outcome='error'),
        )

    await run_memory_checks(chat_id)
    await run_embedding_checks(chat_id)
