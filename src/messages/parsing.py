from telegram import Message as TgMessage, PhotoSize, Sticker
from telegram._files._basemedium import _BaseMedium

from src.models import Message, MessageReply, UserRole
from .repository import get_message_media_data


async def parse_user_message(update) -> Message | None:
    if not update.message:
        return None

    reply = None
    if update.message.reply_to_message:
        reply_msg = update.message.reply_to_message
        reply_nickname = reply_msg.from_user.username or reply_msg.from_user.first_name if reply_msg.from_user else 'unknown'
        reply_media = None
        if reply_medium := _get_message_medium(reply_msg):
            reply_media = await get_message_media_data(
                reply_medium.file_id, reply_medium.file_unique_id, _as_sticker(reply_medium),
            )

        reply_text = reply_msg.text or reply_msg.caption
        reply = MessageReply(
            telegram_id=reply_msg.message_id,
            text=reply_text,
            nickname=reply_nickname,
            media=reply_media,
        )

    user_nickname = update.message.from_user.username or update.message.from_user.first_name
    media = None
    if medium := _get_message_medium(update.message):
        media = await get_message_media_data(
            medium.file_id, medium.file_unique_id, _as_sticker(medium),
        )

    message_text = update.message.text or update.message.caption
    return Message(
        chat_id=update.effective_chat.id,
        telegram_id=update.message.message_id,
        role=UserRole.USER,
        text=message_text,
        reply=reply,
        nickname=user_nickname,
        media=media
    )


def _as_sticker(medium: _BaseMedium) -> Sticker | None:
    """Narrows a medium to a `Sticker`, or None if it is a photo or an animation.

    Done at the call site rather than in `_get_message_medium` on purpose: that
    function already returns the raw PTB object, and changing its return type to
    carry the distinction would buy nothing.
    """
    return medium if isinstance(medium, Sticker) else None


def _get_message_medium(tg_message: TgMessage) -> _BaseMedium | None:
    if sticker := tg_message.sticker:
        return sticker

    if photo_sizes := tg_message.photo:  # type: tuple[PhotoSize, ...]
        # selecting the biggest photo size that is less than 300_000 pixels ("magic number")
        photo_size = next(s for s in reversed(photo_sizes) if s.height * s.width <= 300_000)
        return photo_size

    if animation := tg_message.animation:
        if animation.duration <= 10:  # in seconds; long gifs will be ignored
            return animation

    return None
