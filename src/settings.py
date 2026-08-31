from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = 'anchovy_chat_ai_bot'
CHARACTERS_DIRECTORY = 'src/characters/repository'
PROMPTS_DIR = 'src/prompts'


class _Settings(BaseSettings):
    model_config = SettingsConfigDict(env_ignore_empty=True)

    TELEGRAM_TOKEN: str
    BOT_NICKNAME: str = 'AnchovyAiBot'
    DATABASE_URL: str
    DATABASE_NAME: str = 'data'

    IS_LOCAL: bool = False

    ALLOWED_CHAT_IDS: list[str] = []
    ALLOWED_USER_IDS: list[str] = []

    RANDOM_REPLY_CHANCE: float = 0.05
    RANDOM_REPLY_COOLDOWN_MINUTES: int = 30

    AI_TIMEOUT: int = 90
    CHAT_RATE_LIMIT: int = 5

    # Web search is a separate budget from the character's own rate limit: it protects
    # spend, not voice, so it is per chat rather than per chat per character.
    WEB_SEARCH_RATE_LIMIT: int = 3   # per 60s window (_WINDOW in rate_limit.py)
    WEB_SEARCH_TIMEOUT: int = 12     # must stay well under AI_TIMEOUT, which wraps the whole loop

    EMBEDDINGS_SEARCH_MAX_SIZE: int = 3

    # Fetch caps: the most messages one memory / embedding pass reads in a window.
    # Kept above their triggers so an ordinary cycle is consumed whole.
    MESSAGES_EMBEDDINGS_MAX_SIZE: int = 60
    MESSAGES_MEMORY_MAX_SIZE: int = 60

    # Triggers: how many new messages must pile up before a pass runs. Separate
    # numbers because they answer separate questions — one is worth an LLM call,
    # the other is how stale Qdrant may get.
    MEMORY_TRIGGER_SIZE: int = 40
    EMBEDDINGS_TRIGGER_SIZE: int = 40

    # The answering character's context window, and nothing else. It used to double
    # as both triggers above, which made every memory cycle fire at the reply
    # window's size.
    LAST_MESSAGES_SIZE: int = 40
    LAST_MESSAGES_MIN_SIZE: int = 5

    RESPOND_MEDIA_PROCESSING_POLLING_TIMEOUT: int = 10

    OPENROUTER_API_URL: str = 'https://openrouter.ai/api/v1'
    OPENROUTER_API_KEY: str | None = None
    QDRANT_URL: str = 'http://qdrant:6333'

    ENABLE_MEMORY_PROCESSING: bool = True
    MEMORY_RETENTION_DAYS: int = 7

    # Decay ships off: phase 1 logs what the policy would evict without evicting it.
    ENABLE_MEMORY_DECAY: bool = False
    TRAITS_KEEP: int = 10
    RECENT_KEEP: int = 5
    RECENT_MAX_CYCLES: int = 20
    TOPICS_KEEP: int = 3  # matches «Максимум 3» in the extraction prompt
    QUESTIONS_KEEP: int = 5
    JOKES_KEEP: int = 5

    EMBEDDINGS_MODEL_NAME: str = 'text-embedding-3-small'
    EMBEDDINGS_VECTOR_SIZE: int = 1536

    @field_validator('ALLOWED_CHAT_IDS', 'ALLOWED_USER_IDS', mode='before')
    @classmethod
    def _coerce_to_str_list(cls, v):
        if isinstance(v, list):
            return [str(i) for i in v]
        return v

    @model_validator(mode='after')
    def _fetch_caps_exceed_triggers(self):
        """Refuses to boot on a cap below its trigger.

        Raises rather than clamps: a silently-wrong cap is the failure this pair of
        settings exists to remove. An under-sized cap no longer loses messages, but
        it does leave a remainder every cycle, which re-fires the trigger at once
        and burns an LLM call per pass.
        """
        if self.MESSAGES_MEMORY_MAX_SIZE < self.MEMORY_TRIGGER_SIZE:
            raise ValueError('MESSAGES_MEMORY_MAX_SIZE must be >= MEMORY_TRIGGER_SIZE')
        if self.MESSAGES_EMBEDDINGS_MAX_SIZE < self.EMBEDDINGS_TRIGGER_SIZE:
            raise ValueError('MESSAGES_EMBEDDINGS_MAX_SIZE must be >= EMBEDDINGS_TRIGGER_SIZE')
        return self


_s = _Settings()

TELEGRAM_TOKEN = _s.TELEGRAM_TOKEN
BOT_NICKNAME = _s.BOT_NICKNAME
DATABASE_URL = _s.DATABASE_URL
DATABASE_NAME = _s.DATABASE_NAME
IS_LOCAL = _s.IS_LOCAL
ALLOWED_CHAT_IDS = _s.ALLOWED_CHAT_IDS
ALLOWED_USER_IDS = _s.ALLOWED_USER_IDS
RANDOM_REPLY_CHANCE = _s.RANDOM_REPLY_CHANCE
RANDOM_REPLY_COOLDOWN_MINUTES = _s.RANDOM_REPLY_COOLDOWN_MINUTES
AI_TIMEOUT = _s.AI_TIMEOUT
CHAT_RATE_LIMIT = _s.CHAT_RATE_LIMIT
WEB_SEARCH_RATE_LIMIT = _s.WEB_SEARCH_RATE_LIMIT
WEB_SEARCH_TIMEOUT = _s.WEB_SEARCH_TIMEOUT
EMBEDDINGS_SEARCH_MAX_SIZE = _s.EMBEDDINGS_SEARCH_MAX_SIZE
MESSAGES_EMBEDDINGS_MAX_SIZE = _s.MESSAGES_EMBEDDINGS_MAX_SIZE
MESSAGES_MEMORY_MAX_SIZE = _s.MESSAGES_MEMORY_MAX_SIZE
MEMORY_TRIGGER_SIZE = _s.MEMORY_TRIGGER_SIZE
EMBEDDINGS_TRIGGER_SIZE = _s.EMBEDDINGS_TRIGGER_SIZE
LAST_MESSAGES_SIZE = _s.LAST_MESSAGES_SIZE
LAST_MESSAGES_MIN_SIZE = _s.LAST_MESSAGES_MIN_SIZE
RESPOND_MEDIA_PROCESSING_POLLING_TIMEOUT = _s.RESPOND_MEDIA_PROCESSING_POLLING_TIMEOUT
OPENROUTER_API_URL = _s.OPENROUTER_API_URL
OPENROUTER_API_KEY = _s.OPENROUTER_API_KEY
QDRANT_URL = _s.QDRANT_URL
ENABLE_MEMORY_PROCESSING = _s.ENABLE_MEMORY_PROCESSING
MEMORY_RETENTION_DAYS = _s.MEMORY_RETENTION_DAYS
ENABLE_MEMORY_DECAY = _s.ENABLE_MEMORY_DECAY
TRAITS_KEEP = _s.TRAITS_KEEP
RECENT_KEEP = _s.RECENT_KEEP
RECENT_MAX_CYCLES = _s.RECENT_MAX_CYCLES
TOPICS_KEEP = _s.TOPICS_KEEP
QUESTIONS_KEEP = _s.QUESTIONS_KEEP
JOKES_KEEP = _s.JOKES_KEEP

EMBEDDINGS_MODEL_SETTINGS = {
    'model_name': _s.EMBEDDINGS_MODEL_NAME,
    'vector_size': _s.EMBEDDINGS_VECTOR_SIZE,
}
