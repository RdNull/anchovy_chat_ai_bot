---
paths:
  - "src/models/**"
  - "src/ai.py"
  - "src/model_manager.py"
  - "evals/**"
---

# LLM stack detail

Loaded automatically when Claude reads a file matching the globs above. Companion to the root `CLAUDE.md`, and to `evals/CLAUDE.md` — that file covers running promptfoo and assessing its output; this one covers model/version pinning and the reply suite's provider internals.

**LLM stack**: `src/ai.py` provides cached model instances. `src/model_manager.py` resolves model configs from `src/models/<task>/<version>.json`. The `"env:VAR_NAME"` syntax in JSON configs interpolates environment variables at load time.

Every config pins `max_retries: 0` and a `timeout` (milliseconds), and `test_every_openrouter_config_bounds_its_transport` refuses a new one that does not. Both are corrections to `langchain-openrouter` defaults that are actively harmful here: `max_retries=2` builds a backoff `RetryConfig` with a **300s** window and `retry_connection_errors=True`, while `request_timeout` defaults to `None`, so a connection-level stall hangs and is then retried for minutes — emitting no httpx log line, because a request that never gets a response has nothing to log. The timeout is tiered by what wraps the call: 60s on `chat`, which sits inside `AI_TIMEOUT` and may be called several times per reply; 120s on the background tasks, which have no wall clock of their own but do hold `CHAT_CONTEXT_LOCK`. A dropped call is cheap by design — the memory watermark only advances on success, so a failed cycle re-reads the same window next time.

Each task pins its version at the call site, not in settings: chat in `character.py:respond` (`_get_llm(versions=('v8',))`), memory `v3-cheap` in `memory/processors.py`, facts `v2` in `facts/processors.py`, initiative `v1` in `initiative/processors.py`; the media describers and `search_web` pass nothing and take the `version='v1'` default in `ai.py`. `Character._get_llm` picks one version at random from the tuple it is given and appends it as a LangSmith tag — a one-element tuple is simply an A/B with one arm. Bumping the chat version means adding the JSON *and* changing that tuple; nothing reads the directory for a newest version.

The reply eval mirrors that config by hand: `evals/reply/character_loop_provider.js` re-implements `_run_llm_loop` against OpenRouter, and each `evals/models/chat/<label>.yaml` restates the model, sampling params, and all seven tool schemas for it. `evals/reply/characters/promptfooconfig.yaml` selects which of those provider files the suite runs, so a chat-model bump that stops there leaves the eval measuring the old model.

Those schemas restate each tool's **description verbatim** from `src/characters/tools/`, not its first line — a truncated description measures a different tool and reports it fine, since half the behaviour under test (when *not* to call, and that fragments are material rather than a reply) lives in the lines after the first. `character_loop_provider.js` returns `metadata.toolsCalled`, which a `javascript` assert reads as `context.metadata` to score the tool choice itself; promptfoo passes an inline `value` as an **expression**, not an arrow function.

One divergence from production is deliberate and will look like a bug to the next reader: `character_loop_provider.js` does **not** mirror the loop's direct-tool failure recovery — the suite grades prompt and tool choice, not loop mechanics, so a direct tool there always succeeds; its `extractAnswer` does know `send_sticker`, because with the schema bound under `tool_choice: required` a sticker call would otherwise run out to `maxIterations` and fail for the wrong reason. Its sentinel format is bracket-free on purpose — `REACTION:<emoji>` / `STICKER:<id>`, not `[reaction: …]` / `[sticker: …]` — because `defaultTest`'s `not-regex` assert exists to catch the model leaking the *input* message format into its reply and stays global, so a gesture answer can't use the same brackets a leak would.

`evals/reply/characters/promptfooconfig.yaml` renders `prompts/v9.js` → `character_setup/v9.j2`, matching production's pin, and every fixture's last message carries a `[TARGET] ` prefix so the suite exercises v9's marked branch rather than "answer the conversation as a whole" (its unmarked one). `tool_callbacks.js:find_stickers` is a fixture corpus (`STICKERS`, a dozen entries) branching on the model's queries the same way `search_web` branches on its query, with one reserved branch that always returns `[]` — the cold-start/no-match state a real search can still land on, and the branch the tool description's «пустой список → отвечай текстом» line depends on. `v8-gemini.yaml` mirrors the production `list[str] | str` as an `anyOf`, and the fixture callback accepts both arms too, so the suite can observe the model emitting a bare string rather than silently normalising it away.
