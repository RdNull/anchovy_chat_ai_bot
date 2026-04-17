# Project Overview: Shizo Ded Bot

This project is a Telegram bot designed for complex character-based interactions, utilizing LLMs with RAG (Retrieval-Augmented Generation) and memory capabilities.

## 1. Build/Configuration Instructions

### Prerequisites
- Docker and Docker Compose.
- Python 3.14 (if running locally without Docker).
- MongoDB and Qdrant (provided via Docker Compose).

### Environment Configuration
The project uses a `.env` file for configuration. Essential variables include:
- `TELEGRAM_TOKEN`: Your bot's token from BotFather.
- `DATABASE_URL`: MongoDB connection string (e.g., `mongodb://mongo:27017`).
- `QDRANT_URL`: Qdrant connection string (e.g., `http://qdrant:6333`).
- `OPENROUTER_API_KEY`: API key for OpenRouter (if using cloud models).
- `IS_LOCAL`: Boolean (`true`/`false`). Determines whether to use local or cloud model configurations from `src/models/`.

### Running the Project
Use Docker Compose to start all services:
```bash
docker-compose up -d --build
```

### Backfill embeddings
```bash
python -m src.scripts.create_embeddings
```

## 2. Architecture & Key Project Components

Core flow:
1. **Message received** → `src/messages/handlers.py` routes based on mention/reply/random chance.
2. **Context enrichment** → `src/processors/context/` updates memory (MongoDB) and embeddings (Qdrant) asynchronously.
3. **Character response** → `src/characters/character.py` builds prompt, invokes LLM in agentic loop with tools.
4. **Hourly scheduler** → `src/tasks/memory.py` updates structured memory for all active chats.

### Key subsystems
- **Characters** (`src/characters/`): Defined in `repository/*.yaml`. Each file contains the character's name, description, and base prompt. The `Character` class binds LLM tools and runs an agentic loop, recursively executing tool calls until the model returns a final text response.
- **LLM stack**: `src/ai.py` provides cached model instances. `src/model_manager.py` resolves settings based on the current environment (`IS_LOCAL`) from `src/models/<local|cloud>/<task>/<version>.json`. The `"env:VAR_NAME"` syntax in JSON configs interpolates environment variables at load time.
- **Prompts**: Jinja2 templates stored in `src/prompts/<task>/<version>.j2`, loaded via `src/prompt_manager.py`.
- **Memory** (`src/processors/context/memory.py`): Structured memory (`StructuredMemory` model) stores facts, decisions, topics, open loops, participants, and constraints in MongoDB per chat. Context is enriched with facts and message embeddings.
- **Embeddings/RAG** (`src/embeddings/client.py` + `src/processors/context/embeddings.py`): Messages are chunked (window=8, overlap=3) and stored in Qdrant. LLM tool `search_messages` performs semantic search with cosine similarity.
- **Media pipeline** (`src/processors/media/`): Images go through a vision LLM for description+OCR. Animations/GIFs have key frames extracted (via OpenCV/Lottie), resized to ≤300k pixels, and described in a single vision LLM call.
- **Tools available to LLM**: `search_messages` (Qdrant semantic search), `save_user_fact` (persist fact with confidence score), `get_user_facts` (retrieve known facts about a user).

### Data flow for context
`run_context_checks()` in `src/processors/context/__init__.py` is called after each message. It uses `asyncio.Lock` per chat to prevent concurrent memory/embedding updates.

## 3. Testing Information

### Prompt Evaluation (LLM Output)
LLM prompts and model configurations are evaluated using `promptfoo`.
- Configurations and test cases are located in the `evals/` directory.
- To run evaluations:
  ```bash
  cd evals
  promptfoo eval
  ```
- View results: `promptfoo view`.

### Automated Tests (pytest)
- `pytest.toml` sets `pythonpath = ["src"]` and `asyncio_mode = "auto"`.
- Test env vars (incl. `DATABASE_NAME=test_data`) are set in `[pytest_env]` — no `.env` file needed for tests.
- Run tests:
  ```bash
  pytest                        # all tests
  pytest -k test_db             # single test
  pytest -v --cov               # verbose with coverage
  ```
- Run tests inside Docker: `docker compose exec bot pytest`
- All tests live in `src/tests/`. Shared fixtures are in `src/tests/conftest.py`.

## 4. Code Style
- Follow PEP 8 and the Google Python Style Guide.
- Use single quotes for strings.
- Always use `src.settings` (Pydantic `BaseSettings`) for all configuration access — never read env vars directly.
- It's forbidden to use line splitting for long strings (`\`).
- All tasks (e.g., memory updates, recap generation) are managed through the `src/tasks/` module.
- AI logic is centralized in `src/ai.py`, which uses `model_manager` to resolve settings.
