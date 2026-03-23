from langchain.chat_models import init_chat_model

from src.logs import logger
from src import settings

_llm_cache = {}

def get_model(model_code: str = None):
    if not model_code or model_code not in settings.AI_MODELS:
        model_code = settings.DEFAULT_AI_MODEL

    if model_code in _llm_cache:
        return _llm_cache[model_code]

    ai_settings = settings.AI_MODELS[model_code]
    logger.debug(f"Initializing LLM '{model_code}' with model: {ai_settings.get('model')}")
    llm = init_chat_model(**ai_settings)
    _llm_cache[model_code] = llm
    return llm

def get_recap_model():
    if cache_model := _llm_cache.get('recap'):
        return cache_model

    _llm_cache['recap'] = init_chat_model(**settings.AI_RECAP_MODEL)
    return _llm_cache['recap']


def get_image_descriptor_model():
    if cache_model := _llm_cache.get('image_descriptor'):
        return cache_model

    _llm_cache['image_descriptor'] = init_chat_model(**settings.AI_IMAGE_DESCRIPTOR_MODEL)
    return _llm_cache['image_descriptor']
