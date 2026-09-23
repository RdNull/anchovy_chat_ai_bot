from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field

from src import settings
from src.base import BaseModel, MongoId, format_ts


class UserRole(str, Enum):
    USER = 'user'
    AI = 'ai'


class MessageMediaTypes(str, Enum):
    IMAGE = 'image'
    GIF = 'gif'
    # Never produced by `src/media/download.py`, which types by file extension and
    # cannot tell a `.webp` sticker from a `.webp` photo. Set from Telegram's own
    # metadata at parse time; see `messages/parsing.py:_mark_sticker`.
    STICKER = 'sticker'


class MessageMediaStatus(str, Enum):
    PENDING = 'pending'
    PROCESSING = 'processing'
    READY = 'ready'
    ERROR = 'error'

    @property
    def is_pending(self) -> bool:
        return self not in {MessageMediaStatus.READY, MessageMediaStatus.ERROR}

    @property
    def is_finished(self) -> bool:
        return self in {MessageMediaStatus.READY, MessageMediaStatus.ERROR}


class MessageReply(BaseModel):
    telegram_id: int | None = Field(default=None)
    text: str | None = None
    nickname: str
    media: MessageMedia | None = None

    @property
    def ai_format(self):
        message_part = self.text[:50] if self.text else ''
        if self.media:
            message_part = f'{message_part} [{self.media.ai_format}]'

        return f'{self.nickname}| {message_part}'


class MessageMedia(BaseModel):
    type: MessageMediaTypes | None = None
    status: MessageMediaStatus = MessageMediaStatus.PENDING
    media_id: str | None = None  # for download
    unique_id: str | None = None  # for identification
    description: str | None = None
    ocr_text: str | None = None
    sticker_emoji: str | None = None

    @property
    def ai_format(self):
        media_type_prefix = f'{self.type.value}: ' if self.type else ''
        if self.status == MessageMediaStatus.READY:
            return f'{media_type_prefix}{self.description} | текст: {self.ocr_text or ""}'

        return 'PROCESSING'


class Message(BaseModel):
    id: MongoId | None = Field(default=None, alias='_id')
    telegram_id: int | None = Field(default=None)
    chat_id: int
    nickname: str
    role: UserRole
    text: str | None = None
    reply: MessageReply | None = None
    media: MessageMedia | None = None
    created_at: datetime | None = None
    reactions: dict[str, list[str]] = Field(default_factory=dict)

    def __str__(self) -> str:
        return self.embedding_text

    @property
    def embedding_text(self) -> str:
        message_part = self.text or ''
        if self.media:
            message_part = f'{message_part} [{self.media.ai_format}]'

        if self.reply:
            body = f'{self.nickname} (reply: "{self.reply.ai_format}"): {message_part}'
        else:
            body = f'{self.nickname}: {message_part}'

        if self.created_at:
            return f'[{format_ts(self.created_at)}] {body}'
        return body

    @property
    def ai_format(self) -> str:
        base = self.embedding_text
        if reactions_line := self._render_reactions():
            return f'{base}\n{reactions_line}'
        return base

    @property
    def response_format(self) -> str:
        text = self.text or ''
        if self.media:
            text = f'{text} [{self.media.ai_format}]'
        if reactions_line := self._render_reactions():
            return f'{text}\n{reactions_line}'
        return text

    def _render_reactions(self) -> str | None:
        if not self.reactions:
            return None

        bot_nickname = settings.BOT_NICKNAME
        parts = []
        for emoji, nicknames in self.reactions.items():
            if not nicknames:
                continue

            bot_reacted = bot_nickname in nicknames
            others = [n for n in nicknames if n != bot_nickname]
            named = ([bot_nickname] if bot_reacted else [])
            if len(others) <= 3:
                named.extend(others)
            unnamed_count = len(nicknames) - len(named)
            if named:
                part = f'{emoji} {", ".join(named)}'
                if unnamed_count > 0:
                    part += f' +{unnamed_count}'
            else:
                part = f'{emoji} ×{len(nicknames)}'

            parts.append(part)

        return f"⤷ {' · '.join(parts)}" if parts else None


class UpdateMessage(BaseModel):
    id: MongoId
    text: str
