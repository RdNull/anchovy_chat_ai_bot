#!/usr/bin/env python3
"""PreToolUse guard: blocks Bash commands that would destroy local MongoDB or Docker volume data.

An agent's ad-hoc `docker compose exec bot python -c` script once ended with
`await mongo.messages.drop()`. Outside pytest nothing sets `DATABASE_NAME`, so
`src.mongo` bound to the real `data` database and the local chat history went with it.
The script looked like a test and was not one: only `[pytest_env]` in `pytest.toml`
points the code at `test_data`, and it applies to pytest alone.

A command is blocked when it both executes code (python, mongosh, `exec`) and contains
a drop or bulk delete, unless it targets the test database or is a pytest run — the
suite has its own guard in `src/tests/conftest.py`. Removing Docker volumes is blocked
outright: the local stores live in them. Exit code 2 refuses the call and hands stderr
back to the agent as the reason.
"""

import json
import re
import sys

DESTRUCTIVE = re.compile(
    r'\.drop\(|drop_database|dropDatabase|drop_collection|dropCollection|delete_many|deleteMany'
)
EXECUTES = re.compile(r'\b(python3?|mongosh|mongo)\b|\bexec\b')
TEST_TARGET = re.compile(r'DATABASE_NAME=test_|\bpytest\b')
VOLUME_WIPE = re.compile(
    # Global flags (`--profile x`, `-f file`) may sit between `compose` and `down`.
    r'docker(-|\s+)compose\b[^|;&]*\sdown\b[^|;&]*(\s-v\b|--volumes)'
    r'|docker\s+volume\s+(rm|prune)'
    r'|docker\s+system\s+prune'
)


def main() -> int:
    command = json.load(sys.stdin).get('tool_input', {}).get('command', '')

    if VOLUME_WIPE.search(command):
        reason = 'removes Docker volumes, which hold the local MongoDB and Qdrant data'
    elif DESTRUCTIVE.search(command) and EXECUTES.search(command) and not TEST_TARGET.search(command):
        reason = 'runs code that drops or bulk-deletes MongoDB data outside the test database'
    else:
        return 0

    print(
        f'Blocked by .claude/hooks/guard_destructive_db.py: this command {reason}. '
        'Scratch code that writes to Mongo must target the test database '
        '(`docker compose exec -e DATABASE_NAME=test_data bot python ...`) or be a pytest test. '
        'If the user really wants this, ask them to run it themselves with `!`.',
        file=sys.stderr,
    )
    return 2


if __name__ == '__main__':
    sys.exit(main())
