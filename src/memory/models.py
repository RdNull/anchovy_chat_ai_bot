from datetime import datetime

from pydantic import Field

from src.const import TIMEZONE_ALMATY
from src.memory.keys import normalize
from src.models import TIMESTAMP_FORMAT, BaseModel

WEEK_DAYS = 7


class DecayRecord(BaseModel):
    """How long one stored entry has been in memory.

    Lives in `MemoryData.decay`, beside `content` rather than inside it: the model
    never sees these fields and never emits them, which is the whole point of the
    unit — the clock belongs to code.

    `field` exists solely so promotion is detectable. The sidecar keyspace is
    unified across `traits + recent` so an entry's age survives the move, and it
    therefore cannot tell you which list the entry was in last cycle unless the
    record says so.
    """

    born: str  # "ГГ-ММ-ДД ЧЧ:ММ" — the cycle in which this key first appeared
    cycles: int  # cycles survived since birth
    field: str  # 'traits' | 'recent' — the list it lived in last cycle


# The sidecar's shape: nick -> normalized entry key -> record. Declared once and
# imported by `decay.py` and `repository.py`, for the reason `keys.py` gives about
# `normalize` — three copies of a shape only one of which is checked against the
# Mongo write is how the shape drifts.
Decay = dict[str, dict[str, DecayRecord]]


class ParticipantInfo(BaseModel):
    traits: list[str] = Field(default_factory=list)
    recent: list[str] = Field(default_factory=list)


class ChatState(BaseModel):
    active_topics: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    running_jokes: list[str] = Field(default_factory=list)


class StructuredMemory(BaseModel):
    participants: dict[str, ParticipantInfo] = Field(default_factory=dict)
    state: ChatState = Field(default_factory=ChatState)

    def __bool__(self):
        return any(
            (
                self.state.active_topics,
                self.state.open_questions,
                self.state.running_jokes,
                self.participants,
            )
        )


def _relative_age(born: str, now: datetime) -> str | None:
    """Buckets an entry's age into the phrase the answering model reads.

    Compares calendar days rather than elapsed hours, so «вчера» means yesterday
    rather than 24 hours ago.

    Returns:
        The Russian age phrase, or `None` when `born` cannot be read — a malformed
        record renders without a prefix rather than breaking the reply path.
    """
    try:
        born_at = datetime.strptime(born, TIMESTAMP_FORMAT).replace(tzinfo=TIMEZONE_ALMATY)
    except (TypeError, ValueError):
        return None

    days = (now.astimezone(TIMEZONE_ALMATY).date() - born_at.date()).days
    if days <= 0:
        return 'сегодня'
    if days == 1:
        return 'вчера'
    if days <= WEEK_DAYS:
        # 2-4 take «дня», 5 and up take «дней». The range here never reaches the
        # 21/22 forms, so the two cases are the whole rule.
        suffix = 'дня' if days < 5 else 'дней'
        return f'{days} {suffix} назад'
    return 'больше недели назад'


class MemoryData(BaseModel):
    chat_id: int
    created_at: datetime
    content: StructuredMemory
    decay: Decay = Field(default_factory=dict)

    def prompt_format(self, now: datetime | None = None) -> str:
        """Renders memory for the answering character's system prompt.

        `recent` entries carry an age computed here from the sidecar rather than a
        timestamp the extraction model restamped, so the answering model reads
        «вчера» instead of doing unreliable date arithmetic on a value nobody
        verified.

        Args:
            now: The moment ages are measured against. Defaults to wall clock;
                passed explicitly by tests, which cannot use `freezegun` here.
        """
        now = now or datetime.now(TIMEZONE_ALMATY)
        lines = ['=== ПАМЯТЬ ===']

        if self.content.participants:
            lines.append('УЧАСТНИКИ:')
            for nick, info in self.content.participants.items():
                lines.append(nick)
                lines.extend(f'  • {t}' for t in info.traits)
                if info.recent:
                    lines.append('  recent:')
                    lines.extend(self._render_recent(nick, entry, now) for entry in info.recent)

        for header, items in [
            ('\nОБСУЖДАЕТСЯ:', self.content.state.active_topics),
            ('\nТЕКУЩИЕ ВОПРОСЫ:', self.content.state.open_questions),
            ('\nТЕКУЩИЕ ШУТКИ:', self.content.state.running_jokes),
        ]:
            if items:
                lines.append(header)
                lines.extend(f'- {t}' for t in items)

        return '\n'.join(lines)

    def _render_recent(self, nick: str, entry: str, now: datetime) -> str:
        """Renders one `recent` line, with an age prefix when the sidecar has one.

        A missing record is possible transiently — an entry written before the
        sidecar existed, or a key the guard reshaped — and renders bare rather
        than raising, since this runs on the reply path.
        """
        record = self.decay.get(nick, {}).get(normalize(entry))
        age = _relative_age(record.born, now) if record else None
        return f'  - {age}: {entry}' if age else f'  - {entry}'
