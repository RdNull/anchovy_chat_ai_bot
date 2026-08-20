"""The normalized keyspace shared by the attribution guard and the decay sidecar.

Both `dedup.py` and `decay.py` must key entries identically: a silent divergence
would make the sidecar's keys drift from the guard's and produce age records that
never match anything. One implementation, imported by both.

Deliberately free of any dependency on `src.memory.models` — `MemoryData.prompt_format`
imports `normalize` to look up sidecar records, so a model import here would close a
cycle.

`evals/memory/memory_lib.js:norm` mirrors `normalize` on the eval side so a failure
there predicts a real production drop. Any change to `normalize` must be mirrored in
that file.
"""

import re
import unicodedata

NICK_PATTERN = re.compile(r'@[\w\d_]+')

TRAITS_FIELD = 'traits'
RECENT_FIELD = 'recent'


def normalize(text: str) -> str:
    """Reduces an entry to its comparison key.

    Lowercases, strips `@nick` tokens, folds `ё` to `е`, replaces everything that
    is not a letter, a digit or whitespace with a space, and collapses whitespace.
    Nick stripping must run first, since `@` is itself stripped by the same rule.

    Keeping *only* letters and digits is deliberate, not tidiness: these keys become
    MongoDB field names in the decay sidecar, and Mongo restricts `$`-prefixed names
    and dots. Stripping category `P` alone left `$` (category `Sc`) intact, so
    «$500 в месяц долг» produced a field name Mongo can reject. The rule as written
    makes a restricted character structurally impossible in a key.

    Args:
        text: Raw entry text as the model emitted it.

    Returns:
        The comparison key, or an empty string when the entry carries no
        content of its own (e.g. a bare nick).
    """
    lowered = NICK_PATTERN.sub(' ', text.lower()).replace('ё', 'е')
    stripped = ''.join(
        char if char.isspace() or unicodedata.category(char)[0] in 'LN' else ' '
        for char in lowered
    )
    return ' '.join(stripped.split())


def entry_keys(traits: list[str], recent: list[str]) -> set[str]:
    """Returns the unified `traits` + `recent` keyspace of one participant.

    Unified on purpose: a fact must belong to one participant regardless of which
    list holds it, and an entry's age must survive promotion from `recent` to
    `traits`.
    """
    keys = {normalize(entry) for entry in traits}
    keys.update(normalize(entry) for entry in recent)
    keys.discard('')
    return keys
