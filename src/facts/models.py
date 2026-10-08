from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from pydantic import Field

from src.models import BaseModel, MongoId


class FactKind(str, Enum):
    BIO = 'bio'
    HABIT = 'habit'
    JOKE = 'joke'


class FactStatus(str, Enum):
    CANDIDATE = 'candidate'
    CONFIRMED = 'confirmed'


class FactOpType(str, Enum):
    ADD = 'add'
    CONFIRM = 'confirm'
    REPLACE = 'replace'


class FactOutcome(str, Enum):
    CREATED = 'created'
    CONFIRMED = 'confirmed'
    PROMOTED = 'promoted'
    REPLACED = 'replaced'
    INVALID_TARGET = 'invalid_target'
    BOT_DROPPED = 'bot_dropped'


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
    op: FactOpType
    target: str | None = None
    nickname: str
    kind: FactKind
    text: str
    self_stated: bool = False


class FactOps(BaseModel):
    ops: list[FactOp]
