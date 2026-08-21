# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Run the bot
```bash
docker compose up -d --build
```

### Run tests
```bash
docker compose exec bot pytest                   # all
docker compose exec bot pytest src/tests/test_X.py -v  # single file
docker compose exec bot pytest -k test_name      # single test
```

### Prompt evaluation (LLM outputs)
```bash
cd evals && promptfoo eval
cd evals && promptfoo view              # view results in browser
cd evals/memory && promptfoo eval       # one suite (memory, facts, reply, recap, …)
```

### Backfill embeddings
```bash
python -m src.scripts.create_embeddings
```

## Architecture

This is a Telegram bot that simulates character personalities using LLMs with RAG and persistent memory. Core flow:

1. **Message received** → `src/messages/handlers.py` routes based on mention/reply/random chance
2. **Character response** → `src/characters/character.py` builds prompt, invokes LLM in agentic loop with tools. The active character per chat is resolved from `src/chat_settings/repository.py` (MongoDB `chat_settings` collection), selectable via `/list` (inline keyboard), `/random`, or defaulted; `/info` reports the current one.
3. **Context enrichment (post-response)** → `run_context_checks()` in `src/processors/context/handlers.py` runs after every message; when `messages_count >= settings.MEMORY_TRIGGER_SIZE` since the last snapshot, it triggers structured-memory update + fact extraction (gated by `settings.ENABLE_MEMORY_PROCESSING`); an independent counter fires the embedding update at `settings.EMBEDDINGS_TRIGGER_SIZE`. Both used to read `LAST_MESSAGES_SIZE`, which is the answering character's context window and nothing else
4. **Scheduler** (`src/bot.py:setup_scheduler`) → weekly: `src/tasks/facts.py:run_fact_decay` decays confidence of stale facts and deletes facts that reach zero; daily: `src/tasks/memory.py:run_memory_cleanup` deletes `mongo.memory` snapshots older than `settings.MEMORY_RETENTION_DAYS` (default 7), always preserving the most recent snapshot per chat. The cutoff is wall clock but a snapshot's `created_at` is its window watermark (see below), so retention ages on message time; the preserve-newest guard is what keeps a quiet chat from ageing out entirely

### Key subsystems

**Characters** (`src/characters/`): Defined in `repository/*.yaml`. The `Character` class binds LLM tools with `tool_choice='any'` and runs an agentic loop. Tools are split into *context tools* (`search_messages`, `get_user_facts`) and *direct tools* (`answer_text`, `set_reaction`, both `return_direct=True`); the loop terminates as soon as a direct tool is invoked. A depth>5 safeguard re-binds only direct tools to force termination. Replies and reactions are dispatched via `Replier` (`src/characters/reply.py`), which persists text replies to MongoDB and writes bot reactions directly onto the reacted-to message's `reactions` field.

**Chat settings** (`src/chat_settings/repository.py`): Per-chat character selection, stored as `{chat_id, character_code}` documents in MongoDB — `get_character_code`/`set_character_code`, upserted so a chat keeps its choice across restarts and deploys.

**LLM stack**: `src/ai.py` provides cached model instances. `src/model_manager.py` resolves model configs from `src/models/<local|cloud>/<task>/<version>.json`. The `"env:VAR_NAME"` syntax in JSON configs interpolates environment variables at load time. Toggle local vs cloud with `IS_LOCAL`.

**Prompts**: Jinja2 templates in `src/prompts/<task>/<version>.j2`, loaded via `src/prompt_manager.py`.

**Memory** (`src/memory/`): `StructuredMemory` (`models.py`) stores per-chat `participants` (with `traits` and `recent` items) plus a `ChatState` containing `active_topics`, `open_questions`, and `running_jokes`. A snapshot is persisted as `MemoryData` — `content` (what the model emitted) beside a `decay` sidecar the model never sees. `repository.py` handles MongoDB CRUD (append-only snapshots, newest read back by `created_at`). A snapshot's `created_at` is the **window watermark** — the newest message it actually processed, passed in by `extract_memory` — not the wall clock at write time; it is what the next cycle reads back as its `$gt` bound. See "Memory pipeline" below for the per-cycle flow.

