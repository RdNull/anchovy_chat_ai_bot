from enum import Enum

from pydantic import BaseModel as _BaseModel, ConfigDict
from datetime import datetime


class BaseModel(_BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)

class UserRole(str, Enum):
    USER = 'user'
    AI = 'ai'


class MessageReply(BaseModel):
    text: str
    nickname: str


class Message(BaseModel):
    nickname: str
    role: UserRole
    text: str
    reply: MessageReply | None = None
    created_at: datetime | None = None


class RecapData(BaseModel):
    chat_id: str
    created_at: datetime
    text: str
