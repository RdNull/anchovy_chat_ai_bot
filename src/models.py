from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import ClassVar

from pydantic import BaseModel as _BaseModel, ConfigDict, Field


class RecapType(str, Enum):
    PERIODIC = 'periodic'
    HOURLY = 'hourly'
    DAILY = 'daily'


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
    text: str | None = None
    nickname: str
    media: MessageMedia | None = None

    def ai_format(self):
        message_part = self.text[:50] if self.text else ''
        if self.media:
            message_part = f'{message_part} [{self.media.ai_format()}]'

        return f'{self.nickname}| {message_part}'


class MessageMedia(BaseModel):
    type: MessageMediaTypes | None = None
    status: MessageMediaStatus = MessageMediaStatus.PENDING
    media_id: str | None = None  # for download
    unique_id: str | None = None  # for identification
    description: str | None = None
    ocr_text: str | None = None

    def ai_format(self):
        media_type_prefix = f'{self.type.value}: ' if self.type else ''
        if self.status == MessageMediaStatus.READY:
            return f'{media_type_prefix}{self.description} | текст: {self.ocr_text or ""}'

        return 'PROCESSING'

    def ai_short_format(self):
        if self.status == MessageMediaStatus.READY:
            media_type_prefix = f'{self.type.value}: ' if self.type else ''
            return f'{media_type_prefix}{self.description[:50]} | текст: {self.ocr_text[:50] if self.ocr_text else ""}'

        return 'PROCESSING'


class Message(BaseModel):
    id: str | None = Field(default=None, alias='_id')
    nickname: str
    role: UserRole
    text: str | None = None
    reply: MessageReply | None = None
    media: MessageMedia | None = None
    created_at: datetime | None = None

    def ai_format(self):
        message_part = self.text
        if self.media:
            message_part = f'{message_part} [{self.media.ai_format()}]'

        if self.reply:
            return f'{self.nickname} (reply: "{self.reply.ai_format()}"): {message_part}'

        return f'{self.nickname}: {message_part}'


class RecapData(BaseModel):
    chat_id: str
    created_at: datetime
    text: str
    type: RecapType = RecapType.PERIODIC


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
    id: str | None = Field(default=None, alias='_id')
    media_id: str | None = None
    description: str
    ocr_text: str | None = None
    type: MessageMediaTypes
    status: MessageMediaStatus = MessageMediaStatus.PROCESSING


class MediaDescriptionData(BaseModel):
    description: str
    ocr_text: str | None = None
