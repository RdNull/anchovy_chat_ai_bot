---
paths:
  - "src/models/**"
  - "src/ai.py"
  - "src/model_manager.py"
  - "evals/**"
---

# LLM stack detail

Loaded automatically when Claude reads a file matching the globs above. Companion to the root `CLAUDE.md`.

**LLM stack**: `src/ai.py` provides cached model instances. `src/model_manager.py` resolves model configs from `src/models/<local|cloud>/<task>/<version>.json`. The `"env:VAR_NAME"` syntax in JSON configs interpolates environment variables at load time. Toggle local vs cloud with `IS_LOCAL`.

Every `cloud` config pins `max_retries: 0` and a `timeout` (milliseconds), and `test_every_openrouter_config_bounds_its_transport` refuses a new one that does not. Both are corrections to `langchain-openrouter` defaults that are actively harmful here: `max_retries=2` builds a backoff `RetryConfig` with a **300s** window and `retry_connection_errors=True`, while `request_timeout` defaults to `None`, so a connection-level stall hangs and is then retried for minutes — emitting no httpx log line, because a request that never gets a response has nothing to log. The timeout is tiered by what wraps the call: 60s on `chat`, which sits inside `AI_TIMEOUT` and may be called several times per reply; 120s on the background tasks, which have no wall clock of their own but do hold `CHAT_CONTEXT_LOCK`. A dropped call is cheap by design — the memory watermark only advances on success, so a failed cycle re-reads the same window next time. The `local` (ollama) configs are untouched: different SDK, different parameters.

Each task pins its version at the call site, not in settings: chat in `character.py:respond` (`_get_llm(versions=('v8',))`), memory `v3-cheap` in `memory/processors.py`, facts `v2` in `facts/processors.py`, initiative `v1` in `initiative/processors.py`; the media describers and `search_web` pass nothing and take the `version='v1'` default in `ai.py`. `Character._get_llm` picks one version at random from the tuple it is given and appends it as a LangSmith tag — a one-element tuple is simply an A/B with one arm. Bumping the chat version means adding the JSON *and* changing that tuple; nothing reads the directory for a newest version.

The reply eval mirrors that config by hand: `evals/reply/character_loop_provider.js` re-implements `_run_llm_loop` against OpenRouter, and each `evals/models/cloud/chat/<label>.yaml` restates the model, sampling params, and all five tool schemas for it. `evals/reply/characters/promptfooconfig.yaml` selects which of those provider files the suite runs, so a chat-model bump that stops there leaves the eval measuring the old model.

Those schemas restate each tool's **description verbatim** from `src/characters/tools/`, not its first line — a truncated description measures a different tool and reports it fine, since half the behaviour under test (when *not* to call, and that fragments are material rather than a reply) lives in the lines after the first. `character_loop_provider.js` returns `metadata.toolsCalled`, which a `javascript` assert reads as `context.metadata` to score the tool choice itself; promptfoo passes an inline `value` as an **expression**, not an arrow function.

Two divergences from production are deliberate and will look like bugs to the next reader. `character_loop_provider.js` does **not** mirror the loop's direct-tool failure recovery — the suite grades prompt and tool choice, not loop mechanics, so a direct tool there always succeeds; its `extractAnswer` does know `send_sticker`, because with the schema bound under `tool_choice: required` a sticker call would otherwise run out to `maxIterations` and fail for the wrong reason. And `evals/reply/characters/promptfooconfig.yaml` still renders `prompts/v7.js` → `character_setup/v7.j2` while `character.py:system_message` pins **v9**, so nothing in `evals/` exercises the sticker guidance added in v8 or the `[TARGET]` dialogue rule added in v9 — the suite's fixtures carry no marker, which is v9's unmarked branch. `tool_callbacks.js:find_stickers` returns `[]` on purpose and ignores its `queries`: that is the cold-start state the bot ships in and the branch the tool description has to handle, and it keeps every existing case answering with text. `v8-gemini.yaml` mirrors the production `list[str] | str` as an `anyOf`, so the suite can observe the model emitting a bare string rather than silently normalising it away.
