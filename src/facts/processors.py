from langchain_core.exceptions import OutputParserException
from langchain_core.messages import SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langsmith import traceable

from src import ai, settings
from src.facts.models import FactOp, FactOps, UserFact
from src.messages.models import Message
from src.prompt_manager import prompt_manager


def number_facts(existing: dict[str, list[UserFact]]) -> dict[str, UserFact]:
    """Positional ids (`f1`, `f2`…) across the whole block, in nickname then fact order.

    The prompt renders from this map and the handler resolves targets through it, so the
    numbering the model saw is the numbering the ops are checked against.
    """
    numbered = {}
    for facts in existing.values():
        for fact in facts:
            numbered[f'f{len(numbered) + 1}'] = fact

    return numbered


def render_existing_facts(existing: dict[str, list[UserFact]]) -> str:
    lines = []
    index = 0
    for nickname, facts in existing.items():
        if not facts:
            continue

        lines.append(f'@{nickname}:')
        for fact in facts:
            index += 1
            lines.append(f'f{index} [{fact.kind.value}] {fact.text}')

    return '\n'.join(lines) if lines else '(пока нет)'


@traceable
async def extract_facts(
    new_messages: list[Message],
    existing: dict[str, list[UserFact]],
) -> list[FactOp]:
    """Runs the extraction LLM call and returns the operations it asked for.

    Pure: never writes. `src/facts/handlers.py:update_user_facts` owns applying the ops
    and the terminal `FACT_EXTRACT` event, since it is the one that knows whether the
    writes that follow this call actually landed.
    """
    llm = ai.get_facts_model(version='v3')
    parser = PydanticOutputParser(pydantic_object=FactOps)
    llm_chain = (llm | parser).with_retry(
        retry_if_exception_type=(OutputParserException,),
        stop_after_attempt=3,
    )

    formatted_messages = '\n'.join([m.ai_format for m in new_messages])
    system_prompt = prompt_manager.get_prompt(
        'facts',
        version='v3',
        facts=render_existing_facts(existing),
        messages=formatted_messages,
        bot_nickname=settings.BOT_NICKNAME,
    )

    result: FactOps = await llm_chain.ainvoke([SystemMessage(content=system_prompt)])
    return result.ops