**Facts** (`src/facts/`): User facts are extracted automatically after each memory update. `src/facts/processors.py:extract_facts` calls an LLM with structured output to pull stable facts (with confidence 0.5–1.0) from new messages. `src/facts/handlers.py` upserts each fact — reinforcing confidence if a similar fact exists in Qdrant, creating a new record otherwise. `src/facts/repository.py` handles raw MongoDB CRUD. A weekly decay job (`src/tasks/facts.py`) lowers confidence of facts not updated in 7 days and deletes those that reach zero.

**Reactions** (`src/messages/handlers.py`, `src/messages/repository.py`): User reactions arrive via `message_reaction` Telegram updates (requires bot to be chat admin and `allowed_updates=Update.ALL_TYPES` in `run_polling`). Each update carries the user's full new reaction set as a snapshot. The handler calls `update_message_reactions(message, user_nickname, old_emojis, new_emojis)` which resolves the diff and applies `$pull`/`$addToSet`/`$unset` in a single MongoDB write. Bot reactions are written by `add_bot_reaction(message, settings.BOT_NICKNAME, emoji)` at send time (Telegram never echoes bot reactions). Both write to `reactions: dict[str, list[str]]` on the message document — emoji-keyed, nickname-valued. Reactions render into the LLM prompt via `Message.ai_format` / `Message.response_format` (see Message model below) but are excluded from embeddings via `Message.embedding_text`.

**Embeddings/RAG** (`src/embeddings/client.py` + `src/processors/context/embeddings.py`): Messages are chunked (window=8, overlap=3), assigned UUID chunk IDs, and stored in Qdrant. An `AsyncCache` on the embeddings client avoids re-embedding identical text. LLM tool `search_messages` performs semantic search with cosine similarity.

**Media pipeline** (`src/processors/media/`): Images go through a vision LLM for description+OCR. Animations/GIFs have key frames extracted (via OpenCV/Lottie), resized to ≤300k pixels, and described in a single vision LLM call. Media status is tracked as pending/finished on the message; if a character is triggered while a referenced media item is still processing, `src/messages/response.py:_get_last_messages` calls `wait_for_media_ready` to poll (bounded by `settings.RESPOND_MEDIA_PROCESSING_POLLING_TIMEOUT`) before building the prompt.

**Tools available to LLM** (`src/characters/tools/`):
- *Context tools* (`context.py`): `search_messages` (Qdrant semantic search), `get_user_facts` (known facts about a user).
- *Direct tools* (`answer.py`, `return_direct=True`): `answer_text` (text reply via `Replier`), `set_reaction` (emoji reaction from `src.const.ALLOWED_REACTIONS`).

`ToolRegistry` (`src/tools.py`) holds both groups, executes by name, and exposes `is_return_direct` so the character loop knows when to terminate.

**Deployment**: `Dockerfile` is a multi-stage build (builder venv + slim runtime, non-root `app` user). `.github/workflows/deploy.yml` builds/pushes the image to GHCR on push to `main`, then applies `manifests/*.yaml` (bot, MongoDB, Qdrant Deployments; ConfigMap/Secret populated from repo vars/secrets) to a Kubernetes cluster via `kubectl`. Not relevant to local dev — use `docker compose up -d --build` instead.

### Memory pipeline (`src/memory/`)

`processors.py:extract_memory` is the whole cycle, run from `run_context_checks()`. In order:

