from typing import Literal

from src.const import ALLOWED_REACTIONS

ReactionEmoji = Literal[tuple(sorted(ALLOWED_REACTIONS))]