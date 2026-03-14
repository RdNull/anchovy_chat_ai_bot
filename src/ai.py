import logging

from langchain.chat_models import init_chat_model

from src import settings

logger = logging.getLogger(__name__)

ai_settings = settings.AI_LOCAL_INIT_PARAMS if settings.IS_LOCAL else settings.AI_CLOUD_INIT_PARAMS
logger.info(f"Initializing LLM with params: {ai_settings.get('model')}")
llm = init_chat_model(**ai_settings)
