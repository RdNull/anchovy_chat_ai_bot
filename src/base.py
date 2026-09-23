from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel as _BaseModel, BeforeValidator, ConfigDict

from src.const import TIMEZONE_ALMATY

MongoId = Annotated[str, BeforeValidator(lambda x: str(x))]


class BaseModel(_BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True, arbitrary_types_allowed=True)


TIMESTAMP_FORMAT = '%y-%m-%d %H:%M'


def format_ts(value: datetime) -> str:
    """Renders a timestamp the way every prompt in this project expects it.

    The single producer of the `ГГ-ММ-ДД ЧЧ:ММ` format the memory prompt documents.
    `src/memory/decay.py` stamps `DecayRecord.born` through this same helper, so a
    sidecar age and a message timestamp can never drift apart.
    """
    return value.astimezone(TIMEZONE_ALMATY).strftime(TIMESTAMP_FORMAT)
