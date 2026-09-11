# anchovy_chat_ai_bot

![coverage](coverage.svg)

A context-aware Telegram bot that simulates distinct Russian-speaking character personalities in group chats. The sole purpose is humor — which turns out to be a genuinely hard engineering target. Built as a personal exploration of applied LLM engineering, combining agentic tool use, retrieval-augmented generation, structured memory, and async media processing into a coherent, production-style system.

The bot participates in live group conversations, builds persistent per-chat memory, recalls past discussions via semantic search, and learns facts about individual users — all while staying in character.

---

## Key Features

**Agentic LLM Loop**
Characters run a depth-limited recursive tool-calling loop: the model can invoke tools, receive results, and continue reasoning before producing a final response. Tools are split into *context tools* (message search, user facts, web search, sticker search) that feed information back into the loop, and *direct tools* (text reply, emoji reaction, sticker reply) that terminate it immediately. A `Replier` handles the actual dispatch — persisting replies and writing bot reactions onto Telegram messages. A depth-5 safeguard re-binds only direct tools to force a response if the model keeps calling context tools. Tool binding and structured output are handled via LangChain.

The loop also distinguishes a tool that *is* final from a tool that *succeeded*. "Terminate on a direct tool" was a property of the tool rather than of the call, so a send that failed — an expired file identifier, an empty answer — ended the turn with the bot saying nothing at all, which reads as the bot ignoring you. A failed direct tool now reports back into the conversation and the model gets another turn to answer some other way. That fix needs a companion: the old depth limit terminated only because a direct tool always returned, so an absolute cap was added at the same time, or a send that fails every attempt would trade a silent turn for an endless one.

**Multi-Character Persona System**
Characters are defined as YAML configs (name, description, detailed system prompt, style guidelines, behavioral constraints). The character repository loads them at startup; each character instance is independently rate-limited per chat. Each chat can run a different character, selected via `/list` (inline keyboard), `/random`, or left on the default; the choice is persisted per chat in MongoDB.

**Speaking Unprompted**
The bot used to interject on a coin flip — a fixed chance per message and a cooldown, with no idea what it was interrupting, which produces a bot that is annoying at exactly the rate you configure. It now runs a judge instead: after every message a separate scoring call reads the recent window and rates whether there is anything worth saying, and names which message to answer, or none. It is a second call rather than a smaller model — the same one that writes the replies, asked for a number and a reason instead of a line. The prompt is written around the negative cases, because the default answer has to be silence — two people converging on where to meet want a result, not a character, and a joke that already landed does not need a second one. Deterministic gates run first and cost nothing: a minimum number of new messages, a cooldown since the bot last spoke, a minimum number of human messages since then, and never immediately after itself, so the model only ever judges conversations that are already plausible.

- **It ships as a dry run.** Two flags, not one: the first kills the pipeline outright, the second gates only the send. In between is the state the feature ships in — every window evaluated, scored and logged, nothing sent — so the score threshold can be calibrated against real conversations before the bot is allowed to open its mouth.
- **A bookmark, claimed under a lock.** Each chat records the newest message it has already judged. Because the check runs as a detached task per message, two messages arriving together would otherwise both read that bookmark before either moved it, judge the same window, and post twice; the read-and-advance is serialized, and the lock is released before the model call rather than held across it.
- **The bookmark advances on judging, not on speaking.** What is being rate-limited is the evaluation, and re-reading a window the model already declined only buys the same verdict a second time.
- **It reads the newest unjudged messages, not the oldest.** Memory reads a backlog oldest-first so nothing is lost; the judge deliberately does the opposite. After a stretch where the gates held it back, an oldest-first read judged the conversation from an hour ago — and a moment that has passed is not worth answering, so the surplus is dropped rather than queued.
- **Which message it answers is part of the verdict.** The judge returns an index into the window, or nothing, and the character prompt marks that one message so the reply quotes it. With no index the reply goes out unquoted, and the prompt asks for a line that stands on its own — naming the topic or the person, since a "да ты гонишь" addressed to nobody reads as noise.
- **The judge and the answerer read different windows.** The judge looks further back than the character does, so a message it picks can be missing from the character's context entirely — which would produce a reply visibly quoting a message the model was never shown, and answering the room instead. The chosen message is carried forward into the reply window rather than trusting the two ranges to overlap.

