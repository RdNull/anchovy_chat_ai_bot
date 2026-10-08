from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import Field

from src.models import BaseModel, MongoId


class FactKind(str, Enum):
    BIO = 'bio'
    HABIT = 'habit'
    JOKE = 'joke'


class FactStatus(str, Enum):
    CANDIDATE = 'candidate'
    CONFIRMED = 'confirmed'


class UserFact(BaseModel):
    id: MongoId | None = Field(default=None, alias='_id')
    nickname: str
    kind: FactKind
    status: FactStatus
    text: str
    # Distinct UTC days the fact was added or confirmed. Mongo holds them as ISO strings:
    # BSON has no date-without-time type.
    sightings: list[date] = Field(default_factory=list)
    created_at: datetime | None = None
    last_seen_at: datetime | None = None


class FactOp(BaseModel):
    reason: str
    op: Literal['add', 'confirm', 'replace']
    target: str | None = None
    nickname: str
    kind: FactKind
    text: str
    self_stated: bool = False


class FactOps(BaseModel):
    ops: list[FactOp]
