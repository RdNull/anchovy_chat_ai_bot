from langchain_core.exceptions import OutputParserException
from langchain_core.messages import SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langsmith import traceable

from src import ai
from src.facts.handlers import upsert_fact
from src.logs import logger
from src.models import ExtractedFacts, Message
from src.prompt_manager import prompt_manager


@traceable
async def extract_facts(new_messages: list[Message]):
    llm = ai.get_facts_model(version='v2')
    parser = PydanticOutputParser(pydantic_object=ExtractedFacts)
    llm_chain = (llm | parser).with_retry(
        retry_if_exception_type=(OutputParserException,),
        stop_after_attempt=3,
    )

    formatted_messages = '\n'.join([m.ai_format for m in new_messages])
    system_prompt = prompt_manager.get_prompt(
        'facts', version='v2', messages=formatted_messages
    )

    try:
        result: ExtractedFacts = await llm_chain.ainvoke([
            SystemMessage(content=system_prompt)
        ])

        for fact in result.facts:
            await upsert_fact(fact.nickname, fact.text, fact.confidence)

        logger.info(f'Extracted and saved {len(result.facts)} facts from messages')
    except Exception as e:
        logger.error(f'Error extracting facts from messages: {e}', exc_info=True)
