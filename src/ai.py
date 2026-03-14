from langchain.chat_models import init_chat_model

from src import settings

llm = init_chat_model(**settings.AI_INIT_PARAMS)
