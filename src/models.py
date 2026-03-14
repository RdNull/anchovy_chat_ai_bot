from dataclasses import dataclass
from enum import Enum


class UserRole(str, Enum):
    USER = 'user'
    AI = 'ai'


@dataclass
class MessageReply:
    text: str
    nickname: str


from datetime import datetime


@dataclass
class Message:
    nickname: str
    role: UserRole
    text: str
    reply: MessageReply | None = None
    created_at: datetime | None = None
