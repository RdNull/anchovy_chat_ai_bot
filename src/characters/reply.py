from typing import TYPE_CHECKING

from telegram import Update

from src import settings
from src.logs import logger
from src.messages.repository import save_message
from src.models import Message, MessageReply, UserRole
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

    async def reply_reaction(self, emoji: ReactionEmoji, is_big: bool = False):
        logger.info(f'Setting reaction with emoji: {emoji}')
        result = await self.update.message.set_reaction(emoji, is_big=is_big)
        if not result:
            raise ValueError(f'Failed to set reaction with emoji {emoji}')

        return await self._save_message(message_id=None, text=emoji)

    async def _save_message(self, message_id: int | None, text: str) -> Message:
        message = Message(
            telegram_id=message_id,
            chat_id=self.chat_id,
            nickname=f'{settings.BOT_NICKNAME}({self.character.name})',
            role=UserRole.AI,
            text=text,
            reply=MessageReply(
                telegram_id=self.user_message.telegram_id,
                text=self.user_message.text,
                nickname=self.user_message.nickname,
                media=self.user_message.media
            ),
        )
        await save_message(message)
        return message
