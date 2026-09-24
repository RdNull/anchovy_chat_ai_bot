import asyncio
import random

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message as TgMessage,
    ReactionTypeEmoji,
    Update,
)
from telegram.constants import ChatAction
from telegram.ext import CallbackContext, ContextTypes

from src.characters.registry import (
    CHARACTERS,
    get_available_characters,
    get_chat_character,
    set_chat_character,
)
from src.chat_settings import repository as chat_settings_repository
from src.log_context import log_context
from src.logs import event, logger
from src.media.handlers import handle_media_message
from src.messages.models import UpdateMessage
from .parsing import parse_user_message
from .repository import (
    get_message_by_tg_id,
    save_message,
    update_message,
    update_message_reactions,
)
from .followups import run_followups
from .response import generate_answer
from .utils import escape_markdown_v2, owner_only, restricted, send_action


async def start(update: Update, _context: CallbackContext):
    # chat_id/user_id are already bound by ContextBindingApplication.process_update
    # (src/bot.py) by the time this callback runs.
    logger.info('Command handled', extra=event('COMMAND_HANDLED', command='start'))
    await update.message.reply_text('Дарова, чорт!')


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Registered via `add_error_handler`. A handler-raised exception is routed here from
    # within the same `process_update` call (or the same task, for a non-blocking handler),
    # so it normally already carries the update's bound chat_id/user_id -- but PTB can also
    # call this for a failure with no associated update at all (a polling-level error), which
    # never went through process_update and so never bound anything. Binding again here is a
    # no-op in the common case (log_context leaves an existing value alone) and the fallback
    # for the uncommon one; `update` is typed `object` because it may not be an `Update`.
    chat_id = getattr(getattr(update, 'effective_chat', None), 'id', None)
    user_id = getattr(getattr(update, 'effective_user', None), 'id', None)
    with log_context(chat_id=chat_id, user_id=user_id):
        logger.error(
            'Exception while handling an update',
            exc_info=context.error,
            extra=event('UPDATE_FAILED'),
        )


