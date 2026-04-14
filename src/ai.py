from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from src.logs import logger
from src.model_manager import model_manager

_llm_cache = {}


def get_model(version: str = 'v1') -> BaseChatModel:
    return _get_model('chat', version)


def get_memory_model(version: str = 'v1') -> BaseChatModel:
    return _get_model('memory', version)


def get_image_descriptor_model(version: str = 'v1') -> BaseChatModel:
    return _get_model('image_describe', version)


def get_animation_descriptor_model(version: str = 'v1') -> BaseChatModel:
    return _get_model('animation_describe', version)


def _get_model(task: str, version: str = 'v1') -> BaseChatModel:
    cache_key = f"{task}_{version}"
    if cache_key in _llm_cache:
        return _llm_cache[cache_key]

    ai_settings = model_manager.get_model_settings(task, version)
    logger.debug(
        f"Initializing {task.capitalize()} LLM (version: {version}) with model: {ai_settings.get('model')}"
    )
    llm = init_chat_model(**ai_settings)
    _llm_cache[cache_key] = llm
    return llm