1. **Prompt build** — `_prompt_memory()` renders the current snapshot for the model but strips `active_topics`, so stale topics cannot be carried forward: the model cannot re-emit what it never sees. Extraction uses prompt `memory/v4` and model `v3-cheap`, with `with_structured_output(StructuredMemory)`. `v4.j2` takes a `context_window` variable (`settings.LAST_MESSAGES_SIZE`) for the line telling the model what the bot already holds; `evals/memory/promptfooconfig.yaml` renders the same template directly, so it supplies its own fixture value under `defaultTest.vars` — without it Jinja's default `Undefined` renders an empty gap and every eval case silently changes prompt.
2. **Attribution guard** (`dedup.py:resolve_attribution_conflicts`) — enforces that one fact belongs to exactly one participant. First drops `recent` entries duplicating a trait of the same nick (the trait is the intended survivor), then adjudicates cross-participant collisions against the last snapshot as the incumbent map: exactly one incumbent wins and the other copies are dropped (`incumbent_wins`); every copy an incumbent means the store is already corrupt, so all are dropped to let a later window re-extract (`ambiguous_incumbent`); a fact new on everyone is reported but kept (`no_incumbent`), since a string match cannot separate an intake leak from two people who did the same thing. `state` is untouched — the prompt deliberately allows a fact in both places.
3. **Reconcile** (`decay.py:reconcile`) — ages the sidecar one cycle. `prior_decay` is the sole authority on what was stored last cycle; each emitted key either gets a fresh `DecayRecord(born=now, cycles=0, field=…)` or inherits `born` with `cycles + 1`. `resolve_watermark()` stamps the cycle with the newest message timestamp in the window (wall clock only as fallback), so ages sit on the same clock as the message log and tests stay deterministic. `extract_memory` computes that watermark once and uses it twice — `format_ts`'d for the sidecar, raw for the snapshot's `created_at` — so the sidecar's clock and the next window's `$gt` bound cannot drift apart.
4. **Eviction** (`decay.py:apply_decay`) — computes two survivor sets and applies one. *Baseline* (current default, `ENABLE_MEMORY_DECAY=False`) is positional caps on every list. *Policy* (when enabled) keeps the newest `RECENT_KEEP` by `born` and hard-drops anything past `RECENT_MAX_CYCLES`; `traits` and `state` keep the baseline caps regardless, since with no confidence or recurrence signal every trait-eviction rule is bad. Both sets are recorded — a policy drop the baseline kept logs `applied=False`, a baseline drop the policy would have kept logs `reason=baseline`. The sidecar is pruned to survivors, so an evicted entry the model re-emits starts fresh instead of resurrecting a stale `born`.
5. **Churn** (`decay.py:summarize_churn`) — runs last, on the pruned sidecar, so it describes what was actually saved. Events: `birth`, `carry`, `vanish`, `promote`, `promote_candidate`. Exact `promote` (verbatim `recent` → `traits`) fires approximately never, since the model rewords on generalisation; `promote_candidate` covers the real case — every trait born on a nick that lost a `recent` entry, as a *coincidence count, not a pairing*, which is why the log prints `lost_recent` next to it.

Cycles, not wall clock, are the unit: updates trigger on message count, so a wall-clock TTL would empty a quiet chat and never bind in a busy one.

**Keyspace** (`keys.py`): `normalize()` is the single comparison key used by both the guard and the sidecar — lowercase, strip `@nick` tokens, `ё` → `е`, replace every non-letter/digit with a space, collapse whitespace. Keeping only letters and digits is a Mongo constraint, not tidiness: these keys become field names in the `decay` sidecar, where `$`-prefixed names and dots are restricted. `entry_keys()` returns a participant's unified `traits + recent` keyspace so an entry's age survives promotion. `evals/memory/memory_lib.js:norm` mirrors this — change one, change the other.

**Rendering** (`models.py:MemoryData.prompt_format`): builds the `=== ПАМЯТЬ ===` block for the character prompt (`src/characters/character.py`). `recent` entries are prefixed with a Russian relative age (`сегодня` / `вчера` / `N дня|дней назад` / `больше недели назад`) computed in code from the sidecar's `born`, compared by calendar day. A missing or malformed record renders the entry bare rather than raising — this runs on the reply path.

**Observability**: the cycle emits `MEMORY_ATTRIBUTION_CONFLICT`, `MEMORY_TRAIT_OVERFLOW` (logged before eviction reshapes the list), `MEMORY_DECAY` (`evicted` vs `would_evict`), `MEMORY_CHURN`, and `MEMORY_CHURN_LOST` lines. Decay ships off on purpose: phase 1 measures what the policy would evict before it evicts anything.

**Retention**: `handlers.py:delete_old_memories` backs the daily cleanup task, dropping snapshots older than `settings.MEMORY_RETENTION_DAYS` while always preserving the newest per chat. Since `created_at` is the window watermark, this compares message time against a wall-clock cutoff — normally seconds apart, and the preserve-newest guard covers the case where they are not.

### Message model text formats (`src/models.py`)

`Message` exposes three text representations:
- `embedding_text` (property): plain `[ts] nickname: text` format, no reactions. Used by embeddings, memory extraction, and fact extraction.
- `ai_format` (property): `embedding_text` + rendered reactions line. Used for user turns in the LLM prompt.
- `response_format` (property): bare `text` + rendered reactions line. Used for bot turns in the LLM prompt history.