Dispatching a reply is addressed by chat rather than by incoming Telegram update, which is what makes a reply to nothing expressible at all: the same code path serves a mention, a reply-to-bot, and a message the bot decided to write on its own.

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

A daily job prunes snapshots older than 90 days, always preserving the most recent one per chat. The window is long on purpose: snapshots are a few kilobytes each, and 90 days of them is enough history to tell how long a trait survives, how long a running joke lasts, and whether an open question ever closes.

**User Fact Tracking with Confidence Scoring**
Facts about individual users are extracted automatically in the same pass as each memory update: a dedicated LLM pass reads new messages and emits a list of stable facts per `@username`, each scored with a confidence value (0.5–1.0). Facts are upserted into MongoDB — if a semantically similar fact already exists (Qdrant cosine search), its confidence is reinforced or updated; otherwise a new record is created. A weekly background job decays the confidence of facts not updated in the past week; facts that reach zero confidence are deleted. The `get_user_facts` tool lets the character LLM retrieve the top facts about a user at inference time.

**Async Media Pipeline**
- Images: downloaded, hashed for deduplication, described by a vision LLM (Gemini 2.5 Flash Lite) with OCR
- Animated stickers (Telegram TGS/Lottie format): key frames extracted via OpenCV and the Lottie renderer, resized, and described in a single batched vision LLM call
- Descriptions are stored in MongoDB and injected into the conversation context
- If a character is triggered while a referenced image/sticker is still processing, response generation polls (with a bounded timeout) until the media description is ready before building the prompt

**Sticker Replies From the Group's Own Vocabulary**
Everything a sticker reply needs was already being collected and then thrown away: every sticker anyone sends is downloaded, deduplicated and vision-described, and nothing ever read it back. Making that corpus searchable turned out to rest on a single distinction the code could not draw — a static sticker and a screenshot are both `.webp`-or-`.jpg` images and were classified identically, so an index built the obvious way would have let the bot answer with someone's screenshot. The media type enum turned out to be doing two unrelated jobs: choosing a decoder from the file extension, and labelling the stored record. Only the second needs to know about stickers, so that is where the distinction lives, taken from Telegram's own metadata at intake rather than guessed from the bytes.

- **Retrieval proposes, the character disposes.** Nearest-neighbour search returns the most literally relevant sticker, which is frequently the least funny one, so the search returns a handful of candidates with their descriptions and the character picks. The similarity threshold is deliberately looser than the one used for facts: the job is a plausible shortlist, not one confident hit. Stickers the bot sent in the recent past are filtered out, so it does not lean on the same one twice.
- **The search box had to say what it searches.** The first version asked for a free-form sentence about the sticker, and the model — with no way to know it was addressing a vector index over machine-written image descriptions — filled it with the replies it was weighing, one of which retrieved nothing at all. The fix that mattered was not in the retrieval but in the wording: the tool now asks for short descriptions of a *picture* and says so, with examples. It also accepts several at once, because the same trace showed the model cramming four unrelated ideas into the one slot it had.
- **Several probes, merged by rank rather than score.** Similarity scores from different queries are not measured against a common scale, so ranking a merged list by score quietly hands the whole result to whichever query happened to score generously — and the output looks perfectly reasonable either way. The lists are combined by reciprocal rank instead, which keeps every query represented and promotes anything two of them independently found. The score is not merely ignored, it is no longer carried at all, so the tempting wrong merge cannot be written by accident later. Candidates are merged as bare identities and only the survivors are read back in full, so extra queries cost embeddings rather than database reads.
- **One log line answers whether it was worth it.** Each search records what was asked, what each query found, and how many of the candidates the character finally saw came from each one. That last number is the only evidence that the second and third queries earn their keep, and it is the thing that would justify reverting the feature if they do not.
- **Two fields named the same thing, one of them sendable.** Telegram issues both a stable identifier and a reusable one, and the two collections here call both `media_id`. The index stores identity only and the sendable id is resolved from the message log at send time, so an identifier the platform re-issues costs nothing — copying it into the index would have made a single re-issue evict the sticker permanently.
- **The vocabulary is narrow on purpose** — only stickers the group already sends, never a pack expansion. In-group callback beats having a sticker for everything.
- **It ships cold, and repeat traffic warms it up.** There is no migration: records predating the feature are unmarked, and the first version could only have learned stickers nobody had ever sent, because the pipeline skips anything it has already described. Re-sending a sticker the bot has seen before is now what classifies and indexes it — which is the common case, so the corpus fills by itself from ordinary conversation. A line at startup reports its size; if that number stalls, it is the evidence the group recycles a smaller sticker set than assumed, the one finding that would reopen the narrow-vocabulary decision.
- **No readiness gate.** An early version withheld the tools until the index passed a size floor. That was guarding a temporary version of a permanent problem — an index holding hundreds of stickers still comes back empty on an unrelated topic — so the empty result has to be handled on every single call anyway, and the floor bought nothing but a cache and a stale count.
- **Failure is eviction, not a dead turn.** A sticker Telegram refuses is dropped from the index and reported back into the loop, so the character answers some other way. Only that specific refusal evicts; a network error is allowed to surface, because quietly discarding a perfectly good sticker on a blip is the worse failure.

