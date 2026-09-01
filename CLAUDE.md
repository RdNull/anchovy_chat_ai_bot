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
python -m src.scripts.create_sticker_embeddings   # re-index path; not needed at launch
```

## Architecture

This is a Telegram bot that simulates character personalities using LLMs with RAG and persistent memory. Core flow:

1. **Message received** → `src/messages/handlers.py` routes based on mention/reply/random chance
2. **Character response** → `src/characters/character.py` builds prompt, invokes LLM in agentic loop with tools. The active character per chat is resolved from `src/chat_settings/repository.py` (MongoDB `chat_settings` collection), selectable via `/list` (inline keyboard), `/random`, or defaulted; `/info` reports the current one.
3. **Context enrichment (post-response)** → `run_context_checks()` in `src/processors/context/handlers.py` runs after every message; when `messages_count >= settings.MEMORY_TRIGGER_SIZE` since the last snapshot, it triggers structured-memory update + fact extraction (gated by `settings.ENABLE_MEMORY_PROCESSING`); an independent counter fires the embedding update at `settings.EMBEDDINGS_TRIGGER_SIZE`. Both used to read `LAST_MESSAGES_SIZE`, which is the answering character's context window and nothing else
4. **Scheduler** (`src/bot.py:setup_scheduler`) → weekly: `src/tasks/facts.py:run_fact_decay` decays confidence of stale facts and deletes facts that reach zero; daily: `src/tasks/memory.py:run_memory_cleanup` deletes `mongo.memory` snapshots older than `settings.MEMORY_RETENTION_DAYS` (default 7), always preserving the most recent snapshot per chat. The cutoff is wall clock but a snapshot's `created_at` is its window watermark (see below), so retention ages on message time; the preserve-newest guard is what keeps a quiet chat from ageing out entirely

### Key subsystems

**Characters** (`src/characters/`): Defined in `repository/*.yaml`. The `Character` class binds LLM tools with `tool_choice='any'` and runs an agentic loop. Tools are split into *context tools* (`search_messages`, `get_user_facts`, `search_web`, `find_stickers`) and *direct tools* (`answer_text`, `set_reaction`, `send_sticker`, all `return_direct=True`); the loop terminates as soon as a direct tool is invoked **and it did not fail**. A depth>5 safeguard re-binds only direct tools to force termination, and `_MAX_LOOP_DEPTH` (8) is the absolute stop. Replies and reactions are dispatched via `Replier` (`src/characters/reply.py`), which persists text replies and sticker sends to MongoDB and writes bot reactions directly onto the reacted-to message's `reactions` field.

`is_return_direct` is a property of the *tool*, not of what happened when it ran, so a direct tool that could not deliver used to end the turn with the bot saying nothing at all. `ToolRegistry.execute` therefore returns `tuple[ToolMessage, object]`, and a direct tool that fails returns a `ToolFailure` (`src/tools.py`): the loop logs it, appends the `ToolMessage`, and gives the model another turn. `answer_text('')` returns one too. The depth cap is what that fix requires — the depth>5 branch terminated only because a direct tool always returned, so without an absolute stop a repeatedly-failing send turns a silent turn into an infinite one. Tests that patch `ToolRegistry.execute` must return the tuple; the `write-tests` skill documents the shape.

**Chat settings** (`src/chat_settings/repository.py`): Per-chat character selection, stored as `{chat_id, character_code}` documents in MongoDB — `get_character_code`/`set_character_code`, upserted so a chat keeps its choice across restarts and deploys.

**LLM stack**: `src/ai.py` provides cached model instances. `src/model_manager.py` resolves model configs from `src/models/<local|cloud>/<task>/<version>.json`. The `"env:VAR_NAME"` syntax in JSON configs interpolates environment variables at load time. Toggle local vs cloud with `IS_LOCAL`.

Every `cloud` config pins `max_retries: 0` and a `timeout` (milliseconds), and `test_every_openrouter_config_bounds_its_transport` refuses a new one that does not. Both are corrections to `langchain-openrouter` defaults that are actively harmful here: `max_retries=2` builds a backoff `RetryConfig` with a **300s** window and `retry_connection_errors=True`, while `request_timeout` defaults to `None`, so a connection-level stall hangs and is then retried for minutes — emitting no httpx log line, because a request that never gets a response has nothing to log. The timeout is tiered by what wraps the call: 60s on `chat`, which sits inside `AI_TIMEOUT` and may be called several times per reply; 120s on the background tasks, which have no wall clock of their own but do hold `CHAT_CONTEXT_LOCK`. A dropped call is cheap by design — the memory watermark only advances on success, so a failed cycle re-reads the same window next time. The `local` (ollama) configs are untouched: different SDK, different parameters.

Each task pins its version at the call site, not in settings: chat in `character.py:respond` (`_get_llm(versions=('v8',))`), memory `v3-cheap` in `memory/processors.py`, facts `v2` in `facts/processors.py`; the media describers and `search_web` pass nothing and take the `version='v1'` default in `ai.py`. `Character._get_llm` picks one version at random from the tuple it is given and appends it as a LangSmith tag — a one-element tuple is simply an A/B with one arm. Bumping the chat version means adding the JSON *and* changing that tuple; nothing reads the directory for a newest version.

The reply eval mirrors that config by hand: `evals/reply/character_loop_provider.js` re-implements `_run_llm_loop` against OpenRouter, and each `evals/models/cloud/chat/<label>.yaml` restates the model, sampling params, and all five tool schemas for it. `evals/reply/characters/promptfooconfig.yaml` selects which of those provider files the suite runs, so a chat-model bump that stops there leaves the eval measuring the old model.

Those schemas restate each tool's **description verbatim** from `src/characters/tools/`, not its first line — a truncated description measures a different tool and reports it fine, since half the behaviour under test (when *not* to call, and that fragments are material rather than a reply) lives in the lines after the first. `character_loop_provider.js` returns `metadata.toolsCalled`, which a `javascript` assert reads as `context.metadata` to score the tool choice itself; promptfoo passes an inline `value` as an **expression**, not an arrow function.

Two divergences from production are deliberate and will look like bugs to the next reader. `character_loop_provider.js` does **not** mirror the loop's direct-tool failure recovery — the suite grades prompt and tool choice, not loop mechanics, so a direct tool there always succeeds; its `extractAnswer` does know `send_sticker`, because with the schema bound under `tool_choice: required` a sticker call would otherwise run out to `maxIterations` and fail for the wrong reason. And `evals/reply/characters/promptfooconfig.yaml` still renders `prompts/v7.js` → `character_setup/v7.j2` while `character.py` pins **v8**, so nothing in `evals/` exercises the sticker guidance added in v8. `tool_callbacks.js:find_stickers` returns `[]` on purpose: that is the cold-start state the bot ships in and the branch the prompt has to handle, and it keeps every existing case answering with text.

**Sticker replies** (`src/embeddings/stickers.py`, `find_stickers`/`send_sticker`): the group's own stickers, made findable. Everything a sticker needs was already collected — the media pipeline downloads, hashes, deduplicates and vision-describes every one — but nothing read it back.

*Identity.* `is_sticker`, `sticker_emoji` and `sticker_set` are set from the PTB object at parse time (`messages/parsing.py:_as_sticker`, an `isinstance` narrowing at the call site — `_get_message_medium` still returns the raw medium) and persisted onto the `media_descriptions` row. `get_message_media_data` takes an optional `Sticker` and, when it has none, hydrates the three fields from the stored row: `_parse_media` reads history back with no PTB object, so without hydration every persisted sticker would come back looking like a photo. The live parse wins where both have an answer. `_parse_media_description` reads the three keys with `data.get`, unlike its siblings, because every row written before this unit has none of them. `sticker_set` is captured and unused — it is what makes a later `getStickerSet` expansion a config change rather than a re-derivation.

*The two `media_id`s.* **`messages.media_id` holds a `file_id` and can be sent with; `media_descriptions.media_id` holds a `file_unique_id` and cannot.** The index is built over the latter, so it holds identity only, and `get_sendable_file_id` resolves identity to a sendable id at send time by reading the newest carrier message — a re-issued `file_id` then wins without a reindex. The field was deliberately not renamed (an `updateMany $rename` racing a k8s rollout is not worth a cosmetic fix); the docstring and this line are the mitigation.

*Index.* `StickerEmbeddingsClient` (collection `stickers`) embeds `emoji | description | ocr_text`, emoji first so a free human-authored sentiment label is not averaged into a long description. `_point_id` derives the point id from `unique_id` via md5 — the `chunk_messages` trick — so a re-run upserts; `facts.py` uses `uuid4()` there and duplicates on every backfill. Only `status == READY` rows are indexed, and `search_stickers` re-checks status when it reads the row back. There is no `chat_id` filter: `media_descriptions` has no `chat_id` and the corpus is global by construction, so the per-chat concern is recency and that is enforced against `messages` in the tool. `search_stickers` passes no filter kwargs, so `_search` builds `Filter(must=[])` — verified against Qdrant to be accepted and to return `[]` on an empty collection, which is why `_search` needed no change. `_get_description` imports the media repository inside the function: `src/messages/media/__init__.py` eagerly imports `pipeline`, which imports this module, so a module-level import makes the cycle bite whenever `src.embeddings.stickers` is imported first — as `src/scripts/create_sticker_embeddings.py` does.

*Indexing is never gated on `ENABLE_STICKER_REPLIES`* — the hook at the end of `handle_media_message` runs whenever a sticker row reaches READY, so the corpus accumulates while the flag is off and there is something to search when it is flipped. The flag gates the tools only. Sticker metadata is written on **first sighting only**, since `handle_media_message` early-returns on an existing description; correct under a cold start, and the unit deliberately ships with no backfill of the existing corpus.

*Retrieval.* `find_stickers` returns candidates and the character picks, rather than sending the top hit: nearest-neighbour returns the most literally relevant sticker, which is frequently the least funny one, so the character is the filter and retrieval only has to be roughly right. `STICKER_SCORE_THRESHOLD` is `0.25`, looser than `facts.py`'s `0.6`, for the same reason. The tool over-fetches `limit + len(excluded)` and trims in Python — filtering server-side would need the recency set inside the Qdrant filter, and `_search` builds `must` from equality kwargs only. The «список пуст» line in its description is load-bearing: under a cold start an empty list is the common case, and a model that reads it as "retry" burns one of three context-tool calls.

*Sending.* `send_sticker` resolves the id, and on a missing carrier or a `telegram.error.BadRequest` drops the point from Qdrant and returns `ToolFailure('стикер недоступен')` so the loop can answer some other way. `BadRequest` specifically, not bare `Exception` — a network blip must surface as an error rather than quietly evicting a good sticker. `Replier.reply_sticker` persists the send: that is what the recency exclusion reads, and what puts the bot's own sticker into `ai_format` history.

Observability: one `STICKER_CORPUS size=… min=… enabled=… gate_open=…` line at boot (`src/bot.py:log_sticker_corpus`). At `STICKER_MIN_CORPUS=5` the gate should open almost immediately; if it does not, that line is the evidence the group recycles a very small sticker set — the one thing that would make the narrow-vocabulary decision worth revisiting.

The retrieval eval suite (`evals/stickers/`) is deliberately deferred: under a cold start there is no corpus to hand-label. When it is built, grade **recall-at-K**, not top-1 — what matters is whether a good option was among the candidates the character saw.

**Prompts**: Jinja2 templates in `src/prompts/<task>/<version>.j2`, loaded via `src/prompt_manager.py`.

**Memory** (`src/memory/`): `StructuredMemory` (`models.py`) stores per-chat `participants` (with `traits` and `recent` items) plus a `ChatState` containing `active_topics`, `open_questions`, and `running_jokes`. A snapshot is persisted as `MemoryData` — `content` (what the model emitted) beside a `decay` sidecar the model never sees. `repository.py` handles MongoDB CRUD (append-only snapshots, newest read back by `created_at`). A snapshot's `created_at` is the **window watermark** — the newest message it actually processed, passed in by `extract_memory` — not the wall clock at write time; it is what the next cycle reads back as its `$gt` bound. See "Memory pipeline" below for the per-cycle flow.

**Facts** (`src/facts/`): User facts are extracted automatically after each memory update. `src/facts/processors.py:extract_facts` calls an LLM with structured output to pull stable facts (with confidence 0.5–1.0) from new messages. `src/facts/handlers.py` upserts each fact — reinforcing confidence if a similar fact exists in Qdrant, creating a new record otherwise. `src/facts/repository.py` handles raw MongoDB CRUD. A weekly decay job (`src/tasks/facts.py`) lowers confidence of facts not updated in 7 days and deletes those that reach zero.

**Reactions** (`src/messages/handlers.py`, `src/messages/repository.py`): User reactions arrive via `message_reaction` Telegram updates (requires bot to be chat admin and `allowed_updates=Update.ALL_TYPES` in `run_polling`). Each update carries the user's full new reaction set as a snapshot. The handler calls `update_message_reactions(message, user_nickname, old_emojis, new_emojis)` which resolves the diff and applies `$pull`/`$addToSet`/`$unset` in a single MongoDB write. Bot reactions are written by `add_bot_reaction(message, settings.BOT_NICKNAME, emoji)` at send time (Telegram never echoes bot reactions). Both write to `reactions: dict[str, list[str]]` on the message document — emoji-keyed, nickname-valued. Reactions render into the LLM prompt via `Message.ai_format` / `Message.response_format` (see Message model below) but are excluded from embeddings via `Message.embedding_text`.

**Embeddings/RAG** (`src/embeddings/client.py` + `src/processors/context/embeddings.py`): Messages are chunked (window=8, overlap=3), assigned UUID chunk IDs, and stored in Qdrant. An `AsyncCache` on the embeddings client avoids re-embedding identical text. LLM tool `search_messages` performs semantic search with cosine similarity.

**Media pipeline** (`src/processors/media/`): Images go through a vision LLM for description+OCR. Animations/GIFs have key frames extracted (via OpenCV/Lottie), resized to ≤300k pixels, and described in a single vision LLM call. `MessageMedia.is_sticker` is what separates a sticker from a photo — `type` cannot, because `messages/media/download.py:FILE_FORMATS` types by file extension and maps `webp` and `jpg` alike to `MessageMediaTypes.IMAGE`. The flag is orthogonal to `type`: an animated `.tgs`/`.webm` sticker is `type=GIF, is_sticker=True`. Media status is tracked as pending/finished on the message; if a character is triggered while a referenced media item is still processing, `src/messages/response.py:_get_last_messages` calls `wait_for_media_ready` to poll (bounded by `settings.RESPOND_MEDIA_PROCESSING_POLLING_TIMEOUT`) before building the prompt.

**Tools available to LLM** (`src/characters/tools/`):
- *Context tools* (`context.py`): `search_messages` (Qdrant semantic search), `get_user_facts` (known facts about a user), `search_web` (see below), `find_stickers` (see **Sticker replies** below).
- *Direct tools* (`answer.py`, `return_direct=True`): `answer_text` (text reply via `Replier`), `set_reaction` (emoji reaction from `src.const.ALLOWED_REACTIONS`), `send_sticker` (sticker reply via `Replier`).

`find_stickers` and `send_sticker` are bound only when `settings.ENABLE_STICKER_REPLIES` is on **and** `sticker_corpus_size()` has reached `STICKER_MIN_CORPUS` — both or neither, since `send_sticker` alone gives the model an id parameter it can only hallucinate. The flag is checked before the count, so an installation with stickers off never runs the query.

`ToolRegistry` (`src/tools.py`) holds both groups, executes by name, and exposes `is_return_direct` so the character loop knows when to terminate. Note `execute` mutates `tool.metadata` on the shared tool singleton, so concurrent chats can race — pre-existing, and it means a search can be debited to the wrong chat's budget.

**Web search** (`search_web` in `context.py`): a second cheap LLM call — model `web_search/v1`, prompt `web_search/v1` — against OpenRouter's `web` plugin, declared in the model JSON's `plugins` key. The wrapper is an *extractor*, not a summariser: `max_tokens: 150` makes a paragraph unreachable.

The extraction prompt is **not** the only instruction the model gets. The plugin injects its own preamble with the results — «IMPORTANT: Cite them using markdown links named using the domain of the source» — which outranks a system prompt saying the opposite, and spends a large slice of the 150 tokens on citations that are then deleted. The `search_prompt` key in the plugin config replaces that preamble (keep the "use only these results" half; drop the citation half). `_CLEANERS` in `context.py` is the belt to that braces: an ordered pipeline run per line by `_parse_fragments`, markdown links first — the bare-domain pattern would otherwise match a link's *label*, eat the `](href)` behind it, and strand the opening `[`. A response containing the `не нашлось` marker on any line is void whole, since what follows it is the model explaining itself.

Everything that can go wrong — rate limit, timeout, error, empty, malformed — returns the same `['не нашлось']`, so the character has one post-search state to handle; the cause lives only in the log. `WEB_SEARCH_TIMEOUT` sits inside `AI_TIMEOUT`, which wraps the whole loop, so a slow search costs a search and not the reply.

"No retry" has to be **set**, not assumed: `langchain-openrouter` defaults to `max_retries=2`, which builds a backoff `RetryConfig` with a 300s window and `retry_connection_errors=True`, and to no HTTP timeout at all. Left alone, a connection-level stall spends the whole tool budget on retries that emit no httpx log line — a search that fails with thirty seconds of silence, while a hand-rolled request to the same endpoint returns in five. The config therefore pins `max_retries: 0` and a `timeout` (ms) below `WEB_SEARCH_TIMEOUT`, so the HTTP layer fails first with a cause instead of being cancelled from outside; `test_web_search_transport_fits_the_tool_budget` holds that relationship. Every other OpenRouter config now pins the same pair — see **LLM stack** above. Its value is measured rather than guessed: search latency is bimodal, roughly 5s or 24s on identical config, so the original 12s discarded every slow search. At 30s, three searches in one reply would exhaust `AI_TIMEOUT` — the depth limit and the rate limiter are what keep that from being reachable in practice. `_web_search_limiter` is a module-level `SlidingWindowRateLimiter(WEB_SEARCH_RATE_LIMIT, name='web_search')` — a per-chat budget separate from the character's own, since it protects spend rather than voice, and the `name` is what tells the two apart in the limiter's warning line; `is_exceeded` debits on check and is called exactly once, so a search that then fails still spent its slot. Per-tool guidance for `search_web` lives in each character YAML's `TOOLS:` block, not in `character_setup/v8.j2`. The sticker guidance is the exception and sits in the shared template, because the part that matters is budget arithmetic — `find_stickers` is a fourth context tool competing for the three-call cap the template itself states — and that is not a per-character property.

Observability: one `TOOL_WEB_SEARCH chat_id=… query=… results=… outcome=… elapsed_ms=…` line per call, `outcome ∈ {ok, empty, rate_limited, timeout, error}`. It is the unit's only instrument — what the bot chose to look up, the searches-per-reply ratio, whether the rate limit binds, whether the timeout is throwing away good searches, and (via the `empty` rate on Russian queries) whether `engine: parallel` is the right choice.

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

Always use `src/settings.py` (Pydantic `BaseSettings`) for all config — never read env vars directly. Settings include: allowed users/chats, reply chance, embedding parameters, model selection, the web-search budget (`WEB_SEARCH_RATE_LIMIT`, `WEB_SEARCH_TIMEOUT`), the sticker settings (`ENABLE_STICKER_REPLIES`, `STICKER_SEARCH_LIMIT`, `STICKER_SCORE_THRESHOLD`, `STICKER_MIN_CORPUS`, `STICKER_RECENT_EXCLUDE`), and the memory caps (`ENABLE_MEMORY_DECAY`, `TRAITS_KEEP`, `RECENT_KEEP`, `RECENT_MAX_CYCLES`, `TOPICS_KEEP`, `QUESTIONS_KEEP`, `JOKES_KEEP`), read in one place via `DecayCaps.from_settings()`.

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

All tests live in `src/tests/`. Shared fixtures are in `src/tests/conftest.py`. `src/tests/test_tools.py` covers the tools, including `search_web`'s parsing, failure and rate-limit paths — its autouse fixture clears the module-level limiter, whose window otherwise leaks between tests — and `find_stickers`/`send_sticker`'s eviction and failure paths; `src/tests/media/test_messages_media.py` has an autouse fixture clearing the `sticker_corpus_size` TTL cache for the same reason; `src/tests/test_model_manager.py` asserts the OpenRouter `plugins` key survives both config load and model construction, since `requirements.txt` pins no `langchain-openrouter` version and a dropped key answers from training data instead of erroring. Memory tests are split by module under `src/tests/memory/` (`test_memory.py`, `test_dedup.py`, `test_decay.py`); the cleanup task is covered by `src/tests/test_tasks_memory.py`, and the trigger/fetch-cap settings and their validator by `src/tests/test_settings.py`. Window-intake behaviour — oldest-first fetch, the watermark stamp, and the backlog/boundary deferral for both the memory and embedding passes — lives in `src/tests/test_context.py`. Sticker coverage is split by layer: intake flags in `test_handlers.py` (including the load-bearing assertion that a **photo** is not a sticker) and `media/test_messages_media.py`, the index in `test_embeddings.py`, the tools in `test_tools.py`, the corpus gate in `test_character.py`, and the boot log in `test_bot_init.py`.
Production modules must not carry test-only helpers — reset hooks, clear functions, injection seams. A test that needs past a module's public surface reaches for the private itself, from the test layer: `src/tests/test_tools.py` clears `_web_search_limiter._call_times` in an autouse fixture rather than the module exposing a reset. Module-level state that leaks between tests is the usual reason this comes up, and it is often a sign the global should not exist.

Use `[write-tests](.claude/skills/write-tests)` skill for tests manipulation.

### Documentation

`CLAUDE.md` and `README.md` are kept in sync with the code by hand — they are references, not changelogs. Use the [update-docs](.claude/skills/update-docs) skill when a subsystem changes.

## Code Style

- Follow PEP 8 and the Google Python Style Guide.
- Use single quotes for strings.
- Always use `src/settings.py` for config access — never read env vars directly.
- It's forbidden to use line splitting for long strings (`\`).