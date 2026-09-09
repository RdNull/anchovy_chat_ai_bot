from datetime import datetime

from pydantic import Field

from src.models import BaseModel, Message, MongoId


class InitiativeRun(BaseModel):
    id: MongoId | None = Field(default=None, alias='_id')
    chat_id: int
    last_message_time: datetime
    created_at: datetime


class InitiativeDecision(BaseModel):
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    target_index: int | None = Field(default=None, gt=0)
    reason: str


class InitiativeVerdict(BaseModel):
    target_message: Message | None
    score: float
    reason: str