**LLM Prompt Evaluation**
Prompt quality is tracked with [promptfoo](https://promptfoo.dev) — test suites covering memory extraction, fact extraction, recap generation, image description, and character reply quality, with good/bad sample fixtures for each task. Where a property is mechanically checkable it is asserted in JavaScript rather than left to an LLM rubric: the memory suite re-implements the production key normalization so an attribution or timestamp-leak failure in evals predicts a real drop in production. The reply suite goes further and replays the whole agentic loop — a custom provider re-implements the production tool loop and hands the model the same tool definitions, verbatim down to their descriptions, so what is graded is the loop's final answer under real tool pressure rather than a single completion. Swapping the chat model is then a two-file change: the model config the bot loads, and the provider file the suite runs. Where the re-implementation deliberately diverges from production — it does not model the loop's failure recovery, and its sticker search returns nothing, matching the cold start the feature ships in — that is recorded in the code, so the next reader does not mistake it for drift.

**A Read-Only Window Into the Bot's Own Data**
Building eval fixtures and auditing memory both meant querying MongoDB by hand and reformatting the result, and that is how fixtures quietly drifted out of the format the bot actually produces. A small MCP server now exposes the bot's data to a Claude Code session: semantic search over chat history, a plain chronological read filtered by time, author and role, the exact window around any message, memory snapshots with their age records, a diff between any two snapshots, and user facts. It is a tool boundary rather than a script because the useful work is a loop — which window to pull next depends on what the last one contained — and a script needs a person between every step.

- **Fixtures are rendered by the production code, not a template.** The bot formats the same messages two different ways: one for the model writing the reply, and one for the models that extract memory and facts. A fixture in the wrong format tests a bot that does not exist, so the tool takes exactly those two formats and nothing custom.
- **Memory diffs use production's keyspace.** Entries are compared through the same normalization the attribution guard and the decay records use, so a reworded or re-punctuated entry counts as carried rather than as a loss plus a birth — the numbers are the ones the memory pipeline would report itself.
- **Read-only by credential, not by discipline.** It connects with a database user that cannot write. The vector store has no credential to lean on, so the server also avoids two production helpers that quietly create a missing collection on first use.
- **Outside the reply path entirely.** It runs from the bot's image with a different command, as its own process in its own namespace; nothing it does can reach the loop that answers the chat.
- **Reachable from anywhere, and nothing else is.** It answers at one HTTPS host behind a bearer token, checked before a request body is read, so a rejected caller never reaches the protocol layer. An empty token refuses to start the process rather than serving without auth — the same silent-blank failure the deploy tests guard against, where the outcome would be the chat published. The pod holds only the read-only database credential and a separate, spend-capped embedding key, never the bot's secrets. Its network policy admits traffic only from the ingress and lets it out only to the two stores, DNS, and public HTTPS for query embeddings, so read-only is a property of the cluster, not just of the code. There is one transport: the local copy is the same HTTP server with the same token check, so the auth path runs every day rather than only in production.
- **Its output is untrusted.** Everything it returns was written by chat members, some of it deliberately adversarial, and it is handed back as data to analyse, never as instructions.

**Dual Local/Cloud Mode**
A single `IS_LOCAL` flag switches the entire model stack between OpenRouter (cloud) and Ollama (local). Model configs are versioned JSON files per task, supporting environment variable interpolation.

**Observability**
LangSmith tracing is integrated via `@traceable` decorators across the LLM call graph, including A/B model-version tags on chat completions. Full span trees are captured for each agentic loop execution.

**Kubernetes Deployment & CI/CD**
A GitHub Actions workflow builds and pushes a multi-stage Docker image to GHCR on every push to `main`, then deploys it to a Kubernetes cluster (bot, MongoDB, and Qdrant manifests under `manifests/`) via `kubectl`, with app config supplied through a ConfigMap/Secret pair populated from repo variables and secrets.

Template substitution renders an unexported variable as an empty string rather than failing, which makes a missing config value a silent write of a blank into the cluster — and one that stays dormant until the next time a pod happens to be recreated. The test suite enforces the link instead: every value the deploy substitutes must be exported by the deploy job, and the deploy script itself refuses to run with a required value empty. A config gap fails in CI, where it is a red test rather than an outage months later.

The one public endpoint — the data-access server below — costs nothing beyond the node. An edge proxy runs on the node's own ports instead of behind a cloud load balancer, which would have cost two thirds of current spend to buy a stable IP that a single node cannot make highly available anyway. Certificates come from Let's Encrypt, held in the cluster rather than on a volume the proxy owns. The price of skipping the load balancer is an IP that changes when the node is replaced, so a ten-minute job re-points the DNS record, selecting the node's public IPv4 explicitly and refusing to ever write a private one: pointing the record at the internal address resolves fine and fails silently.

Two constraints in the manifests are load-bearing and read like frugality: the bot runs a single replica with a `Recreate` rollout, because Telegram permits exactly one long-polling caller per token and the default rolling update briefly ran two. Store images are pinned to the versions the cluster is actually running rather than `latest`, so a pod recreation cannot pull a new major over an existing volume. Both stores declare readiness probes and the deploy blocks on them before the bot rolls, and a nightly `mongodump` CronJob writes to a separate volume — the single-replica MongoDB previously had no backup of any kind.

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
| Vector Database      | Qdrant (AsyncQdrantClient)                      | Message, fact and sticker retrieval             |
| Document Database    | MongoDB (AsyncIOMotorClient)                    | Chat history, memory, facts, chat + initiative state |
| Data Validation      | Pydantic v2 / pydantic-settings                 | Models, structured LLM output, settings         |
| Prompt Templating    | Jinja2                                          | Versioned, task-specific prompt files           |
| Media Processing     | Pillow, OpenCV, Lottie, CairoSVG                | Image resizing, GIF/sticker frame extraction    |
| Scheduling           | scheduler                                       | Weekly fact-confidence decay, daily memory cleanup |
| Prompt Evaluation    | promptfoo                                       | LLM output quality testing across tasks         |
| Developer Tooling    | MCP Python SDK v2 (streamable HTTP)             | Read-only data access for Claude Code sessions  |
| Observability        | LangSmith                                       | LLM call tracing and span visualization         |
| Containerization     | Docker (multi-stage build)                      | Bot, MongoDB, and Qdrant services               |
| Deployment           | Kubernetes, GitHub Actions                      | CI build/push to GHCR, `kubectl`-based deploy   |
| Ingress & TLS        | Traefik, cert-manager, Let's Encrypt            | HTTPS on the node's own ports, no load balancer |
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
     |          (a sticker is typed as one at intake and indexed into Qdrant
     |           once described; a sticker predating the feature is retyped
     |           and indexed the next time anyone sends it)
     |
     +---> [async] Context checks
     |          Initiative check (cheap gates -> judge LLM -> score + chosen message)
     |            |         (window claimed under a lock; bookmark advances on judging,
     |            |          not on speaking; the send itself is behind a second flag)
     |            +---> [async] unprompted reply -> Character.respond()
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
               Build prompt (system + memory + related messages + history,
               with the message being answered marked in place)
               |
               Agentic loop (context-tool depth 5, hard cap 8):
                   LLM call
                     |-- tool_call: search_messages  --> Qdrant vector search
                     |-- tool_call: get_user_facts   --> MongoDB fact lookup
                     |-- tool_call: search_web       --> extractor LLM + OpenRouter web plugin
                     |                                   (rate-limited per chat, own timeout)
                     |-- tool_call: find_stickers    --> 1-3 Qdrant searches over sticker
                     |                                   descriptions, fused by rank,
                     |                                   minus recent sends
                     |-- tool_call: answer_text       --> Replier sends text, saves to MongoDB
                     |-- tool_call: set_reaction      --> Replier sets emoji reaction
                     |-- tool_call: send_sticker      --> resolve sendable id -> Replier sends
                     |                                   (on refusal: evict, loop continues)

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
               -> blackbox namespace (MCP server, ingress, network policy)
               -> DNS sync CronJob

[blackbox]  (separate process and namespace; never on the reply path)
     Claude Code --HTTPS + bearer token--> Traefik (node ports 80/443)
               -> MCP server (read-only Mongo user) -> MongoDB, Qdrant
```

**Configuration model:** Characters (YAML), prompts (Jinja2 templates), and model configs (versioned JSON) are all loaded from the filesystem at startup. This makes it straightforward to add new characters, tune prompts, or swap models without touching application code. Per-chat character selection and settings live in MongoDB instead, so they persist across deploys. Settings that constrain each other are checked at boot and refuse to start rather than being quietly clamped — a window cap silently smaller than the trigger that fills it is the kind of misconfiguration that costs data rather than announcing itself.

---

## Testing

Tests live in `src/tests/` and run inside Docker against a real MongoDB instance. The suite covers unit tests for models and utilities as well as integration tests for the memory and embedding subsystems. Time-sensitive logic is driven by real message timestamps rather than a frozen clock — patching the clock breaks the async MongoDB driver, so the code takes its "now" from the message log instead, which makes it injectable and the tests deterministic without one.

```bash
docker compose exec bot pytest
```

---

## Blackbox: Data Access for Claude Code

The read-only MCP server runs in two places, both over streamable HTTP with the same bearer-token check. It is hosted in the cluster at `https://mcp.anchovy-bot.rdnull.im/`, and it runs locally as a Docker container. Nothing about the tools differs between the two.

### Hosted (production)

Add it to Claude Code once. The token is the `BLACKBOX_MCP_ACCESS_TOKEN` repository secret:

```bash
claude mcp add --transport http blackbox https://mcp.anchovy-bot.rdnull.im/ \
  --header "Authorization: Bearer <token>"
```

The deploy workflow creates the server from these repository settings, and refuses to deploy with any secret empty:

| name | kind | what |
|---|---|---|
| `BLACKBOX_MCP_ACCESS_TOKEN` | secret | the bearer token |
| `BLACKBOX_DATABASE_URL` | secret | the read-only user's URI, host `mongo.default.svc.cluster.local:27017` (the user is created as in Option B below) |
| `BLACKBOX_OPENROUTER_API_KEY` | secret | a separate OpenRouter key with a low spend cap, used only to embed search queries |
| `LINODE_DNS_ACCESS_TOKEN` | secret | Linode API token scoped to Domains read/write, for the DNS sync job |
| `BLACKBOX_CHAT_ID` | variable | the default chat for every tool |

**Cluster bootstrap.** This is done once, and before the first deploy that includes `manifests/blackbox/`. It installs Traefik and cert-manager at pinned chart versions, plus the Let's Encrypt issuers. Re-running it is also how a version bump is applied. It needs `helm`:

```bash
KUBE_CONTEXT=anchovy-prod scripts/cluster-bootstrap.sh
```

**Certificates.** The Ingress starts on the Let's Encrypt staging issuer. Before testing the endpoint, check the certificate: a failed challenge and an unrouted request both look like a 404 from outside.

```bash
kubectl --context anchovy-prod -n blackbox get certificate    # READY must be True
```

To move to the production issuer, change the `cert-manager.io/cluster-issuer` annotation in `manifests/blackbox/ingress.yaml` to `letsencrypt-prod`, deploy, then delete the `blackbox-tls` secret so it re-issues.

**Rotating the token.** This is deliberately manual. Between steps 2 and 3, calls fail.

1. Generate a new token and update the `BLACKBOX_MCP_ACCESS_TOKEN` repository secret.
2. Re-run the deploy workflow, then restart the server: `kubectl --context anchovy-prod -n blackbox rollout restart deployment/blackbox`.
3. Update the header in your local Claude Code and Claude Desktop config.

**After a node recycle.** A replacement node gets a new public IP.
- Confirm the `dns-sync` CronJob in `default` updated the record. Its log line reads `action=updated`.
- Confirm the Cloud Firewall is attached to the new node.
- Confirm the block-storage volumes reattached (they are CSI-managed and normally do).

### Local server

The local server reads what `.env.blackbox` (gitignored, in the repository root) points it at: either the stores of the local `docker compose` stack, or production through `kubectl port-forward`. Switching between them means rewriting that file; nothing else changes.

Common to both:

- Build the image once, and again after a requirements change: `docker compose --profile blackbox build blackbox`.
- Every `.env.blackbox` also carries these four lines:
  ```ini
  BLACKBOX_MCP_ACCESS_TOKEN=<any local token>  # the server refuses to start without one
  OPENROUTER_API_KEY=<key>     # search queries are embedded through OpenRouter
  TELEGRAM_TOKEN=unused        # required by settings, never used
  BLACKBOX_CHAT_ID=<chat id>   # default chat for every tool
  ```
- Start the server: `docker compose --profile blackbox up -d blackbox`. It listens on `127.0.0.1:8765` only.
- `.mcp.json` reads the token from your shell, so export the same value before launching Claude Code, then approve the `blackbox` server in `/mcp`:
  ```bash
  export BLACKBOX_MCP_ACCESS_TOKEN=<the value from .env.blackbox>
  ```

### Option A: local Docker stores

This reads the MongoDB and Qdrant containers from `docker-compose.yml`. The server container joins the compose network, so it reaches them by service name, with no port-forward.

1. Start the local stack: `docker compose up -d`. The blackbox service declares no dependencies, so it never starts the stores itself.
2. Create a read-only MongoDB user, authenticating as the local root user from `.env` (`MONGO_INITDB_ROOT_USERNAME`):
   ```bash
   docker compose exec mongo mongosh -u <root user> -p --authenticationDatabase admin \
     --eval 'db.getSiblingDB("admin").createUser({user: "blackbox", pwd: passwordPrompt(), roles: [{role: "read", db: "<DATABASE_NAME>"}]})'
   ```
   For a throwaway local dataset you can reuse the bot's own `DATABASE_URL` from `.env` instead. That credential can write, though, so the read-only guarantee does not hold.
3. Write `.env.blackbox`, using the `DATABASE_NAME` from `.env`:
   ```ini
   DATABASE_URL=mongodb://blackbox:<password>@mongo:27017/?authSource=admin
   DATABASE_NAME=<DATABASE_NAME>
   QDRANT_URL=http://qdrant:6333
   ```

### Option B: production stores through port-forwards

This reads the cluster's MongoDB and Qdrant through `kubectl port-forward`. The server container reaches your machine's forwarded ports through `host.docker.internal`. The hosted server above covers the everyday case, so use this one when you need a local change against real data.

1. Create a read-only MongoDB user in the cluster. The root user comes from `manifests/deployment-mongo.yaml`. The hosted server uses the same user:
   ```bash
   kubectl --context anchovy-prod exec -it mongo-0 -- mongosh -u anchovy -p --authenticationDatabase admin \
     --eval 'db.getSiblingDB("admin").createUser({user: "blackbox", pwd: passwordPrompt(), roles: [{role: "read", db: "<DATABASE_NAME>"}]})'
   ```
2. Write `.env.blackbox`, using the production `DATABASE_NAME`:
   ```ini
   DATABASE_URL=mongodb://blackbox:<password>@host.docker.internal:27018/?authSource=admin&directConnection=true
   DATABASE_NAME=<DATABASE_NAME>
   QDRANT_URL=http://host.docker.internal:6335
   ```
3. For each session, start both port-forwards before using the tools:
   ```bash
   kubectl --context anchovy-prod port-forward svc/mongo 27018:27017
   kubectl --context anchovy-prod port-forward svc/qdrant 6335:6333
   ```
   If the server reports the stores unreachable while the forwards are up, add `--address 0.0.0.0` to both commands. `curl -s -o /dev/null -w '%{http_code}' localhost:8765/readyz` prints `200` once both stores answer.

In either option, check that the credential really is read-only: a write as `blackbox`, for example `db.memory.insertOne({})`, must fail with "not authorized".
