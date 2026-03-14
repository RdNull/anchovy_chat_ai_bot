from langchain.chat_models import init_chat_model

from src.logs import logger
from src import settings

ai_settings = settings.AI_LOCAL_INIT_PARAMS if settings.IS_LOCAL else settings.AI_CLOUD_INIT_PARAMS
logger.info(f"Initializing LLM with params: {ai_settings.get('model')}")
llm = init_chat_model(**ai_settings)
