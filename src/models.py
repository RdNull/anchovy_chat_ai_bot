from enum import Enum

from pydantic import BaseModel as _BaseModel, ConfigDict
from datetime import datetime


class RecapType(str, Enum):
    PERIODIC = 'periodic'  # current logic (by message count)
    HOURLY = 'hourly'
    DAILY = 'daily'


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

    def ai_format(self):
        if self.reply:
            return f'{self.nickname} (в ответ на "{self.reply.text}"): {self.text}'
        return f'{self.nickname}: {self.text}'

class RecapData(BaseModel):
    chat_id: str
    created_at: datetime
    text: str
    type: RecapType = RecapType.PERIODIC
