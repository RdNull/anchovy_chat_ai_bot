from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Annotated, ClassVar

from pydantic import BaseModel as _BaseModel, BeforeValidator, ConfigDict, Field

from src.const import TIMEZONE_ALMATY

MongoId = Annotated[str, BeforeValidator(lambda x: str(x))]


class BaseModel(_BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True, arbitrary_types_allowed=True)


class UserRole(str, Enum):
    USER = 'user'
    AI = 'ai'


class MessageMediaTypes(str, Enum):
    IMAGE = 'image'
    GIF = 'gif'


class MessageMediaStatus(str, Enum):
    PENDING = 'pending'
    PROCESSING = 'processing'
    READY = 'ready'
    ERROR = 'error'


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

    @property
    def ai_format(self):
        message_part = self.text
        if self.media:
            message_part = f'{message_part} [{self.media.ai_format}]'

        if self.reply:
            body = f'{self.nickname} (reply: "{self.reply.ai_format}"): {message_part}'
        else:
            body = f'{self.nickname}: {message_part}'

        if self.created_at:
            ts = self.created_at.astimezone(TIMEZONE_ALMATY).strftime('%y-%m-%d %H:%M')
            return f'[{ts}] {body}'
        return body


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
