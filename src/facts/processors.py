from langchain_core.exceptions import OutputParserException
from langchain_core.messages import SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langsmith import traceable

from src import ai
from src.facts.models import ExtractedFact, ExtractedFacts
from src.messages.models import Message
from src.prompt_manager import prompt_manager


@traceable
async def extract_facts(new_messages: list[Message]) -> list[ExtractedFact]:
    """Runs the extraction LLM call and returns the facts it found.

    Pure: never writes. `src/facts/handlers.py:update_user_facts` owns the upsert
    and the terminal `FACT_EXTRACT` event, since it is the one that knows whether
    the writes that follow this call actually landed.
    """
    llm = ai.get_facts_model(version='v2')
    parser = PydanticOutputParser(pydantic_object=ExtractedFacts)
    llm_chain = (llm | parser).with_retry(
        retry_if_exception_type=(OutputParserException,),
        stop_after_attempt=3,
    )

    formatted_messages = '\n'.join([m.ai_format for m in new_messages])
    system_prompt = prompt_manager.get_prompt('facts', version='v2', messages=formatted_messages)

    result: ExtractedFacts = await llm_chain.ainvoke([SystemMessage(content=system_prompt)])
    return result.facts
