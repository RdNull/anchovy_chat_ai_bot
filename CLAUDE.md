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
cd evals && promptfoo view    # view results in browser
```

### Backfill embeddings
```bash
python -m src.scripts.create_embeddings
```

## Architecture

This is a Telegram bot that simulates character personalities using LLMs with RAG and persistent memory. Core flow:

1. **Message received** → `src/messages/handlers.py` routes based on mention/reply/random chance
2. **Character response** → `src/characters/character.py` builds prompt, invokes LLM in agentic loop with tools. The active character per chat is resolved from `src/chat_settings/repository.py` (MongoDB `chat_settings` collection), selectable via `/list` (inline keyboard), `/random`, or defaulted; `/info` reports the current one.
3. **Context enrichment (post-response)** → `run_context_checks()` in `src/processors/context/handlers.py` runs after every message; when `messages_count >= settings.LAST_MESSAGES_SIZE` since the last update, it triggers structured-memory update + fact extraction (gated by `settings.ENABLE_MEMORY_PROCESSING`), and a separate counter triggers embedding update
4. **Scheduler** (`src/bot.py:setup_scheduler`) → weekly: `src/tasks/facts.py:run_fact_decay` decays confidence of stale facts and deletes facts that reach zero; daily: `src/tasks/memory.py:run_memory_cleanup` deletes `mongo.memory` snapshots older than `settings.MEMORY_RETENTION_DAYS` (default 7), always preserving the most recent snapshot per chat

### Key subsystems

**Characters** (`src/characters/`): Defined in `repository/*.yaml`. The `Character` class binds LLM tools with `tool_choice='any'` and runs an agentic loop. Tools are split into *context tools* (`search_messages`, `get_user_facts`) and *direct tools* (`answer_text`, `set_reaction`, both `return_direct=True`); the loop terminates as soon as a direct tool is invoked. A depth>5 safeguard re-binds only direct tools to force termination. Replies and reactions are dispatched via `Replier` (`src/characters/reply.py`), which persists text replies to MongoDB and writes bot reactions directly onto the reacted-to message's `reactions` field.

**Chat settings** (`src/chat_settings/repository.py`): Per-chat character selection, stored as `{chat_id, character_code}` documents in MongoDB — `get_character_code`/`set_character_code`, upserted so a chat keeps its choice across restarts and deploys.

**LLM stack**: `src/ai.py` provides cached model instances. `src/model_manager.py` resolves model configs from `src/models/<local|cloud>/<task>/<version>.json`. The `"env:VAR_NAME"` syntax in JSON configs interpolates environment variables at load time. Toggle local vs cloud with `IS_LOCAL`.

**Prompts**: Jinja2 templates in `src/prompts/<task>/<version>.j2`, loaded via `src/prompt_manager.py`.

**Memory** (`src/memory/`): `StructuredMemory` (`models.py`) stores per-chat `participants` (with `traits` and `recent` items) plus a `ChatState` containing `active_topics`, `open_questions`, and `running_jokes`. `processors.py:extract_memory` runs an LLM with structured output against new messages; `repository.py` handles MongoDB CRUD.

**Facts** (`src/facts/`): User facts are extracted automatically after each memory update. `src/facts/processors.py:extract_facts` calls an LLM with structured output to pull stable facts (with confidence 0.5–1.0) from new messages. `src/facts/handlers.py` upserts each fact — reinforcing confidence if a similar fact exists in Qdrant, creating a new record otherwise. `src/facts/repository.py` handles raw MongoDB CRUD. A weekly decay job (`src/tasks/facts.py`) lowers confidence of facts not updated in 7 days and deletes those that reach zero.

**Reactions** (`src/messages/handlers.py`, `src/messages/repository.py`): User reactions arrive via `message_reaction` Telegram updates (requires bot to be chat admin and `allowed_updates=Update.ALL_TYPES` in `run_polling`). Each update carries the user's full new reaction set as a snapshot. The handler calls `update_message_reactions(message, user_nickname, old_emojis, new_emojis)` which resolves the diff and applies `$pull`/`$addToSet`/`$unset` in a single MongoDB write. Bot reactions are written by `add_bot_reaction(message, settings.BOT_NICKNAME, emoji)` at send time (Telegram never echoes bot reactions). Both write to `reactions: dict[str, list[str]]` on the message document — emoji-keyed, nickname-valued. Reactions render into the LLM prompt via `Message.ai_format` / `Message.response_format` (see Message model below) but are excluded from embeddings via `Message.embedding_text`.

**Embeddings/RAG** (`src/embeddings/client.py` + `src/processors/context/embeddings.py`): Messages are chunked (window=8, overlap=3), assigned UUID chunk IDs, and stored in Qdrant. An `AsyncCache` on the embeddings client avoids re-embedding identical text. LLM tool `search_messages` performs semantic search with cosine similarity.

**Media pipeline** (`src/processors/media/`): Images go through a vision LLM for description+OCR. Animations/GIFs have key frames extracted (via OpenCV/Lottie), resized to ≤300k pixels, and described in a single vision LLM call. Media status is tracked as pending/finished on the message; if a character is triggered while a referenced media item is still processing, `src/messages/response.py:_get_last_messages` calls `wait_for_media_ready` to poll (bounded by `settings.RESPOND_MEDIA_PROCESSING_POLLING_TIMEOUT`) before building the prompt.

**Tools available to LLM** (`src/characters/tools/`):
- *Context tools* (`context.py`): `search_messages` (Qdrant semantic search), `get_user_facts` (known facts about a user).
- *Direct tools* (`answer.py`, `return_direct=True`): `answer_text` (text reply via `Replier`), `set_reaction` (emoji reaction from `src.const.ALLOWED_REACTIONS`).

`ToolRegistry` (`src/tools.py`) holds both groups, executes by name, and exposes `is_return_direct` so the character loop knows when to terminate.

**Deployment**: `Dockerfile` is a multi-stage build (builder venv + slim runtime, non-root `app` user). `.github/workflows/deploy.yml` builds/pushes the image to GHCR on push to `main`, then applies `manifests/*.yaml` (bot, MongoDB, Qdrant Deployments; ConfigMap/Secret populated from repo vars/secrets) to a Kubernetes cluster via `kubectl`. Not relevant to local dev — use `docker compose up -d --build` instead.

### Message model text formats (`src/models.py`)

`Message` exposes three text representations:
- `embedding_text` (property): plain `[ts] nickname: text` format, no reactions. Used by embeddings, memory extraction, and fact extraction.
- `ai_format` (property): `embedding_text` + rendered reactions line. Used for user turns in the LLM prompt.
- `response_format` (property): bare `text` + rendered reactions line. Used for bot turns in the LLM prompt history.

Reactions render via `Message._render_reactions()`: bot (`settings.BOT_NICKNAME`) is always named and sorted first, excluded from the ≤3 collapse threshold; beyond threshold, named reactors get `+K`, unnamed-only gets `×N`. Bot reactions are stored under `settings.BOT_NICKNAME` (not character-qualified).

### Data flow for context

`run_context_checks()` in `src/processors/context/handlers.py` is called after each message. It uses `asyncio.Lock` per chat to prevent concurrent memory/embedding updates. When memory is updated, fact extraction runs in the same pass over the new messages.

### Configuration access

Always use `src/settings.py` (Pydantic `BaseSettings`) for all config — never read env vars directly. Settings include: allowed users/chats, reply chance, history window size, embedding parameters, model selection.

### Testing notes

`pytest.toml` sets `pythonpath = ["src"]` and `asyncio_mode = "auto"`. Test env vars (incl. `DATABASE_NAME=test_data`) are set in `[pytest_env]` — no `.env` file needed for tests.

Run tests inside Docker: `docker compose exec bot pytest`

All tests live in `src/tests/`. Shared fixtures are in `src/tests/conftest.py`.
Use `[write-tests](.claude/skills/write-tests)` skill for tests manipulation.

## Code Style

- Follow PEP 8 and the Google Python Style Guide.
- Use single quotes for strings.
- Always use `src/settings.py` for config access — never read env vars directly.
- It's forbidden to use line splitting for long strings (`\`).