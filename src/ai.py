import logging
from langchain.chat_models import init_chat_model

from src import settings

logger = logging.getLogger(__name__)

logger.info(f"Initializing LLM with params: {settings.AI_INIT_PARAMS}")
llm = init_chat_model(**settings.AI_INIT_PARAMS)
