from enum import StrEnum
from typing import TYPE_CHECKING

from telegram import Bot, MessageEntity, ReplyParameters

from src.logs import event, logger
from src.messages.repository import add_bot_reaction, save_message
from src.messages.models import Message, MessageMedia, MessageMediaTypes, MessageReply, UserRole
from src.types import ReactionEmoji

if TYPE_CHECKING:
    from src.characters.character import Character


class ReplyKind(StrEnum):
    TEXT = 'text'
    STICKER = 'sticker'
    REACTION = 'reaction'


_QUOTE_PREFIX = '> '


def _utf16_len(text: str) -> int:
    return len(text.encode('utf-16-le')) // 2


def build_blockquote(text: str) -> tuple[str, list[MessageEntity]]:
    """Turns the first run of `> ` lines into one blockquote entity.

    Telegram counts entity offsets and lengths in UTF-16 code units. Only the first run
    is converted; any later `> ` line goes out as written.
    """
    lines = text.split('\n')
    start = next((i for i, line in enumerate(lines) if line.startswith(_QUOTE_PREFIX)), None)
    if start is None:
        return text, []

    end = start
    while end < len(lines) and lines[end].startswith(_QUOTE_PREFIX):
        end += 1

    quoted = '\n'.join(line.removeprefix(_QUOTE_PREFIX) for line in lines[start:end])
    before = ''.join(f'{line}\n' for line in lines[:start])
    after = ''.join(f'\n{line}' for line in lines[end:])
    entity = MessageEntity(
        type=MessageEntity.BLOCKQUOTE,
        offset=_utf16_len(before),
        length=_utf16_len(quoted),
    )
    return f'{before}{quoted}{after}', [entity]


class Replier:
    def __init__(self, bot: Bot, character: Character, chat_id: int, target: Message | None):
        self.bot = bot
        self.character = character
        self.chat_id = chat_id
        self.target_message = target
        # Kinds actually delivered to Telegram, appended after each call succeeds. The
        # initiative path reads it to decide whether a daily slot was really spent.
        self.delivered: list[ReplyKind] = []

    async def reply_message(self, text: str) -> Message:
        text, entities = build_blockquote(text)
        # The full text is chat content, not diagnostic metadata -- it moves to its own
        # DEBUG line so an INFO-level stream never carries a reply body.
        logger.info(
            'Replying to user message',
            extra=event(
                'REPLY_SENT',
                kind=ReplyKind.TEXT,
                text_len=len(text),
                quote_like=bool(entities),
            ),
        )
        logger.debug('Reply text', extra=event('REPLY_TEXT', text=text))

        kwargs = {'entities': entities} if entities else {}
        reply = await self.bot.send_message(
            chat_id=self.chat_id,
            reply_parameters=self._get_reply_params(),
            text=text,
            **kwargs,
        )
        self.delivered.append(ReplyKind.TEXT)
        return await self._save_message(reply.message_id, text)

    async def reply_sticker(self, file_id: str, unique_id: str) -> Message:
        logger.info(
            'Replying to user message',
            extra=event('REPLY_SENT', kind=ReplyKind.STICKER, sticker_id=unique_id),
        )
        reply = await self.bot.send_sticker(
            chat_id=self.chat_id,
            reply_parameters=self._get_reply_params(),
            sticker=file_id,
        )
        self.delivered.append(ReplyKind.STICKER)
        return await self._save_message(
            reply.message_id,
            media=MessageMedia(
                media_id=file_id,
                unique_id=unique_id,
                type=MessageMediaTypes.STICKER,
            ),
        )

    async def reply_reaction(self, emoji: ReactionEmoji, is_big: bool = False) -> bool:
        if not self.target_message or not self.target_message.telegram_id:
            raise ValueError('Target message is not set for reply reaction')

        result = await self.bot.set_message_reaction(
            chat_id=self.chat_id,
            message_id=self.target_message.telegram_id,
            reaction=emoji,
            is_big=is_big,
        )
        if not result:
            return False

        self.delivered.append(ReplyKind.REACTION)
        # Logged only on confirmed delivery -- otherwise a rejected reaction would
        # still count as sent in the REPLY_SENT Axiom metric.
        logger.info(
            'Setting reaction', extra=event('REPLY_SENT', kind=ReplyKind.REACTION, emoji=str(emoji))
        )
        await add_bot_reaction(self.target_message, self.character.nickname, str(emoji))
        return True

    def _get_reply_params(self) -> ReplyParameters | None:
        if self.target_message and self.target_message.telegram_id:
            return ReplyParameters(
                message_id=self.target_message.telegram_id,
            )

        return None

    async def _save_message(
        self,
        message_id: int | None,
        text: str | None = None,
        media: MessageMedia | None = None,
    ) -> Message:
        reply = None
        if self.target_message:
            reply = MessageReply(
                telegram_id=self.target_message.telegram_id,
                text=self.target_message.text,
                nickname=self.target_message.nickname,
                media=self.target_message.media,
            )

        message = Message(
            telegram_id=message_id,
            chat_id=self.chat_id,
            nickname=self.character.nickname,
            character_code=self.character.code,
            role=UserRole.AI,
            text=text,
            media=media,
            reply=reply,
        )
        await save_message(message)
        return message
