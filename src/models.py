from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Annotated, ClassVar

from pydantic import BaseModel as _BaseModel, BeforeValidator, ConfigDict, Field

from src import settings
from src.const import TIMEZONE_ALMATY

MongoId = Annotated[str, BeforeValidator(lambda x: str(x))]


class BaseModel(_BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True, arbitrary_types_allowed=True)


class UserRole(str, Enum):
    USER = 'user'
    AI = 'ai'


TIMESTAMP_FORMAT = '%y-%m-%d %H:%M'


def format_ts(value: datetime) -> str:
    """Renders a timestamp the way every prompt in this project expects it.

    The single producer of the `ГГ-ММ-ДД ЧЧ:ММ` format the memory prompt documents.
    `src/memory/decay.py` stamps `DecayRecord.born` through this same helper, so a
    sidecar age and a message timestamp can never drift apart.
    """
    return value.astimezone(TIMEZONE_ALMATY).strftime(TIMESTAMP_FORMAT)


class MessageMediaTypes(str, Enum):
    IMAGE = 'image'
    GIF = 'gif'


class MessageMediaStatus(str, Enum):
    PENDING = 'pending'
    PROCESSING = 'processing'
    READY = 'ready'
    ERROR = 'error'

    @property
    def is_pending(self) -> bool:
        return self not in {MessageMediaStatus.READY, MessageMediaStatus.ERROR}

    @property
    def is_finished(self) -> bool:
        return self in {MessageMediaStatus.READY, MessageMediaStatus.ERROR}


class MessageReply(BaseModel):
    telegram_id: int | None = Field(default=None)
    text: str | None = None
    nickname: str
    media: MessageMedia | None = None

    @property
    def ai_format(self):
        message_part = self.text[:50] if self.text else ''
        if self.media:
            message_part = f'{message_part} [{self.media.ai_format}]'

        return f'{self.nickname}| {message_part}'


class MessageMedia(BaseModel):
    type: MessageMediaTypes | None = None
    status: MessageMediaStatus = MessageMediaStatus.PENDING
    media_id: str | None = None  # for download
    unique_id: str | None = None  # for identification
    description: str | None = None
    ocr_text: str | None = None

    @property
    def ai_format(self):
        media_type_prefix = f'{self.type.value}: ' if self.type else ''
        if self.status == MessageMediaStatus.READY:
            return f'{media_type_prefix}{self.description} | текст: {self.ocr_text or ""}'

        return 'PROCESSING'


class Message(BaseModel):
    id: MongoId | None = Field(default=None, alias='_id')
    telegram_id: int | None = Field(default=None)
    chat_id: int
    nickname: str
    role: UserRole
    text: str | None = None
    reply: MessageReply | None = None
    media: MessageMedia | None = None
    created_at: datetime | None = None
    reactions: dict[str, list[str]] = Field(default_factory=dict)

    @property
    def embedding_text(self) -> str:
        message_part = self.text or ''
        if self.media:
            message_part = f'{message_part} [{self.media.ai_format}]'

        if self.reply:
            body = f'{self.nickname} (reply: "{self.reply.ai_format}"): {message_part}'
        else:
            body = f'{self.nickname}: {message_part}'

        if self.created_at:
            return f'[{format_ts(self.created_at)}] {body}'
        return body

    @property
    def ai_format(self) -> str:
        base = self.embedding_text
        if reactions_line := self._render_reactions():
            return f'{base}\n{reactions_line}'
        return base

    @property
    def response_format(self) -> str:
        text = self.text or ''
        if reactions_line := self._render_reactions():
            return f'{text}\n{reactions_line}'
        return text

    def _render_reactions(self) -> str | None:
        if not self.reactions:
            return None

        bot_nickname = settings.BOT_NICKNAME
        parts = []
        for emoji, nicknames in self.reactions.items():
            if not nicknames:
                continue

            bot_reacted = bot_nickname in nicknames
            others = [n for n in nicknames if n != bot_nickname]
            named = ([bot_nickname] if bot_reacted else [])
            if len(others) <= 3:
                named.extend(others)
            unnamed_count = len(nicknames) - len(named)
            if named:
                part = f'{emoji} {", ".join(named)}'
                if unnamed_count > 0:
                    part += f' +{unnamed_count}'
            else:
                part = f'{emoji} ×{len(nicknames)}'

            parts.append(part)

        return f"⤷ {' · '.join(parts)}" if parts else None


class UpdateMessage(BaseModel):
    id: MongoId
    text: str


class MediaDetectionData(BaseModel):
    format: str
    type: ClassVar[MessageMediaTypes]

    @property
    def content_hash(self):
        raise NotImplementedError()


class ImageDetectionData(MediaDetectionData):
    content: str
    type: ClassVar[MessageMediaTypes] = MessageMediaTypes.IMAGE

    @property
    def content_hash(self):
        return hashlib.md5(self.content.encode('utf-8')).hexdigest()


class AnimationDetectionData(MediaDetectionData):
    content: bytes
    type: ClassVar[MessageMediaTypes] = MessageMediaTypes.GIF

    @property
    def content_hash(self):
        return hashlib.md5(self.content).hexdigest()


class MediaDescription(BaseModel):
    id: MongoId | None = Field(default=None, alias='_id')
    media_id: str | None = None
    description: str
    ocr_text: str | None = None
    type: MessageMediaTypes
    status: MessageMediaStatus = MessageMediaStatus.PROCESSING


class MediaDescriptionData(BaseModel):
    description: str
    ocr_text: str | None = None


class EmbeddingTask(BaseModel):
    id: MongoId | None = Field(default=None, alias='_id')
    chat_id: int
    last_message_time: datetime
    created_at: datetime


class RelatedMessagesData(BaseModel):
    messages: list[Message]
    score: float


class UserFact(BaseModel):
    id: MongoId | None = Field(default=None, alias='_id')
    nickname: str
    text: str
    confidence: float
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ExtractedFact(BaseModel):
    nickname: str
    text: str
    confidence: float


class ExtractedFacts(BaseModel):
    facts: list[ExtractedFact]
