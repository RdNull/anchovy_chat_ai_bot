from src import settings
from src.initiative.models import InitiativeVerdict


async def pre_check(chat_id: int) -> bool:
    # todo add it
    return True


async def decide(evaluation: InitiativeVerdict) -> bool:
    if evaluation.score < 0 or evaluation.score > 1:
        return False

    return evaluation.score >= settings.INITIATIVE_SCORE_THRESHOLD
