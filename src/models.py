import hashlib
from enum import Enum

from pydantic import BaseModel as _BaseModel, ConfigDict, Field
from datetime import datetime


class RecapType(str, Enum):
    PERIODIC = 'periodic'  # current logic (by message count)
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
    PROCESSING = 'processing'
    READY = 'ready'
    ERROR = 'error'

class MessageReply(BaseModel):
    text: str
    nickname: str


class MessageMedia(BaseModel):
    type: MessageMediaTypes
    status: MessageMediaStatus = MessageMediaStatus.PROCESSING
    media_id: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    ocr_text: str | None = None


class Message(BaseModel):
    id: str | None = Field(alias='_id')
    nickname: str
    role: UserRole
    text: str | None = None
    reply: MessageReply | None = None
    media: MessageMedia | None = None
    created_at: datetime | None = None

    def ai_format(self):
        message_part = f'TEXT: {self.text}'
        if self.media:
            image_description = 'processing...'
            if self.media.status == MessageMediaStatus.ERROR:
                image_description = 'processing_error'

            if self.media.status == MessageMediaStatus.READY:
                image_description = f'{self.media.description}'
                if self.media.tags:
                    tags = ','.join(self.media.tags)
                    image_description = f'{image_description}|tags: {tags}'

                if self.media.ocr_text:
                    image_description = f'{image_description}|ocr: {self.media.ocr_text}'

            message_part = f'{message_part} [IMAGE: {image_description}]'


        if self.reply:
            return f'{self.nickname} (REPLY_TO "{self.reply.text}"): {message_part}'

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
    description: str
    tags: list[str]
    ocr_text: str | None = None
