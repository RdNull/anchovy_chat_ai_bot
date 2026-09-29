from datetime import datetime

from pydantic import Field

from src.messages.models import Message
from src.models import BaseModel, MongoId


class InitiativeRun(BaseModel):
    id: MongoId | None = Field(default=None, alias='_id')
    chat_id: int
    last_message_time: datetime
    created_at: datetime
    # Stamped at delivery, by the reply task, once text or a sticker was actually sent —
    # dry-run, below-threshold and empty runs leave this unset. That is what
    # count_replied_since counts against INITIATIVE_DAILY_LIMIT.
    replied_at: datetime | None = None


class InitiativeDecision(BaseModel):
    reason: str
    target_index: int | None = None
    score: float = Field(default=0.0, ge=0.0, le=1.0)


class InitiativeVerdict(BaseModel):
    target_message: Message | None
    score: float
    reason: str
