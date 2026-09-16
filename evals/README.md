# Promptfoo prompt evaluation

## Quick start

1. Set your API key — the custom providers in this repo (`character_loop_provider.js`,
   used by `reply/characters` and `reply/setup`) call OpenRouter directly, not promptfoo's
   built-in provider layer:

```bash
export OPENROUTER_API_KEY=sk-or-...
```

2. Pick a suite and run it from its own directory (each `promptfooconfig.yaml` lives one
   level below `evals/`, e.g. `evals/reply/characters/`, `evals/memory/`):

```bash
cd reply/characters && promptfoo eval
```

3. View results in your browser:

```bash
promptfoo view
```

## Layout (`evals/reply/`)

- `character_loop_provider.js` — a custom provider that re-implements the production tool
  loop (`_run_llm_loop`) against OpenRouter's chat-completions endpoint, since promptfoo's
  built-in providers don't run an agentic tool loop.
- `tool_callbacks.js` — fake callbacks for every tool the loop can call, shared by
  `reply/characters` and `reply/setup`. `find_stickers` is a small fixture corpus
  (`STICKERS`), not a real Qdrant search — it branches on the model's queries the same way
  the `search_web` callback branches on its, with one reserved branch that always returns
  `[]` (the no-match/cold-start state the tool's own description has to handle).
- `prompts/v<N>.js` — one file per prompt version, rendering the matching
  `src/prompts/character_setup/v<N>.j2`. `characters/promptfooconfig.yaml` currently points
  at `v9.js`, matching what production ships.
- `characters/*/messages.json` — fixtures. Each conversation's last message carries a
  `[TARGET] ` prefix, matching how production marks the message being answered
  (`src/characters/character.py`) — without it, v9's prompt takes its unmarked branch
  ("answer the conversation as a whole") instead of the one production actually uses for a
  mention or reply.

### Answer sentinels

A reply can be text, a reaction, or a sticker. `extractAnswer()` renders the latter two as
`REACTION:<emoji>` / `STICKER:<id>` — bracket-free on purpose, so they don't trip
`defaultTest`'s `not-regex` assert (which exists to catch the model leaking the *input*
message format into its reply, and has to stay global rather than be scoped per case). The
`llm-rubric` assertions are told about this format so a gesture answer can score on its own
terms instead of failing rubrics written for prose.

## Learn more

- Configuration guide: https://promptfoo.dev/docs/configuration/guide
- All providers: https://promptfoo.dev/docs/providers
- Assertions & metrics: https://promptfoo.dev/docs/configuration/expected-outputs
- Examples: https://github.com/promptfoo/promptfoo/tree/main/examples
