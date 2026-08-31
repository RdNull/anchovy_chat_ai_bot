# anchovy_chat_ai_bot

![coverage](coverage.svg)

A context-aware Telegram bot that simulates distinct Russian-speaking character personalities in group chats. The sole purpose is humor — which turns out to be a genuinely hard engineering target. Built as a personal exploration of applied LLM engineering, combining agentic tool use, retrieval-augmented generation, structured memory, and async media processing into a coherent, production-style system.

The bot participates in live group conversations, builds persistent per-chat memory, recalls past discussions via semantic search, and learns facts about individual users — all while staying in character.

---

## Key Features

**Agentic LLM Loop**
Characters run a depth-limited recursive tool-calling loop: the model can invoke tools, receive results, and continue reasoning before producing a final response. Tools are split into *context tools* (`search_messages`, `get_user_facts`, `search_web`) that feed information back into the loop, and *direct tools* (`answer_text`, `set_reaction`) that terminate it immediately. A `Replier` handles the actual dispatch — persisting text replies and writing bot reactions onto Telegram messages. A depth-5 safeguard re-binds only direct tools to force a response if the model keeps calling context tools. Tool binding and structured output are handled via LangChain.

**Multi-Character Persona System**
Characters are defined as YAML configs (name, description, detailed system prompt, style guidelines, behavioral constraints). The character repository loads them at startup; each character instance is independently rate-limited per chat. Each chat can run a different character, selected via `/list` (inline keyboard), `/random`, or left on the default; the choice is persisted per chat in MongoDB.

**Message Reactions**
The bot both reads and writes Telegram message reactions. Incoming `message_reaction` updates are diffed against the stored reaction set and applied as an atomic MongoDB update; the `set_reaction` tool lets a character emoji-react instead of replying (from a curated `ALLOWED_REACTIONS` set). Reactions render into the LLM prompt (collapsed/aggregated once a chat has more than a few reactors) but are excluded from embeddings.

**Retrieval-Augmented Generation (RAG)**
Messages are chunked with a sliding window, embedded via OpenAI `text-embedding-3-small`, and stored in Qdrant with UUID chunk IDs. An in-memory async cache avoids re-embedding identical text. The `search_messages` tool lets the LLM semantically retrieve relevant past conversation chunks at inference time, filtered by chat ID.

**Web Search Without a Reciting Bot**
The bot answers in one-line jabs, so the hard part of giving it web access was not fetching a result but keeping the result from becoming the reply. A search runs as a separate cheap model call against OpenRouter's web plugin, and every constraint is placed where compliance is not optional: the wrapper is prompted as an extractor rather than a summariser and capped at 150 tokens, so a paragraph is physically out of reach; sources are stripped by code afterwards rather than forbidden in the prompt — which turned out to matter, because the search provider injects its own instruction to cite every result as a markdown link, outranking any prompt that says otherwise and spending most of the token budget on citations destined for the bin; and the tool description tells the model the fragments are raw material for a line, not a line. Failure is deliberately flat — rate limit, timeout, upstream error, empty result and unparseable output all return the same "не нашлось", leaving the character one post-search state to stay in character about instead of five. Cause is recoverable from the log, which is also the instrument for the questions deferred to production: how often the bot chooses to look something up, whether the per-chat budget binds, and whether the search engine handles Russian queries well enough to keep.

**Structured Persistent Memory**
Each chat accumulates a `StructuredMemory` snapshot in MongoDB — per-participant `traits` (stable properties) and `recent` (things that just happened), plus chat-level active topics, open questions, and running jokes. Memory is rebuilt whenever message volume since the last update crosses a threshold (gated by an `ENABLE_MEMORY_PROCESSING` flag so the pipeline can be disabled without a redeploy), using an LLM in structured output mode (Claude Haiku 4.5 via OpenRouter) for reliable JSON extraction. Everything downstream of the model call is deterministic code:

