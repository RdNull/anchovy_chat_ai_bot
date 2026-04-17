# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Run the bot
```bash
docker-compose up -d --build
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
2. **Context enrichment** → `src/processors/context/` updates memory (MongoDB) and embeddings (Qdrant) asynchronously
3. **Character response** → `src/characters/character.py` builds prompt, invokes LLM in agentic loop with tools
4. **Hourly scheduler** → `src/tasks/memory.py` updates structured memory for all active chats

### Key subsystems

**Characters** (`src/characters/`): Defined in `repository/*.yaml`. The `Character` class binds LLM tools and runs an agentic loop, recursively executing tool calls until the model returns a final text response.

**LLM stack**: `src/ai.py` provides cached model instances. `src/model_manager.py` resolves model configs from `src/models/<local|cloud>/<task>/<version>.json`. The `"env:VAR_NAME"` syntax in JSON configs interpolates environment variables at load time. Toggle local vs cloud with `IS_LOCAL`.

**Prompts**: Jinja2 templates in `src/prompts/<task>/<version>.j2`, loaded via `src/prompt_manager.py`.

**Memory** (`src/processors/context/memory.py`): Structured memory (`StructuredMemory` model) stores facts, decisions, topics, open loops, participants, and constraints in MongoDB per chat.

**Embeddings/RAG** (`src/embeddings/client.py` + `src/processors/context/embeddings.py`): Messages are chunked (window=8, overlap=3) and stored in Qdrant. LLM tool `search_messages` performs semantic search with cosine similarity.

**Media pipeline** (`src/processors/media/`): Images go through a vision LLM for description+OCR. Animations/GIFs have key frames extracted (via OpenCV/Lottie), resized to ≤300k pixels, and described in a single vision LLM call.

**Tools available to LLM**: `search_messages` (Qdrant semantic search), `save_user_fact` (persist fact with confidence score), `get_user_facts` (retrieve known facts about a user).

### Data flow for context

`run_context_checks()` in `src/processors/context/__init__.py` is called after each message. It uses `asyncio.Lock` per chat to prevent concurrent memory/embedding updates.

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