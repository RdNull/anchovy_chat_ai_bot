# anchovy_chat_ai_bot

![coverage](coverage.svg)

A context-aware Telegram bot that simulates distinct Russian-speaking character personalities in group chats. The sole purpose is humor — which turns out to be a genuinely hard engineering target. Built as a personal exploration of applied LLM engineering, combining agentic tool use, retrieval-augmented generation, structured memory, and async media processing into a coherent, production-style system.

The bot participates in live group conversations, builds persistent per-chat memory, recalls past discussions via semantic search, and learns facts about individual users — all while staying in character.

---

## Key Features

**Agentic LLM Loop**
Characters run a depth-limited recursive tool-calling loop: the model can invoke tools, receive results, and continue reasoning before producing a final response. Tool binding and structured output are handled via LangChain.

**Multi-Character Persona System**
Characters are defined as YAML configs (name, description, detailed system prompt, style guidelines, behavioral constraints). The character repository loads them at startup; each character instance is independently rate-limited per chat.

**Retrieval-Augmented Generation (RAG)**
Messages are chunked with a sliding window, embedded via OpenAI `text-embedding-3-small`, and stored in Qdrant. The `search_messages` tool lets the LLM semantically retrieve relevant past conversation chunks at inference time, filtered by chat ID.

**Structured Persistent Memory**
Each chat accumulates a `StructuredMemory` document in MongoDB — tracking facts, decisions, active topics, open loops, participant profiles, constraints, and preferences. Memory is updated hourly via a background scheduler and also triggered when message volume crosses a threshold. Updates use GPT-5-mini with structured output mode for reliable JSON extraction.

**User Fact Tracking with Confidence Scoring**
Facts about individual users are extracted automatically after each memory update cycle: a dedicated LLM pass reads new messages and emits a list of stable facts per `@username`, each scored with a confidence value (0.5–1.0). Facts are upserted into MongoDB — if a semantically similar fact already exists (Qdrant cosine search), its confidence is reinforced or updated; otherwise a new record is created. A weekly background job decays the confidence of facts not updated in the past week; facts that reach zero confidence are deleted. The `get_user_facts` tool lets the character LLM retrieve the top facts about a user at inference time.

**Async Media Pipeline**
- Images: downloaded, hashed for deduplication, described by a vision LLM (Gemini 2.5 Flash) with OCR
- Animated stickers (Telegram TGS/Lottie format): key frames extracted via OpenCV and the Lottie renderer, resized, and described in a single batched vision LLM call
- Descriptions are stored in MongoDB and injected into the conversation context

**LLM Prompt Evaluation**
Prompt quality is tracked with [promptfoo](https://promptfoo.dev) — test suites covering memory extraction, recap generation, image description, and character reply quality, with good/bad sample fixtures for each task.

**Dual Local/Cloud Mode**
A single `IS_LOCAL` flag switches the entire model stack between OpenRouter (cloud) and Ollama (local). Model configs are versioned JSON files per task, supporting environment variable interpolation.

**Observability**
LangSmith tracing is integrated via `@traceable` decorators across the LLM call graph. Full span trees are captured for each agentic loop execution.

---

## Tech Stack

| Layer                | Technology                                      | Role                                            |
|----------------------|-------------------------------------------------|-------------------------------------------------|
| Language & Runtime   | Python 3.14, asyncio                            | Async-first throughout                          |
| Telegram Integration | python-telegram-bot (HTTP/2)                    | Bot API, polling, persistence                   |
| LLM Orchestration    | LangChain                                       | Tool binding, structured output, model routing  |
| Cloud LLM Provider   | OpenRouter API                                  | Access to Grok, GPT-5-mini, Gemini 2.5 Flash    |
| Local LLM            | Ollama                                          | Self-hosted fallback for all tasks              |
| Embeddings           | OpenAI text-embedding-3-small (via OpenRouter)  | 1536-dim vectors for RAG                        |
| Vector Database      | Qdrant (AsyncQdrantClient)                      | Message and fact retrieval                      |
| Document Database    | MongoDB (AsyncIOMotorClient)                    | Chat history, memory, facts, media descriptions |
| Data Validation      | Pydantic v2                                     | Models, structured LLM output, settings         |
| Prompt Templating    | Jinja2                                          | Versioned, task-specific prompt files           |
| Media Processing     | Pillow, OpenCV, Lottie, CairoSVG                | Image resizing, GIF/sticker frame extraction    |
| Scheduling           | scheduler                                       | Hourly context updates, weekly fact decay       |
| Prompt Evaluation    | promptfoo                                       | LLM output quality testing across tasks         |
| Observability        | LangSmith                                       | LLM call tracing and span visualization         |
| Containerization     | Docker Compose                                  | Bot, MongoDB, and Qdrant services               |
| Testing              | pytest, pytest-asyncio, pytest-mock, freezegun  | Async test suite with time mocking              |

---

## Architecture

The system is built around an async message processing pipeline with two parallel concerns: generating a response now, and updating long-term context asynchronously.

```
Telegram API
     |
     v
Message Handlers  (handlers.py)
     |
     +---> Parse message (text, media, reply context)
     |
     +---> Save to MongoDB
     |
     +---> [async] Media pipeline
     |          Download -> hash dedup -> vision LLM -> store description
     |
     +---> [async] Context checks (per-chat lock)
     |          Memory update (if threshold reached)
     |            +---> Fact extraction from new messages (LLM structured output)
     |            |         upsert into MongoDB (confidence-based merge via Qdrant similarity)
     |          Embeddings update (if threshold reached)
     |
     +---> Character.respond()
               |
               Build prompt (system + memory + related messages + history)
               |
               Agentic loop (max depth 5):
                   LLM call
                     |-- tool_call: search_messages  --> Qdrant vector search
                     |-- tool_call: get_user_facts   --> MongoDB fact lookup
                     |-- text response               --> send to Telegram

[weekly scheduler]
     +---> Fact confidence decay
               Facts not updated in 7 days lose 0.1 confidence
               Facts at zero confidence are deleted
```

**Configuration model:** Characters (YAML), prompts (Jinja2 templates), and model configs (versioned JSON) are all loaded from the filesystem at startup. This makes it straightforward to add new characters, tune prompts, or swap models without touching application code.

---

## Testing

Tests live in `src/tests/` and run inside Docker against a real MongoDB instance. The suite covers unit tests for models and utilities as well as integration tests for the memory and embedding subsystems. Time-sensitive logic is tested with `freezegun`.

```bash
docker compose exec bot pytest
```
