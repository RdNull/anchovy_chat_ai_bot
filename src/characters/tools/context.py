from langchain.tools import tool

from src.embeddings.messages import messages_embeddings_client
from src.facts.repository import get_facts
from src.logs import logger
from src.tools import ToolContext

SEARCH_MESSAGES_DESCRIPTION = '''
[context]: Поиск сообщений чата по запросу
Не используй для того, что уже есть в текущей истории
Args:
    search_query: Текст запроса для поиска в свободном формате; Максимум 2 предложения
    limit: количество результатов для возврата
Returns:
    Список найденных блоков сообщений с оценками релевантности (`score`; 0..1) и сообщениями (`messages`)
    Блоки расположены в порядке релевантности; сообщения внутри блока идут по порядку
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
            'messages': '\n'.join([m.embedding_text for m in rm.messages]),
        } for rm in related_messages
    ]


GET_USER_FACT_TOOL_DESCRIPTION = '''
[context]: Получить КЛЮЧЕВЫЕ факты о пользователе
Args:
- nickname: Никнейм пользователя
- limit: Количество фактов для получения
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
