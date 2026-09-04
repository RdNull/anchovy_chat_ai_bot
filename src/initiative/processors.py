from langchain_core.messages import SystemMessage

from src import ai
from src.characters.character import Character
from src.initiative.models import InitiativeDecision, InitiativeVerdict
from src.logs import logger
from src.models import Message, UserRole
from src.prompt_manager import prompt_manager


async def evaluate_initiative(character: Character, messages: list[Message]) -> InitiativeVerdict:
    rendered_messages = '\n'.join([
        f'#{i} ▸ {m.ai_format if m.role == UserRole.USER else m.response_format}'
        for i, m in enumerate(messages)
    ])

    llm = ai.get_initiative_model(version='v1')
    model_with_structure = llm.with_structured_output(InitiativeDecision)

    system_prompt = prompt_manager.get_prompt(
        'initiative',
        version='v1',
        messages=rendered_messages,
        current_memory=character.memory.initiative_format(),
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

    target_message = None
    if evaluation_result.target_index and evaluation_result.target_index < len(messages):
        target_message = messages[evaluation_result.target_index]

    return InitiativeVerdict(
        target_message=target_message,
        score=evaluation_result.score,
        reason=evaluation_result.reason,
    )
