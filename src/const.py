import datetime as dt

from telegram.constants import ReactionEmoji

TIMEZONE_ALMATY = dt.timezone(offset=dt.timedelta(hours=5))

# fmt: off
ALLOWED_REACTIONS = {
    '🤡', '🤨', '💩', '🤮', '🖕', '😐', '🤣', '💯', '🌚', '🤝', '😭', '🗿', '👍', '👎',
    '🔥', '❤', '👀', '🤓', '👾',
}
# fmt: on

assert ALLOWED_REACTIONS.issubset(ReactionEmoji)
