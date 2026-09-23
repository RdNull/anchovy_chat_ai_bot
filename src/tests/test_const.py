"""`ALLOWED_REACTIONS` and its hand-mirror in the reply eval provider.

`evals/models/chat/v8-gemini.yaml` restates the enum by hand (see `.claude/rules/models.md` —
`evals/` must not import Python), so nothing catches the two drifting apart except a test
that reads both.
"""

from pathlib import Path

import yaml

from src.const import ALLOWED_REACTIONS

_ROOT = Path(__file__).resolve().parents[2]
_EVAL_PROVIDER = _ROOT / 'evals' / 'models' / 'chat' / 'v8-gemini.yaml'


def test_allowed_reactions_is_the_18_emoji_set():
    assert {
        '🤡',
        '🤨',
        '💩',
        '🤮',
        '🖕',
        '😐',
        '🤣',
        '💯',
        '🌚',
        '🤝',
        '😭',
        '🗿',
        '👍',
        '👎',
        '🔥',
        '❤',
        '👀',
        '🤓',
    } == ALLOWED_REACTIONS


def test_allowed_reactions_is_a_subset_of_telegram_reaction_emoji():
    from telegram.constants import ReactionEmoji

    assert ALLOWED_REACTIONS.issubset(ReactionEmoji)


def test_eval_provider_enum_matches_allowed_reactions():
    config = yaml.safe_load(_EVAL_PROVIDER.read_text())
    tools = config['config']['tools']
    set_reaction_tool = next(t for t in tools if t['function']['name'] == 'set_reaction')
    enum = set_reaction_tool['function']['parameters']['properties']['emoji']['enum']

    assert enum == sorted(ALLOWED_REACTIONS)