@restricted
async def info(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info('Command handled', extra=event('COMMAND_HANDLED', command='info'))
    character = await get_chat_character(chat_id)
    name = escape_markdown_v2(character.display_name)
    description = escape_markdown_v2(character.description)
    await update.message.reply_text(
        f'*Персонаж:* {name}\n*Описание:* {description}', parse_mode='MarkdownV2'
    )


@restricted
async def list_characters(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    logger.info('Command handled', extra=event('COMMAND_HANDLED', command='list'))
    available = await get_available_characters(update.effective_chat.id)
    keyboard = [
        [InlineKeyboardButton(character.display_name, callback_data=f'select_char:{code}')]
        for code, character in available.items()
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('Выберите персонажа:', reply_markup=reply_markup)


@restricted
async def select_character(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = update.effective_chat.id

    character_code = query.data.split(':')[1]
    available = await get_available_characters(chat_id)
    if character_code not in available:
        # A stale keyboard: the character was removed from the allow list, or never was one.
        await query.answer(text='Этот персонаж тут больше недоступен', show_alert=False)
        return

    await query.answer()
    logger.info(
        'Character set',
        extra=event('CHARACTER_SET', character=character_code, source='select'),
    )
    await set_chat_character(chat_id, character_code)
    character = CHARACTERS[character_code]

    await query.edit_message_text(f'Персонаж изменён на: {character.display_name}')


@restricted
async def random_character(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    available = await get_available_characters(chat_id)
    character_code = random.choice(list(available.keys()))
    logger.info(
        'Character set',
        extra=event('CHARACTER_SET', character=character_code, source='random'),
    )
    await set_chat_character(chat_id, character_code)
    character = CHARACTERS[character_code]

    await update.message.reply_text(f'Выпал персонаж: {character.display_name}')


_ACCESS_DONE = 'char_access_done'
_ACCESS_PREFIX = 'char_access:'


def _private_characters() -> dict:
    return {code: c for code, c in CHARACTERS.items() if not c.public}


def _access_keyboard(allowed: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for code, character in _private_characters().items():
        mark = '✅' if code in allowed else '⬜'
        button = InlineKeyboardButton(
            f'{mark} {character.display_name}', callback_data=f'{_ACCESS_PREFIX}{code}'
        )
        rows.append([button])

    rows.append([InlineKeyboardButton('Готово', callback_data=_ACCESS_DONE)])
    return InlineKeyboardMarkup(rows)


@owner_only
async def manage_character_access(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    logger.info('Command handled', extra=event('COMMAND_HANDLED', command='characters'))
    if not _private_characters():
        await update.message.reply_text('Приватных персонажей нет')
        return

    allowed = await chat_settings_repository.get_allowed_characters(update.effective_chat.id)
    await update.message.reply_text(
        'Какие приватные персонажи доступны в этом чате:',
        reply_markup=_access_keyboard(allowed),
    )


@owner_only
async def character_access(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = update.effective_chat.id

    if query.data == _ACCESS_DONE:
        await query.answer()
        allowed = await chat_settings_repository.get_allowed_characters(chat_id)
        names = [c.display_name for code, c in _private_characters().items() if code in allowed]
        summary = ', '.join(names) if names else 'никто'
        await query.edit_message_text(f'Приватные персонажи в этом чате: {summary}')
        return

    character_code = query.data.removeprefix(_ACCESS_PREFIX)
    if character_code not in _private_characters():
        await query.answer(text='Такого приватного персонажа нет', show_alert=False)
        return

    await query.answer()
    allowed = await chat_settings_repository.toggle_allowed_character(chat_id, character_code)
    await query.edit_message_reply_markup(reply_markup=_access_keyboard(allowed))


@restricted
async def handle_mention(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info('Bot mentioned or replied to', extra=event('MESSAGE_MENTION'))
    await generate_answer(update, context)


@restricted
async def handle_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_message = await parse_user_message(update)
    if not user_message:
        return

    # Promoted from DEBUG: intake volume per chat is currently not queryable at all.
    logger.info(
        'Message received',
        extra=event(
            'MESSAGE_RECEIVED',
            nickname=user_message.nickname,
            has_media=bool(user_message.media),
            text_len=len(user_message.text or ''),
        ),
    )

    await save_message(user_message)
    # Dispatched for any media, not just PENDING: the pipeline has its own skip checks,
    # and it has to see already-described media too so `_backfill_sticker` can retype a
    # sticker whose row predates the sticker unit. Gating on PENDING here would leave
    # that backfill unreachable on the path most stickers actually arrive by.
    if user_message.media:
        asyncio.create_task(handle_media_message(user_message, context))

    asyncio.create_task(run_followups(chat_id))


@restricted
@send_action(ChatAction.TYPING)
async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info('Handling media message', extra=event('MEDIA_RECEIVED'))
    user_message = await parse_user_message(update)
    if not user_message:
        return

    if user_message.media:
        await handle_media_message(user_message, context)
        await generate_answer(update, context)


@restricted
async def handle_message_reaction(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    logger.info('Handling message reaction', extra=event('REACTION_RECEIVED'))
    reaction_update = update.message_reaction
    if not reaction_update or not reaction_update.user:
        return

    message = await get_message_by_tg_id(reaction_update.chat.id, reaction_update.message_id)
    if not message:
        return

    user_nickname = reaction_update.user.username or reaction_update.user.first_name
    old_emojis = [r.emoji for r in reaction_update.old_reaction if isinstance(r, ReactionTypeEmoji)]
    new_emojis = [r.emoji for r in reaction_update.new_reaction if isinstance(r, ReactionTypeEmoji)]
    await update_message_reactions(message, user_nickname, old_emojis, new_emojis)


@restricted
async def handle_message_edit(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    logger.info('Handling message edit', extra=event('MESSAGE_EDIT_RECEIVED'))
    if not update.edited_message:
        return

    edited_tg_message: TgMessage = update.edited_message

    new_text = edited_tg_message.text or edited_tg_message.caption
    if not new_text:
        return

    message = await get_message_by_tg_id(
        chat_id=edited_tg_message.chat_id,
        telegram_id=edited_tg_message.message_id,
    )
    if not message:
        return

    await update_message(
        UpdateMessage(
            id=message.id,
            text=new_text,
        )
    )
