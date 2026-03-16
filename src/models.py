from enum import Enum

from pydantic import BaseModel
from datetime import datetime

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
