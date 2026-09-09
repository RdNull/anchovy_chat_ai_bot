import asyncio

from src import settings
from src.characters.character import Character
from src.characters.reply import Replier
from src.initiative.models import InitiativeRun, InitiativeVerdict
from src.initiative.policies import decide, pre_check
from src.initiative.processors import evaluate_initiative
from src.initiative.repository import get_last_initiative_run
from src.logs import logger
from src.memory.repository import get_last_memory
from src.messages.repository import get_messages_count, get_messages_count_since
from src.messages.response import get_last_messages
from src.messages.utils import get_chat_character
from src.models import Message
from src.running_app import get_bot


async def run_initiative_checks(chat_id: int):
    last_initiative_run = await get_last_initiative_run(chat_id)

    messages_count = await _get_message_count_since_last_run(chat_id, last_initiative_run)
    if messages_count < settings.INITIATIVE_TRIGGER_SIZE:
        return

    messages = await _get_messages(chat_id, last_initiative_run)
    if not await pre_check(chat_id):
        return

    character: Character = await get_chat_character(chat_id=chat_id)
    logger.info(
        f'Triggering initiative run for chat {chat_id} (count since last: {messages_count})'
    )
    evaluation = await evaluate_initiative(character, messages)
    if not await decide(evaluation):
        logger.info(f'Initiative run skipped for chat {chat_id}; {evaluation.score=}')
        return

    asyncio.create_task(
        _run_initiative_reply(chat_id=chat_id, character=character, evaluation=evaluation)
    )


async def _get_message_count_since_last_run(
    chat_id: int, last_initiative_run: InitiativeRun | None,
) -> int:
    if last_initiative_run:
        return await get_messages_count_since(
            chat_id, last_initiative_run.last_message_time.timestamp()
        )

    return await get_messages_count(chat_id)


async def _get_messages(chat_id: int, last_initiative_run: InitiativeRun | None) -> list[Message]:
    from_date = last_initiative_run.last_message_time if last_initiative_run else None
    return await get_last_messages(
        chat_id,
        size=settings.INITIATIVE_RUN_MESSAGES_MAX_SIZE,
        from_date=from_date,
        sort_order=1,
    )


async def _run_initiative_reply(
    chat_id: int, character: Character, evaluation: InitiativeVerdict,
):
    logger.info(f'Initiative run triggered for chat {chat_id}')
    character.memory = await get_last_memory(chat_id)
    bot = get_bot()
    replier = Replier(
        bot=bot,
        character=character,
        chat_id=chat_id,
        target=evaluation.target_message,
    )

    # reload messages to fetch messages that might be sent in-between initiative evaluation
    last_messages = await get_last_messages(chat_id, size=settings.LAST_MESSAGES_SIZE)
    await character.respond(replier, last_messages)
