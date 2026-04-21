from langchain.tools import tool

from src.embeddings.messages import messages_embeddings_client
from src.logs import logger
from src.processors.context.facts import get_facts, save_fact
from src.tools import ToolContext

SEARCH_MESSAGES_DESCRIPTION = '''
Поиск сообщений чата по запросу. 
Не используй для того, что уже есть в текущей истории.

Args:
    search_query: Текст запроса для поиска в свободном формате; Максимум 2 предложения.
    limit: Максимальное количество результатов для возврата; Максимум 5 результатов.

Returns:
    Список найденных блоков сообщений с оценками релевантности (`score`; 0..1) и сообщениями (`messages`).
    Блоки расположены в порядке релевантности; сообщения внутри блока идут по порядку.
'''


@tool(description=SEARCH_MESSAGES_DESCRIPTION)
async def search_messages(search_query: str, limit: int = 3) -> list[dict]:
    if limit < 0 or limit > 5:
        logger.warning(f'[TOOL] search_messages call with wrong limit {limit}, defaulting to 3')
        limit = 3

    tool_context: ToolContext = search_messages.metadata['context']
    chat_id = tool_context.chat_id
    logger.info(f"[TOOL] Searching messages for {search_query}; {limit=}")
    related_messages = await messages_embeddings_client.search(chat_id, search_query, limit=limit)

    return [
        {
            'score': rm.score,
            'messages': '\n'.join([m.ai_format for m in rm.messages]),
        } for rm in related_messages
    ]


KEEP_USER_FACT_TOOL_DESCRIPTION = '''
Сохранить СТАБИЛЬНЫЙ и ВАЖНЫЙ факт о пользователе
Факты сохраняются с оценкой уверенности о факте (confidence).

Args:
 nickname: Никнейм пользователя (`@username`)
 text: Описание факта о пользователе (например: "Любит пиццу")
 confidence: Оценка уверенности в факте от 0 до 1 .
 
Не сохраняй факты с уверенностью ниже 0.5.
'''


@tool(description=KEEP_USER_FACT_TOOL_DESCRIPTION)
async def keep_user_fact(nickname: str, text: str, confidence: float) -> str:
    if confidence < 0.5 or confidence > 1:
        logger.warning(
            f"[TOOL] save_user_fact call with invalid confidence: {confidence}, aborting. ({nickname=} {text=})"
        )
        return f'Wrong confidence: {confidence}'

    nickname = nickname.replace('@', '')
    fact = await save_fact(nickname, text, confidence)
    logger.info(f"[TOOL] Saved fact {fact}")

    return 'ok'


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
