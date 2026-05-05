from datetime import datetime

from pydantic import Field

from src.models import BaseModel


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

    def trim(self, keep: int = 5) -> 'StructuredMemory':
        for info in self.participants.values():
            info.traits = info.traits[-keep:]
            info.recent = info.recent[-keep:]
        self.state.active_topics = self.state.active_topics[-keep:]
        self.state.open_questions = self.state.open_questions[-keep:]
        self.state.running_jokes = self.state.running_jokes[-keep:]
        return self

    def prompt_format(self) -> str:
        lines = ['=== ПАМЯТЬ ===']

        if self.participants:
            lines.append('УЧАСТНИКИ:')
            for nick, info in self.participants.items():
                lines.append(nick)
                lines.extend(f'  • {t}' for t in info.traits)
                if info.recent:
                    lines.append('  recent:')
                    lines.extend(f'  - [{r.last_seen_at}] {r.text}' for r in info.recent)

        for header, items in [
            ('\nОБСУЖДАЕТСЯ:', self.state.active_topics),
            ('\nТЕКУЩИЕ ВОПРОСЫ:', self.state.open_questions),
            ('\nТЕКУЩИЕ ШУТКИ:', self.state.running_jokes),
        ]:
            if items:
                lines.append(header)
                lines.extend(f'- {t}' for t in items)

        return '\n'.join(lines)

class MemoryData(BaseModel):
    chat_id: int
    created_at: datetime
    content: StructuredMemory
