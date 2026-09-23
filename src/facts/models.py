from __future__ import annotations

from datetime import datetime

from pydantic import Field

from src.base import BaseModel, MongoId


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
