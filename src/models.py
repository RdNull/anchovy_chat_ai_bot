from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel as _BaseModel, BeforeValidator, ConfigDict

MongoId = Annotated[str, BeforeValidator(str)]


class BaseModel(_BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True, arbitrary_types_allowed=True)
