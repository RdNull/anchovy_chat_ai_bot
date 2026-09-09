import asyncio

from telegram.constants import ChatAction

from src import settings
from src.characters.character import Character
from src.characters.reply import Replier
from src.initiative.models import InitiativeRun, InitiativeVerdict
from src.initiative.policies import decide, pre_check
from src.initiative.processors import evaluate_initiative
from src.initiative.repository import get_last_initiative_run, save_initiative_run
from src.logs import logger
from src.memory.repository import get_last_memory
from src.messages.repository import fetch_last_messages
from src.messages.utils import get_chat_character, send_chat_action
from src.models import Message
from src.running_app import get_bot

# `run_context_checks` is a detached task per message, so the watermark's
# read-then-write is not atomic on its own: two messages arriving together would both
# read the same watermark, claim the same window and answer it twice. Module-level and
# shared by every chat, like `CHAT_CONTEXT_LOCK` — the critical section is short and
# ends before the LLM call.
INITIATIVE_RUN_LOCK = asyncio.Lock()


async def run_initiative_checks(chat_id: int):
    if not settings.INITIATIVE_CHECKS_ENABLED:
        return

    logger.info(f'Running initiative checks for chat {chat_id}')
    messages = await _claim_window(chat_id)
    if not messages:
        return

    last_memory = await get_last_memory(chat_id)
    character: Character = await get_chat_character(chat_id=chat_id, memory=last_memory)
    evaluation = await evaluate_initiative(character, messages)
    if not await decide(evaluation):
        logger.info(
            f'Initiative run skipped for chat {chat_id}; {evaluation.score=}, {evaluation.reason=}'
        )
        return

    if not settings.INITIATIVE_ENABLED:
        logger.info(
            f'Initiative run for chat {chat_id} would reply (dry run); '
            f'{evaluation.score=}|{evaluation.reason=}'
        )
        return

    asyncio.create_task(
        _run_initiative_reply(chat_id=chat_id, character=character, evaluation=evaluation)
    )


async def _claim_window(chat_id: int) -> list[Message]:
    """Reads the pending window and advances the watermark under a single lock.

    Returns the claimed messages, or an empty list when the pre-checks say no.
    """
    async with INITIATIVE_RUN_LOCK:
        last_initiative_run = await get_last_initiative_run(chat_id)
        messages = await _get_messages(chat_id, last_initiative_run)

        if not await pre_check(chat_id, messages):
            logger.info(f'Initiative run pre-checks failed for chat {chat_id}')
            return []

        logger.info(f'Triggering initiative run for chat {chat_id} {len(messages)}')
        await save_initiative_run(chat_id, last_message_time=messages[-1].created_at)

    return messages


async def _get_messages(chat_id: int, last_initiative_run: InitiativeRun | None) -> list[Message]:
    if last_initiative_run:
        # Resuming: oldest-first from the watermark, so a backlog past the cap is
        # deferred to the next run rather than skipped.
        return await fetch_last_messages(
            chat_id,
            size=settings.INITIATIVE_RUN_MESSAGES_MAX_SIZE,
            from_date=last_initiative_run.last_message_time,
            sort_order=1,
        )

    # No prior run to resume from — anchor on the most recent messages rather than
    # the chat's oldest history, which could be an arbitrarily old backlog.
    return await fetch_last_messages(
        chat_id,
        size=settings.INITIATIVE_RUN_MESSAGES_MAX_SIZE,
        sort_order=-1,
    )


async def _run_initiative_reply(
    chat_id: int, character: Character, evaluation: InitiativeVerdict,
):
    logger.info(f'Initiative run triggered for chat {chat_id}')
    await send_chat_action(chat_id, ChatAction.TYPING)
    # No `character.memory = ...` here: `get_character` hands out a shared singleton,
    # and this runs as a detached task, so re-stamping it from a background task can
    # land another chat's memory in a reply already being built. `run_initiative_checks`
    # has already passed this chat's snapshot into `get_chat_character`.
    bot = get_bot()
    replier = Replier(
        bot=bot,
        character=character,
        chat_id=chat_id,
        target=evaluation.target_message,
    )

    # reload messages to fetch messages that might be sent in-between initiative evaluation
    last_messages = await fetch_last_messages(chat_id, size=settings.LAST_MESSAGES_SIZE)
    await character.respond(replier, _with_target(last_messages, evaluation.target_message))


def _with_target(messages: list[Message], target: Message | None) -> list[Message]:
    """Keeps the evaluated target inside the reply window.

    The evaluation window is the wider of the two, so a target picked from its older
    half can fall outside `LAST_MESSAGES_SIZE`: the character would get no `[TARGET]`
    marker while the reply still quotes that message. It is older than everything in
    the window by construction, so it goes at the front.
    """
    if not target or not target.id or any(m.id == target.id for m in messages):
        return messages

    return [target, *messages]
