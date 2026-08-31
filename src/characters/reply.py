from typing import TYPE_CHECKING

from telegram import Update

from src import settings
from src.logs import logger
from src.messages.repository import add_bot_reaction, save_message
from src.models import Message, MessageMedia, MessageReply, UserRole
from src.types import ReactionEmoji

if TYPE_CHECKING:
    from src.characters.character import Character


class Replier:
    def __init__(self, character: Character, update: Update, user_message: Message):
        self.chat_id = update.effective_chat.id
        self.update = update
        self.user_message = user_message
        self.character = character

    async def reply_message(self, text: str) -> Message:
        logger.info(f'Replying to user message with text: {text}')
        reply = await self.update.message.reply_text(text)
        return await self._save_message(reply.message_id, text)

    async def reply_sticker(self, file_id: str, unique_id: str) -> Message:
        logger.info(f'Replying to user message with sticker: {unique_id}')
        reply = await self.update.message.reply_sticker(file_id)
        return await self._save_message(
            reply.message_id,
            media=MessageMedia(media_id=file_id, unique_id=unique_id, is_sticker=True),
        )

    async def reply_reaction(self, emoji: ReactionEmoji, is_big: bool = False):
        logger.info(f'Setting reaction with emoji: {emoji}')
        result = await self.update.message.set_reaction(emoji, is_big=is_big)
        if not result:
            raise ValueError(f'Failed to set reaction with emoji {emoji}')

        await add_bot_reaction(self.user_message, settings.BOT_NICKNAME, str(emoji))

    async def _save_message(
        self,
        message_id: int | None,
        text: str | None = None,
        media: MessageMedia | None = None,
    ) -> Message:
        message = Message(
            telegram_id=message_id,
            chat_id=self.chat_id,
            nickname=f'{settings.BOT_NICKNAME}({self.character.name})',
            role=UserRole.AI,
            text=text,
            media=media,
            reply=MessageReply(
                telegram_id=self.user_message.telegram_id,
                text=self.user_message.text,
                nickname=self.user_message.nickname,
                media=self.user_message.media
            ),
        )
        await save_message(message)
        return message
