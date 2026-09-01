---
name: write-tests
description: >
  Use this skill when the user asks to write, add, modify, or fix tests for
  this project. Covers pytest patterns, fixture usage, mock strategies, and
  enforces project assertion style (explicit asserts, no mock.assert_*
  methods) for anchovy_chat_ai_bot.
disable-model-invocation: false
---

## Current test state
- Files: !`find src/tests -name 'test_*.py' | sort`
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
| `clean_collections` | `autouse async` | Drops every MongoDB collection after each test (`messages`, `memory`, `facts`, `embedding_tasks`, `media_descriptions`, `chat_settings`) |
| `mock_langsmith` | `autouse` | Patches `langsmith.get_current_run_tree` with a run tree whose `tags` is a real list |
| `make_update(...)` | factory | `MagicMock` Telegram `Update`; params: `message_id`, `text`, `updated_text`, `user_id`, `chat_id`, `username`, `reply_to_message`, `photo`, `sticker`, `animation` |
| `make_context` | plain | `MagicMock` PTB context with `bot.send_chat_action = AsyncMock()` |
| `mock_llm` | mocker | Patches `src.characters.character.ai.get_model`; returns an `AIMessage` carrying an `answer_text` tool call with `text='мок ответ'` |

`make_update` defaults: `user_id=111`, `chat_id=222` — these match `ALLOWED_USER_IDS`/`ALLOWED_CHAT_IDS` in `pytest_env`, so `@restricted` passes transparently.
Pass `updated_text=...` to get an `edited_message`; otherwise `update.edited_message` is `None`.

`mock_llm` returns a tool call, not plain content — the character loop terminates on the
`return_direct` tool, so a bare `AIMessage(content='reply')` would loop instead of answering.

### Shared helper

`src/tests/test_utils.py:make_message(chat_id=1, telegram_id=None, role=UserRole.USER, text='hello', nickname='user1')`
builds a `Message` for DB-layer, context, and memory tests. Import it rather than
constructing `Message` inline.

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
The memory cycle lives in `src/memory/processors.py` (`extract_memory`), so patch
`ai.get_memory_model` **there** — not on `src.ai`, and not under
`src.processors.context` (that package only holds `handlers.py` and `embeddings.py`):
```python
from src.memory.models import StructuredMemory

mock_llm = MagicMock()
mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
    return_value=StructuredMemory()
)
mocker.patch('src.memory.processors.ai.get_memory_model', return_value=mock_llm)
```
`src/tests/test_context.py` wraps exactly this in a local `mock_memory_llm(mocker, return_value=None)`
helper — reuse the pattern when adding memory-cycle tests.

### AsyncMock is rarely needed
`mocker.patch` auto-maps async functions — do NOT wrap with `AsyncMock` unless the default doesn't work (e.g. the mock must return a complex object or behave in a non-standard way):

```python
# GOOD — auto-mapping handles awaiting
mocker.patch('src.embeddings.facts.facts_embedding_client.search_facts', return_value=[])
mock_save = mocker.patch('src.embeddings.facts.facts_embedding_client.save_fact')

# Only use AsyncMock when auto-mapping falls short
mocker.patch('some.module.fn', AsyncMock(side_effect=lambda x: x))
```

### Qdrant / embeddings
The client methods are `save` and `search` (on `MessageEmbeddingsClient`), and
`save_fact` / `search_facts` (on `FactsEmbeddingClient`). Patch at the module that
imported the client instance:
```python
mocker.patch('src.processors.context.embeddings.messages_embeddings_client.save')
mocker.patch('src.characters.tools.context.messages_embeddings_client.search', return_value=[])
```

### Tool calls in character.respond()
Patch `ToolRegistry.execute` — do NOT patch individual `StructuredTool` instances
(they are frozen Pydantic models and cannot be patched directly). `execute` returns
a **tuple** of `(ToolMessage, raw result)`; the loop reads the raw value to tell a
`ToolFailure` from a delivered answer, so a bare `ToolMessage` return unpacks wrong:
```python
from src.tools import ToolRegistry
from langchain_core.messages import ToolMessage

mocker.patch.object(
    ToolRegistry,
    'execute',
    new=AsyncMock(return_value=(ToolMessage(tool_call_id='tc1', content='[]'), None)),
)
```
To drive the failure-recovery path, return a `ToolFailure` as the raw value —
`src/tests/test_character.py:execute_returning` wraps this for a sequence of calls:
```python
from src.tools import ToolFailure

mocker.patch.object(
    ToolRegistry,
    'execute',
    new=AsyncMock(return_value=(ToolMessage(tool_call_id='tc1', content='fail'),
                                ToolFailure('стикер недоступен'))),
)
```