- **Attribution guard** — one fact must belong to exactly one participant. Entries duplicated across a participant's own `traits`/`recent` collapse to the trait; when several participants claim the same fact, the previous snapshot acts as the incumbent map and decides which copy survives. A fact that is new on everyone is logged but kept, since a string match cannot distinguish a leak from two people who genuinely did the same thing.
- **Code-owned clock** — the extraction model emits bare strings and never sees or stamps a timestamp. A decay sidecar stored beside the snapshot tracks, per entry, when it was first written and how many cycles it has survived, keyed by a normalized form shared with the guard. The character prompt then renders ages the model cannot get wrong (`вчера`, `3 дня назад`, `больше недели назад`).
- **Cycle-based decay** — ageing is counted in update cycles rather than wall clock, because updates are triggered by message volume: a wall-clock TTL would wipe a quiet chat's memory and never bind in a busy one. Eviction keeps the newest N `recent` entries by birth and drops anything past a cycle limit, with positional caps on traits and chat state.
- **Churn accounting** — each cycle reports what was born, carried, promoted from `recent` to a generalized trait, evicted, or silently lost, so memory quality is measurable rather than anecdotal. The decay policy currently ships in log-only mode: it computes what it *would* evict and records the gap against the live baseline before being switched on.
- **Lossless intake** — each window is read oldest-first and the snapshot is stamped with the newest message it actually processed, rather than the clock at write time. Both halves are needed: reading newest-first drops the front of a backlog behind an advancing bookmark, and a wall-clock stamp swallows whatever arrives while the model is still thinking. Together they turn a window that overflows into ordinary backpressure — the surplus is simply the next cycle's work — instead of messages the bot never sees. The same pairing guards the embedding pass.

A daily job prunes old snapshots, always preserving the most recent one per chat.

**User Fact Tracking with Confidence Scoring**
Facts about individual users are extracted automatically in the same pass as each memory update: a dedicated LLM pass reads new messages and emits a list of stable facts per `@username`, each scored with a confidence value (0.5–1.0). Facts are upserted into MongoDB — if a semantically similar fact already exists (Qdrant cosine search), its confidence is reinforced or updated; otherwise a new record is created. A weekly background job decays the confidence of facts not updated in the past week; facts that reach zero confidence are deleted. The `get_user_facts` tool lets the character LLM retrieve the top facts about a user at inference time.

**Async Media Pipeline**
- Images: downloaded, hashed for deduplication, described by a vision LLM (Gemini 2.5 Flash Lite) with OCR
- Animated stickers (Telegram TGS/Lottie format): key frames extracted via OpenCV and the Lottie renderer, resized, and described in a single batched vision LLM call
- Descriptions are stored in MongoDB and injected into the conversation context
- If a character is triggered while a referenced image/sticker is still processing, response generation polls (with a bounded timeout) until the media description is ready before building the prompt

