from datetime import datetime, timedelta, timezone

from langchain_core.messages import SystemMessage
from langsmith import traceable

from src import ai
from src.facts.handlers import decay_facts, upsert_fact
from src.logs import logger
from src.models import ExtractedFacts, Message
from src.prompt_manager import prompt_manager


@traceable
async def extract_facts(new_messages: list[Message]):
    formatted_messages = "\n".join([m.ai_format for m in new_messages])
    llm = ai.get_facts_model(version='v1')
    model_with_structure = llm.with_structured_output(ExtractedFacts)

    system_prompt = prompt_manager.get_prompt(
        'facts', version='v1', messages=formatted_messages
    )

    try:
        result: ExtractedFacts = await model_with_structure.ainvoke([
            SystemMessage(content=system_prompt)
        ])

        for fact in result.facts:
            await upsert_fact(fact.nickname, fact.text, fact.confidence)

        logger.info(f"Extracted and saved {len(result.facts)} facts from messages")
    except Exception as e:
        logger.error(f"Error extracting facts from messages: {e}", exc_info=True)


async def decay_all_facts(decay_amount: float = 0.1) -> None:
    one_week_ago_ts = datetime.now(timezone.utc) - timedelta(weeks=1)
    await decay_facts(one_week_ago_ts, decay_amount)
