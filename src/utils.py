from __future__ import annotations

from datetime import datetime

from src.const import TIMEZONE_ALMATY

TIMESTAMP_FORMAT = '%Y-%m-%d %H:%M'


def format_ts(value: datetime) -> str:
    """Renders a timestamp the way every prompt in this project expects it.

    The single producer of the `ГГГГ-ММ-ДД ЧЧ:ММ` format the memory prompt documents.
    `src/memory/decay.py` stamps `DecayRecord.born` through this same helper, so a
    sidecar age and a message timestamp can never drift apart.
    """
    return value.astimezone(TIMEZONE_ALMATY).strftime(TIMESTAMP_FORMAT)
