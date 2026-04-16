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

## 2. Testing Information

### Prompt Evaluation (LLM Output)
LLM prompts and model configurations are evaluated using `promptfoo`.
- Configurations and test cases are located in the `evals/` directory.
- To run evaluations:
  ```bash
  cd evals
  promptfoo eval
  ```
- View results: `promptfoo view`.

## 3. Additional Development Information

### Key Project Components
- **Characters**: Defined in `src/characters/repository/*.yaml`. Each file contains the character's name, description, and base prompt.
- **Prompts**: Jinja2 templates stored in `src/prompts/<task>/<version>.j2`.
- **Model Configurations**: JSON files in `src/models/<local|cloud>/<task>/<version>.json`. They support environment variable interpolation using the `"env:VAR_NAME"` syntax.
- **Memory & RAG**: Context is enriched with facts (MongoDB) and message embeddings (Qdrant).

### Code Style
- Follow PEP 8.
- Follow Google style guide
- Use `src.settings` for all configuration access.
- All tasks (e.g., memory updates, recap generation) are managed through the `src/tasks/` module.
- AI logic is centralized in `src/ai.py`, which uses `model_manager` to resolve settings based on the current environment (`IS_LOCAL`).
