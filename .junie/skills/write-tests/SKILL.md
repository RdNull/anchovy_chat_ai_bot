---
name: write-tests
description: >
  Use this skill when the user asks to write, add, or implement tests for this
  project. Covers pytest patterns, fixture usage, mock strategies, and
  assertion style for anchovy_chat_ai_bot.
disable-model-invocation: true
---

## Current test state
- Files: !`ls src/tests/`
- Coverage: !`docker compose exec bot pytest --co -q 2>/dev/null | tail -5`

---

## Project test conventions

### Stack
- `pytest` with `asyncio_mode = auto` — all async tests work without decorators.
- Real MongoDB against `test_data` DB (set in `pytest.toml` `[pytest_env]`).
- Mocked LLM, Telegram API, and Qdrant.

### Running tests
```bash
docker compose exec bot pytest                   # all
docker compose exec bot pytest src/tests/test_X.py -v  # single file
docker compose exec bot pytest -k test_name      # single test
```

---

## Fixtures (conftest.py)

| Fixture | Type | What it provides |
|---------|------|-----------------|
| `clean_collections` | `autouse async` | Drops all MongoDB collections after each test |
| `make_update(...)` | factory | `MagicMock` Telegram `Update`; params: `text`, `user_id`, `chat_id`, `username`, `reply_to_message`, `photo`, `sticker`, `animation` |
| `make_context` | plain | `MagicMock` PTB context with `chat_data={}` and `bot.send_chat_action = AsyncMock()` |
| `mock_llm` | mocker | Patches `src.characters.character.ai.get_model`; returns `AIMessage(content='мок ответ')` |

`make_update` defaults: `user_id=111`, `chat_id=222` — these match `ALLOWED_USER_IDS`/`ALLOWED_CHAT_IDS` in `pytest_env`, so `@restricted` passes transparently.

---

## Mock patterns

### LLM (character respond loop)
```python
from unittest.mock import AsyncMock, MagicMock
from langchain_core.messages import AIMessage

llm = MagicMock()               # NOT AsyncMock — bind_tools() must be sync
llm.bind_tools.return_value = llm
llm.ainvoke = AsyncMock(side_effect=[AIMessage(content='reply')])
mocker.patch('src.characters.character.ai.get_model', return_value=llm)
```

### Memory LLM
```python
mock_llm = MagicMock()
mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
    return_value=StructuredMemory()
)
mocker.patch(
    'src.processors.context.memory.ai.get_memory_model',
    return_value=mock_llm,
)
```

### Qdrant / embeddings
```python
mocker.patch(
    'src.processors.context.embeddings.messages_embeddings_client.save_embeddings',
    new_callable=AsyncMock,
)
```

### Tool calls in character.respond()
Patch `ToolRegistry.execute` — do NOT patch individual `StructuredTool` instances
(they are frozen Pydantic models and cannot be patched directly):
```python
from src.tools import ToolRegistry
from langchain_core.messages import ToolMessage

mocker.patch.object(
    ToolRegistry,
    'execute',
    new=AsyncMock(return_value=ToolMessage(tool_call_id='tc1', content='[]')),
)
```

---

## Assertion style

**Never** use mock assertion methods. Use explicit `assert` statements:

```python
# BAD
mock.assert_called_once()
mock.assert_called_once_with('arg')
mock.assert_not_called()

# GOOD
assert mock.call_count == 1
assert mock.call_args == call('arg')
assert mock.call_count == 0
```

Import `call` from `unittest.mock` when using `call_args` comparisons.

---

## Character references

Never hardcode character names from the `CHARACTERS` registry — they can be
renamed at any time:

```python
# BAD
ctx.chat_data['character_code'] = 'anchovy'

# GOOD
code = next(iter(CHARACTERS))
ctx.chat_data['character_code'] = code
```

---

## reply_to_message mock

When building a `reply_to_message` mock, always set `sticker` and `animation`
explicitly to `None` — `MagicMock` auto-attributes are truthy and will be
misread by `_get_media()`:

```python
reply_msg = MagicMock()
reply_msg.text = 'original'
reply_msg.caption = None
reply_msg.from_user.username = 'bob'
reply_msg.from_user.first_name = 'bob'
reply_msg.sticker = None
reply_msg.photo = None
reply_msg.animation = None
update = make_update(reply_to_message=reply_msg)
```

---

## Known gotchas

| Gotcha | Fix |
|--------|-----|
| `freezegun` breaks pymongo async | Never use `freezegun` in this project. Natural insertion order is reliable enough for ordering tests. |
| `bind_tools()` returns a coroutine | Use `MagicMock()` base for LLM, not `AsyncMock()`. |
| Patching `StructuredTool.ainvoke` fails | Patch `ToolRegistry.execute` instead. |
| `@restricted` blocks unexpected users | `make_update` defaults to `user_id=111`, `chat_id=222` which are in the allowlist. Use a different ID to test the rejection path. |
| Test for rejection path needs `effective_message` mock | `make_update` sets `update.effective_message.reply_text = AsyncMock()` — use this, not `update.message.reply_text`, to assert rejection messages. |
| `settings` variables are set at import time | Use `mocker.patch.object(settings, 'FIELD', value)` to override for a test. |

---

## Test categories and where they live

| Category | File | DB? | LLM mock? |
|----------|------|-----|-----------|
| History / MongoDB layer | `test_history.py` | yes | no |
| Facts | `test_facts.py` | yes | no |
| Models & utils (pure) | `test_utils.py` | no | no |
| Context processors | `test_context.py` | yes | yes |
| Character respond loop | `test_character.py` | no | yes |
| Telegram handlers | `test_handlers.py` | yes | yes |

---

## Implementation steps

1. Read the source file under test to understand all branches.
2. Identify which fixtures from `conftest.py` apply.
3. For each public function/method, write at least one happy-path test and one
   edge-case test (empty input, missing field, error path).
4. For models with `ai_format` properties, cover every format branch.
5. Run `docker compose exec bot pytest src/tests/test_<name>.py -v` to verify.
