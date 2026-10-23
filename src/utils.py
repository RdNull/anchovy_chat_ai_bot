from __future__ import annotations

import re
from datetime import datetime

from src import settings
from src.const import TIMEZONE_ALMATY

TIMESTAMP_FORMAT = '%Y-%m-%d %H:%M'


def format_ts(value: datetime) -> str:
    """Renders a timestamp the way every prompt in this project expects it.

    The single producer of the `ГГГГ-ММ-ДД ЧЧ:ММ` format the memory prompt documents.
    `src/memory/decay.py` stamps `DecayRecord.born` through this same helper, so a
    sidecar age and a message timestamp can never drift apart.
    """
    return value.astimezone(TIMEZONE_ALMATY).strftime(TIMESTAMP_FORMAT)


def is_bot_nickname(nickname: str) -> bool:
    """The bot's own nick in any form, with or without `@`.

    Matches the bare `BOT_NICKNAME`, the tagged `BOT_NICKNAME[<code>]` and the legacy
    `BOT_NICKNAME(<name>)`. Read live from `settings`, like the blackbox pattern it mirrors.
    """
    pattern = rf'{re.escape(settings.BOT_NICKNAME)}(\[.*\]|\(.*\))?'
    return re.fullmatch(pattern, nickname.removeprefix('@')) is not None
