from langchain.tools import tool

from src.logs import logger
from src.models import UserFact
from src.processors.context.facts import get_facts, save_fact

SAVE_USER_FACT_TOOL_DESCRIPTION = '''
Сохранить СТАБИЛЬНЫЙ и ВАЖНЫЙ факт о пользователе
Факты сохраняются с оценкой уверенности о факте (confidence).

Args:
 nickname: Никнейм пользователя (`@username`)
 text: Описание факта о пользователе (например: "Любит пиццу")
 confidence: Оценка уверенности в факте от 0 до 1 .
 
Не сохраняй факты с уверенностью ниже 0.5.
'''


@tool(description=SAVE_USER_FACT_TOOL_DESCRIPTION)
async def save_user_fact(nickname: str, text: str, confidence: float) -> UserFact | None:
    if confidence < 0.5 or confidence > 1:
        logger.warning(
            f"[TOOL] save_user_fact call with invalid confidence: {confidence}, aborting. ({nickname=} {text=})"
        )
        return None

    nickname = nickname.replace('@', '')
    fact = await save_fact(nickname, text, confidence)
    logger.info(f"[TOOL] Saved fact {fact}")
    return fact


GET_USER_FACT_TOOL_DESCRIPTION = '''
Получить КЛЮЧЕВЫЕ факты о пользователе
Args:
- nickname: Никнейм пользователя
- limit: Максимальное количество фактов для получения; От 1 до 20.

Returns:
- text: Описание факта
- confidence: Оценка уверенности в факте от 0 до 1

Факты отсортированы по confidence от большего к меньшему.
'''


@tool(description=GET_USER_FACT_TOOL_DESCRIPTION)
async def get_user_facts(nickname: str, limit: int = 5) -> list[dict]:
    if limit < 0 or limit > 20:  # dumb check, but I don't trust AI
        logger.warning(f"[TOOL] get_user_facts call with wrong limit {limit}, defaulting to 5")
        limit = 5

    nickname = nickname.replace('@', '')
    facts = await get_facts(nickname, limit=limit)
    logger.info(f"[TOOL] Retrieved {len(facts)} facts for {nickname}")
    return [
        fact.model_dump(include={'text', 'confidence'})
        for fact in facts
    ]
