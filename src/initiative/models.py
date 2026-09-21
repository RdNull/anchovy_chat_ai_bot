from datetime import datetime

from pydantic import Field

from src.models import BaseModel, Message, MongoId


class InitiativeRun(BaseModel):
    id: MongoId | None = Field(default=None, alias='_id')
    chat_id: int
    last_message_time: datetime
    created_at: datetime
    # Stamped at decision time (before the reply is actually dispatched) by the send
    # path only — the dry-run and below-threshold branches leave this unset. That is
    # what count_replied_last_24h counts against INITIATIVE_DAILY_LIMIT.
    replied_at: datetime | None = None


class InitiativeDecision(BaseModel):
    reason: str
    target_index: int | None = None
    score: float = Field(default=0.0, ge=0.0, le=1.0)


class InitiativeVerdict(BaseModel):
    target_message: Message | None
    score: float
    reason: str