---

## No test-only code in production

Production modules must not carry helpers that exist only for tests — reset
hooks, clear functions, injection seams, `_for_testing` arguments. If a test
needs to reach past a module's public surface, it reaches for the private
directly, from the test layer:

```python
# BAD — src/characters/tools/context.py
def reset_web_search_limiter():
    """For tests."""
    _web_search_limiter._call_times.clear()

# GOOD — src/tests/test_tools.py
@pytest.fixture(autouse=True)
def reset_web_search_limiter():
    _web_search_limiter._call_times.clear()
    yield
    _web_search_limiter._call_times.clear()
```

The test file is allowed to know a module private; the module is not allowed to
grow API for its tests. Module-level state that leaks between tests (a rate
limiter window, a cache) is the usual reason this comes up — clear it in an
autouse fixture next to the tests that care.

If the seam feels unavoidable, that is usually the state itself being wrong
rather than the test: prefer deleting the module-level global.

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
renamed at any time. The active character is persisted per chat in MongoDB
(`chat_settings`), not in PTB `chat_data`, so set it through the helpers:

```python
# BAD
await set_chat_character(chat_id, 'anchovy')

# GOOD
from src.characters.repository import CHARACTERS
from src.messages.utils import get_chat_character, set_chat_character

code = next(iter(CHARACTERS))
await set_chat_character(chat_id, code)
character = await get_chat_character(chat_id)
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
| `ToolRegistry.execute` returns a tuple | `(ToolMessage, raw_result)`. Returning a bare `ToolMessage` from the mock makes the loop unpack it as two messages. |
| A direct tool that fails must not end the turn | It returns `ToolFailure`; the loop appends the `ToolMessage` and recurses. `_MAX_LOOP_DEPTH` (8) is the absolute stop. |
| `@restricted` blocks unexpected users | `make_update` defaults to `user_id=111`, `chat_id=222` which are in the allowlist. Use a different ID to test the rejection path. |
| Test for rejection path needs `effective_message` mock | `make_update` sets `update.effective_message.reply_text = AsyncMock()` — use this, not `update.message.reply_text`, to assert rejection messages. |
| `settings` variables are set at import time | Use `mocker.patch.object(settings, 'FIELD', value)` to override for a test. |
| Memory ages are stamped from the window watermark, not wall clock | Give test messages explicit `created_at` values and assert against them; don't reach for time-freezing. |
| Three different window settings | `LAST_MESSAGES_SIZE` (character context), `*_TRIGGER_SIZE` (when a pass fires), `MESSAGES_*_MAX_SIZE` (fetch cap). Don't mix them up. |
| The fetch-cap validator only fires on construction | `mocker.patch.object(settings, ...)` bypasses it. To test the rule, build `_Settings(MESSAGES_MEMORY_MAX_SIZE=10, MEMORY_TRIGGER_SIZE=40)` inside `pytest.raises(ValidationError)`. |

---

## Test categories and where they live

| Category | File | DB? | LLM mock? |
|----------|------|-----|-----------|
| Mongo connection | `test_db.py` | yes | no |
| Messages repository / model | `test_messages.py` | yes | no |
| Facts | `test_facts.py` | yes | yes |
| Models & utils (pure) | `test_utils.py` | partly | no |
| Context & memory triggers, window intake | `test_context.py` | yes | yes |
| Memory subsystem | `memory/test_memory.py`, `memory/test_dedup.py`, `memory/test_decay.py` | yes | yes |
| Scheduled tasks | `test_tasks_memory.py`, `test_tasks_facts.py` | yes | no |
| Settings & validators | `test_settings.py` | no | no |
| Embeddings clients / chunking | `test_embeddings.py` | no | no |
| LLM tools | `test_tools.py` | no | no |
| Character respond loop | `test_character.py` | no | yes |
| Replier | `test_reply.py` | yes | no |
| Telegram handlers | `test_handlers.py` | yes | yes |
| Media pipeline | `media/test_messages_media.py`, `media/test_processors_media.py` | yes | yes |
| Bot wiring / scheduler | `test_bot_init.py` | no | no |

---

## Implementation steps

1. Read the source file under test to understand all branches.
2. Identify which fixtures from `conftest.py` apply.
3. For each public function/method, write at least one happy-path test and one
   edge-case test (empty input, missing field, error path).
4. For models with `ai_format` properties, cover every format branch.
5. Run `docker compose exec bot pytest src/tests/test_<name>.py -v` to verify.
