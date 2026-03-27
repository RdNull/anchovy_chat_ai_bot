import hashlib
from enum import Enum

from pydantic import BaseModel as _BaseModel, ConfigDict, Field
from datetime import datetime


class RecapType(str, Enum):
    PERIODIC = 'periodic'
    HOURLY = 'hourly'
    DAILY = 'daily'


class BaseModel(_BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)


class UserRole(str, Enum):
    USER = 'user'
    AI = 'ai'


class MessageMediaTypes(str, Enum):
    IMAGE = 'image'


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
            message_part = f'{message_part} [img: {self.media.ai_format()}]'

        return f'{self.nickname}| {message_part}'

class MessageMedia(BaseModel):
    type: MessageMediaTypes | None = None
    status: MessageMediaStatus = MessageMediaStatus.PENDING
    media_id: str | None = None
    description: str | None = None
    ocr_text: str | None = None

    def ai_format(self):
        if self.status == MessageMediaStatus.READY:
            return f'{self.description} | текст: {self.ocr_text or ""}'

        return 'PROCESSING'

    def ai_short_format(self):
        if self.status == MessageMediaStatus.READY:
            return f'{self.description[:50]} | текст: {self.ocr_text[:50] if self.ocr_text else ""}'

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
            message_part = f'{message_part} [img: {self.media.ai_format()}]'

        if self.reply:
            return f'{self.nickname} (reply: "{self.reply.ai_format()}"): {message_part}'

        return f'{self.nickname}: {message_part}'


class RecapData(BaseModel):
    chat_id: str
    created_at: datetime
    text: str
    type: RecapType = RecapType.PERIODIC


class ImageDetectionData(BaseModel):
    content: str
    format: str = 'jpg'

    @property
    def content_hash(self):
        return hashlib.md5(self.content.encode('utf-8')).hexdigest()


class ImageDetectionResult(BaseModel):
    id: str | None = Field(default=None, alias='_id')
    media_id: str | None = None
    description: str
    ocr_text: str | None = None
    type: MessageMediaTypes
    status: MessageMediaStatus = MessageMediaStatus.PROCESSING

class ImageDescription(BaseModel):
    description: str
    ocr_text: str | None = None