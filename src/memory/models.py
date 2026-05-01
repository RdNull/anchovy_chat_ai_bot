from datetime import datetime

from pydantic import Field

from models import BaseModel


class RecentItem(BaseModel):
    text: str
    last_seen_at: str  # "ГГ-ММ-ДД ЧЧ:ММ" — same format as Message timestamps


class ParticipantInfo(BaseModel):
    traits: list[str] = Field(default_factory=list)
    recent: list[RecentItem] = Field(default_factory=list)


class ChatState(BaseModel):
    active_topics: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    running_jokes: list[str] = Field(default_factory=list)


class StructuredMemory(BaseModel):
    participants: dict[str, ParticipantInfo] = Field(default_factory=dict)
    state: ChatState = Field(default_factory=ChatState)


class MemoryData(BaseModel):
    chat_id: int
    created_at: datetime
    content: StructuredMemory
