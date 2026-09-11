from langchain_core.messages import SystemMessage
from langsmith import traceable

from src import ai, settings
from src.characters.character import Character
from src.initiative.models import InitiativeDecision, InitiativeVerdict
from src.logs import logger
from src.models import Message
from src.prompt_manager import prompt_manager


@traceable
async def evaluate_initiative(character: Character, messages: list[Message]) -> InitiativeVerdict:
    rendered_messages = '\n'.join([
        f'#{i} ▸ {m.ai_format}'
        for i, m in enumerate(messages, start=1)
    ])

    llm = ai.get_initiative_model(version='v1')
    model_with_structure = llm.with_structured_output(InitiativeDecision)

    system_prompt = prompt_manager.get_prompt(
        'initiative',
        version='v1',
        messages=rendered_messages,
        bot_nickname=settings.BOT_NICKNAME,
        current_memory=character.memory.initiative_format() if character.memory else None,
        character_description=character.style_prompt,
    )

    try:
        evaluation_result: InitiativeDecision = await model_with_structure.ainvoke([
            SystemMessage(content=system_prompt)
        ])
    except Exception as e:
        logger.error(f'Error while evaluating initiative: {e}')
        return InitiativeVerdict(
            target_message=None,
            score=0,
            reason='Initiative evaluation error'
        )

    if not evaluation_result:
        return InitiativeVerdict(
            target_message=None,
            score=0,
            reason='Initiative evaluation empty response'
        )

    # target_index is 1-based, matching the `#N` labels the model was shown above.
    # Anything outside that range — `0` included — means 'no target', not a failed
    # run: a stray index must not throw away an otherwise good score.
    target_index = evaluation_result.target_index or 0
    target_message = messages[target_index - 1] if 0 < target_index <= len(messages) else None

    logger.info(
        f'Initiative evaluation result: '
        f'{target_message.embedding_text if target_message else "<direct>"}'
        f'|{evaluation_result.reason=}|{evaluation_result.score=}'
    )
    return InitiativeVerdict(
        target_message=target_message,
        score=evaluation_result.score,
        reason=evaluation_result.reason,
    )
