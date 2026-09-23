from __future__ import annotations

import hashlib
from datetime import datetime
from typing import ClassVar

from pydantic import Field

from src.messages.models import MessageMediaStatus, MessageMediaTypes
from src.models import BaseModel, MongoId


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
        return hashlib.md5(self.content.encode('utf-8'), usedforsecurity=False).hexdigest()


class AnimationDetectionData(MediaDetectionData):
    content: bytes
    type: ClassVar[MessageMediaTypes] = MessageMediaTypes.GIF

    @property
    def content_hash(self):
        return hashlib.md5(self.content, usedforsecurity=False).hexdigest()


class MediaDescription(BaseModel):
    id: MongoId | None = Field(default=None, alias='_id')
    media_id: str | None = None  # holds a `file_unique_id`, NOT a sendable `file_id`
    description: str
    ocr_text: str | None = None
    type: MessageMediaTypes
    status: MessageMediaStatus = MessageMediaStatus.PROCESSING
    sticker_emoji: str | None = None
    # When `status` last changed. None on a row written before this field existed —
    # treated as stale rather than raising, so a legacy PROCESSING row is retried
    # instead of polled forever.
    updated_at: datetime | None = None


class MediaDescriptionData(BaseModel):
    description: str
    ocr_text: str | None = None
