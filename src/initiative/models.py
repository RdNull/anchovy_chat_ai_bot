from pydantic import Field

from src.models import BaseModel, Message


class InitiativeDecision(BaseModel):
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    target_index: int | None
    reason: str


class InitiativeVerdict(BaseModel):
    target_message: Message | None
    score: float
    reason: str
