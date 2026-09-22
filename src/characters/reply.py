from typing import TYPE_CHECKING

from telegram import Bot, ReplyParameters

from src import settings
from src.logs import event, logger
from src.messages.repository import add_bot_reaction, save_message
from src.models import Message, MessageMedia, MessageMediaTypes, MessageReply, UserRole
from src.types import ReactionEmoji

if TYPE_CHECKING:
    from src.characters.character import Character


class Replier:
    def __init__(self, bot: Bot, character: Character, chat_id: int, target: Message | None):
        self.bot = bot
        self.character = character
        self.chat_id = chat_id
        self.target_message = target

    async def reply_message(self, text: str) -> Message:
        # The full text is chat content, not diagnostic metadata -- it moves to its own
        # DEBUG line so an INFO-level stream never carries a reply body.
        logger.info('Replying to user message', extra=event('REPLY_SENT', kind='text', text_len=len(text)))
        logger.debug('Reply text', extra=event('REPLY_TEXT', text=text))

        reply = await self.bot.send_message(
            chat_id=self.chat_id,
            reply_parameters=self._get_reply_params(),
            text=text,
        )
        return await self._save_message(reply.message_id, text)

    async def reply_sticker(self, file_id: str, unique_id: str) -> Message:
        logger.info(
            'Replying to user message',
            extra=event('REPLY_SENT', kind='sticker', sticker_id=unique_id),
        )
        reply = await self.bot.send_sticker(
            chat_id=self.chat_id,
            reply_parameters=self._get_reply_params(),
            sticker=file_id,
        )
        return await self._save_message(
            reply.message_id,
            media=MessageMedia(
                media_id=file_id, unique_id=unique_id, type=MessageMediaTypes.STICKER,
            ),
        )

    async def reply_reaction(self, emoji: ReactionEmoji, is_big: bool = False) -> bool:
        if not self.target_message or not self.target_message.telegram_id:
            raise ValueError('Target message is not set for reply reaction')

        result = await self.bot.set_message_reaction(
            chat_id=self.chat_id,
            message_id=self.target_message.telegram_id,
            reaction=emoji,
            is_big=is_big
        )
        if not result:
            return False

        # Logged only on confirmed delivery -- otherwise a rejected reaction would
        # still count as sent in the REPLY_SENT Axiom metric.
        logger.info('Setting reaction', extra=event('REPLY_SENT', kind='reaction', emoji=str(emoji)))
        await add_bot_reaction(self.target_message, settings.BOT_NICKNAME, str(emoji))
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
                media=self.target_message.media
            )

        message = Message(
            telegram_id=message_id,
            chat_id=self.chat_id,
            nickname=f'{settings.BOT_NICKNAME}({self.character.name})',
            role=UserRole.AI,
            text=text,
            media=media,
            reply=reply,
        )
        await save_message(message)
        return message
