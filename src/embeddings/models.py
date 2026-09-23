from __future__ import annotations

from datetime import datetime

from pydantic import Field

from src.base import BaseModel, MongoId
from src.messages.models import Message


class EmbeddingTask(BaseModel):
    id: MongoId | None = Field(default=None, alias='_id')
    chat_id: int
    last_message_time: datetime
    created_at: datetime


class RelatedMessagesData(BaseModel):
    messages: list[Message]
    score: float