Reactions render via `Message._render_reactions()`: bot (`settings.BOT_NICKNAME`) is always named and sorted first, excluded from the ≤3 collapse threshold; beyond threshold, named reactors get `+K`, unnamed-only gets `×N`. Bot reactions are stored under `settings.BOT_NICKNAME` (not character-qualified).

### Data flow for context

`run_context_checks()` in `src/processors/context/handlers.py` is called after each message. `CHAT_CONTEXT_LOCK` is a **single module-level `asyncio.Lock` shared by every chat**, not one per chat, and it guards only the memory path (`update_chat_context`) — the embedding path runs outside it. `_update_chat_memory` re-reads `get_last_memory` after acquiring the lock, so a queued second run sees the fresh watermark and falls out on `LAST_MESSAGES_MIN_SIZE`. When memory is updated, fact extraction runs in the same pass over the new messages.

Both passes read their window with `get_messages(..., sort_order=1)`. `sort_order` selects **which end of the matching range the `size` cap takes** (`-1` newest, `1` oldest) and never the order of the returned list, which is always chronological. Oldest-first is what makes truncation backpressure rather than data loss: the overflow is the newest messages, they fall after the watermark, and the next cycle picks them up. Fetching newest-first dropped the oldest behind an advancing watermark, which lost them permanently.

### Configuration access

Always use `src/settings.py` (Pydantic `BaseSettings`) for all config — never read env vars directly. Settings include: allowed users/chats, reply chance, embedding parameters, model selection, and the memory caps (`ENABLE_MEMORY_DECAY`, `TRAITS_KEEP`, `RECENT_KEEP`, `RECENT_MAX_CYCLES`, `TOPICS_KEEP`, `QUESTIONS_KEEP`, `JOKES_KEEP`), read in one place via `DecayCaps.from_settings()`.

Three window sizes are easy to confuse, so they are three settings:

| setting | meaning |
|---|---|
| `LAST_MESSAGES_SIZE` | the answering character's context window (`src/messages/response.py`), and nothing else |
| `MEMORY_TRIGGER_SIZE` / `EMBEDDINGS_TRIGGER_SIZE` | how many new messages must accumulate before that pass runs |
| `MESSAGES_MEMORY_MAX_SIZE` / `MESSAGES_EMBEDDINGS_MAX_SIZE` | the fetch cap — the most messages one pass reads in a window |

A `_fetch_caps_exceed_triggers` model validator refuses to boot when a cap is below its trigger. It raises rather than clamps: after the intake fix an under-sized cap no longer loses messages, but it does leave a remainder every cycle that re-fires the trigger immediately and burns an LLM call per pass. `env_ignore_empty=True` means an unset repo var expands to `""` in the configmap and falls through to the code default.

### Testing notes

`pytest.toml` sets `pythonpath = ["src"]` and `asyncio_mode = "auto"`. Test env vars (incl. `DATABASE_NAME=test_data`) are set in `[pytest_env]` — no `.env` file needed for tests.

Run tests inside Docker: `docker compose exec bot pytest`

All tests live in `src/tests/`. Shared fixtures are in `src/tests/conftest.py`. Memory tests are split by module under `src/tests/memory/` (`test_memory.py`, `test_dedup.py`, `test_decay.py`); the cleanup task is covered by `src/tests/test_tasks_memory.py`, and the trigger/fetch-cap settings and their validator by `src/tests/test_settings.py`. Window-intake behaviour — oldest-first fetch, the watermark stamp, and the backlog/boundary deferral for both the memory and embedding passes — lives in `src/tests/test_context.py`.
Use `[write-tests](.claude/skills/write-tests)` skill for tests manipulation.

### Documentation

`CLAUDE.md` and `README.md` are kept in sync with the code by hand — they are references, not changelogs. Use the [update-docs](.claude/skills/update-docs) skill when a subsystem changes.

## Code Style

- Follow PEP 8 and the Google Python Style Guide.
- Use single quotes for strings.
- Always use `src/settings.py` for config access — never read env vars directly.
- It's forbidden to use line splitting for long strings (`\`).