**LLM Prompt Evaluation**
Prompt quality is tracked with [promptfoo](https://promptfoo.dev) — test suites covering memory extraction, fact extraction, recap generation, image description, and character reply quality, with good/bad sample fixtures for each task. Where a property is mechanically checkable it is asserted in JavaScript rather than left to an LLM rubric: the memory suite re-implements the production key normalization so an attribution or timestamp-leak failure in evals predicts a real drop in production. The reply suite goes further and replays the whole agentic loop — a custom provider re-implements the production tool loop and hands the model the same four tool definitions, so what is graded is the loop's final answer under real tool pressure rather than a single completion. Swapping the chat model is then a two-file change: the model config the bot loads, and the provider file the suite runs.

**Dual Local/Cloud Mode**
A single `IS_LOCAL` flag switches the entire model stack between OpenRouter (cloud) and Ollama (local). Model configs are versioned JSON files per task, supporting environment variable interpolation.

**Observability**
LangSmith tracing is integrated via `@traceable` decorators across the LLM call graph, including A/B model-version tags on chat completions. Full span trees are captured for each agentic loop execution.

**Kubernetes Deployment & CI/CD**
A GitHub Actions workflow builds and pushes a multi-stage Docker image to GHCR on every push to `main`, then deploys it to a Kubernetes cluster (bot, MongoDB, and Qdrant manifests under `manifests/`) via `kubectl`, with app config supplied through a ConfigMap/Secret pair populated from repo variables and secrets.

---

## Tech Stack

| Layer                | Technology                                      | Role                                            |
|----------------------|-------------------------------------------------|-------------------------------------------------|
| Language & Runtime   | Python 3.14, asyncio                            | Async-first throughout                          |
| Telegram Integration | python-telegram-bot (HTTP/2)                    | Bot API, polling, message reactions             |
| LLM Orchestration    | LangChain                                       | Tool binding, structured output, model routing  |
| Cloud LLM Provider   | OpenRouter API                                  | Gemini and Claude models, pinned per task       |
| Local LLM            | Ollama                                          | Self-hosted fallback for all tasks              |
| Embeddings           | OpenAI text-embedding-3-small (via OpenRouter)  | 1536-dim vectors for RAG, cached via async-cache|
| Vector Database      | Qdrant (AsyncQdrantClient)                      | Message and fact retrieval                      |
| Document Database    | MongoDB (AsyncIOMotorClient)                    | Chat history, memory, facts, chat settings      |
| Data Validation      | Pydantic v2 / pydantic-settings                 | Models, structured LLM output, settings         |
| Prompt Templating    | Jinja2                                          | Versioned, task-specific prompt files           |
| Media Processing     | Pillow, OpenCV, Lottie, CairoSVG                | Image resizing, GIF/sticker frame extraction    |
| Scheduling           | scheduler                                       | Weekly fact-confidence decay, daily memory cleanup |
| Prompt Evaluation    | promptfoo                                       | LLM output quality testing across tasks         |
| Observability        | LangSmith                                       | LLM call tracing and span visualization         |
| Containerization     | Docker (multi-stage build)                      | Bot, MongoDB, and Qdrant services               |
| Deployment           | Kubernetes, GitHub Actions                      | CI build/push to GHCR, `kubectl`-based deploy   |
| Testing              | pytest, pytest-asyncio, pytest-mock, pytest-cov | Async test suite against a real MongoDB         |

---

## Architecture

The system is built around an async message processing pipeline with two parallel concerns: generating a response now, and updating long-term context asynchronously.

```
Telegram API
     |
     +---> message_reaction updates --> diff old/new reactions --> $pull/$addToSet on message doc
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
     +---> [async] Context checks
     |          Memory update (if message count since last snapshot >= its own trigger, and enabled)
     |            |         (serialized by one process-wide lock; embeddings run outside it)
     |            |         Read window oldest-first, capped
     |            |         LLM structured output -> attribution guard -> age sidecar
     |            |         -> eviction -> churn log -> save snapshot
     |            |         stamped with the newest message processed, so the surplus
     |            |         and anything that arrived mid-call become the next window
     |            +---> Fact extraction from new messages (LLM structured output)
     |            |         upsert into MongoDB (confidence-based merge via Qdrant similarity)
     |          Embeddings update (independent trigger, same oldest-first intake)
     |
     +---> Character.respond()  (character resolved per chat from MongoDB chat_settings)
               |
               If a referenced media item is still processing, poll until ready
               |
               Build prompt (system + memory + related messages + history)
               |
               Agentic loop (max depth 5):
                   LLM call
                     |-- tool_call: search_messages  --> Qdrant vector search
                     |-- tool_call: get_user_facts   --> MongoDB fact lookup
                     |-- tool_call: search_web       --> extractor LLM + OpenRouter web plugin
                     |                                   (rate-limited per chat, own timeout)
                     |-- tool_call: answer_text       --> Replier sends text, saves to MongoDB
                     |-- tool_call: set_reaction      --> Replier sets emoji reaction

[weekly scheduler]
     +---> Fact confidence decay
               Facts not updated in 7 days lose 0.1 confidence
               Facts at zero confidence are deleted

[daily scheduler]
     +---> Memory snapshot cleanup
               Snapshots older than the retention window are deleted
               (a snapshot is dated by the newest message it processed)
               The most recent snapshot per chat is always kept

[CI/CD]
     +---> GitHub Actions: build multi-stage Docker image -> push to GHCR
               -> kubectl apply against manifests/ (bot, MongoDB, Qdrant)
```

**Configuration model:** Characters (YAML), prompts (Jinja2 templates), and model configs (versioned JSON) are all loaded from the filesystem at startup. This makes it straightforward to add new characters, tune prompts, or swap models without touching application code. Per-chat character selection and settings live in MongoDB instead, so they persist across deploys. Settings that constrain each other are checked at boot and refuse to start rather than being quietly clamped — a window cap silently smaller than the trigger that fills it is the kind of misconfiguration that costs data rather than announcing itself.

---

## Testing

Tests live in `src/tests/` and run inside Docker against a real MongoDB instance. The suite covers unit tests for models and utilities as well as integration tests for the memory and embedding subsystems. Time-sensitive logic is driven by real message timestamps rather than a frozen clock — patching the clock breaks the async MongoDB driver, so the code takes its "now" from the message log instead, which makes it injectable and the tests deterministic without one.

```bash
docker compose exec bot pytest
```
