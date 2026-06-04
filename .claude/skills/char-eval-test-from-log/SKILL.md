---
name: char-eval-test-from-log
description: >
  Use this skill when the user pastes a langchain/langsmith messages dump (a
  `messages` array of LangChain SystemMessage/HumanMessage/AIMessage entries
  serialized via `lc: 1, type: "constructor"`) and asks to turn it into a
  character response eval test case under `evals/reply/characters/`.
  Handles message extraction, nickname anonymization, file scaffolding, and
  `promptfooconfig.yaml` registration.
disable-model-invocation: false
---

## Inputs

The user provides:
1. A **test name** in kebab-case, e.g. `crime-assignment`. This becomes the
   directory name and the `description:` in promptfooconfig.yaml.
2. A **langchain/langsmith JSON dump** with a `messages` field. Format is
   nested arrays of objects shaped like:
   ```json
   {
     "id": ["langchain", "schema", "messages", "HumanMessage"],
     "kwargs": { "content": "...", "type": "human" },
     "lc": 1,
     "type": "constructor"
   }
   ```
   `id[3]` is one of `SystemMessage`, `HumanMessage`, `AIMessage`.

If either is missing, ask before proceeding.

## What to produce

1. `evals/reply/characters/<test-name>/messages.json` — a flat array of
   `{role, content}` objects (promptfoo chat format).
2. An entry appended to the `tests:` block of
   `evals/reply/characters/promptfooconfig.yaml`:
   ```yaml
     - description: "<test-name>"
       vars:
         messages: file://<test-name>/messages.json
   ```

## Conversion rules

- **Drop the SystemMessage**. The eval already injects the system prompt via
  `src/prompts/character_setup/v6.j2` — duplicating it in the test breaks
  the prompt rendering.
- **HumanMessage → `{role: "user", content: <kwargs.content>}`**, preserving
  the original string exactly (timestamps, `[image: ...]`, `[gif: ...]`,
  `[PROCESSING]`, multiline content, etc.). The bot's `ai_format` is already
  baked into the content.
- **AIMessage → `{role: "assistant", content: <kwargs.content>}`**. If the
  AIMessage has tool_calls and empty content, skip it — those represent the
  bot's intermediate context-tool calls, not its final reply.
- Preserve message order. Don't collapse consecutive same-role messages.
- Don't reformat content — no trimming, no escaping changes beyond what JSON
  requires.

## Nickname anonymization

The user typically wants real chat participants anonymized. Apply a
**consistent mapping** across:
- The `username:` prefix inside each message's `content`.
- Any `@mentions` inside content.
- First-name references / transliterations of the same person (e.g.
  Kazakh-language messages may address `bekniyazzz` as `Бека` → if `bekniyazzz`
  becomes `kolya`, then `Бека` becomes `Коля`).

**Default fake pool** (matches names already used in
`src/characters/repository/whyzzzy.yaml` examples and existing tests, so the
character's example replies remain coherent):
`kolya`, `sasha`, `misha`, `dima`, `Biba`, `Boba`.

Pick one fake per real user in the order they first appear in the log. Show
the mapping to the user before writing files when more than three users are
remapped, so they can override.

**Exceptions — don't anonymize:**
- `ShizoDedAnchovyBot` (the bot itself). Leave as-is.
- The character's target user from the character yaml (currently `whyzzzy` in
  `whyzzzy.yaml`) — if you anonymize them, the character's "адаптируйся под
  @whyzzzy" instruction has no anchor in the test. Keep the original handle
  unless the user explicitly asks to rename.

## Workflow

1. Parse the dump and list the distinct real usernames you found (extract
   from the `username:` prefix of each HumanMessage and any `@mentions`).
2. Propose a mapping. For ≤3 users, just apply defaults silently; for more,
   show the mapping and confirm.
3. Write `messages.json` to the new directory using the Write tool. Don't
   `mkdir` first — Write creates parent dirs.
4. Edit `promptfooconfig.yaml` to register the test (Edit tool, append after
   the last `- description:` block under `tests:`).
5. Report: test path, mapping used, count of messages written.

## Don't

- Don't run promptfoo (`promptfoo eval`) — the user runs evals themselves.
- Don't modify `whyzzzy.js`, the model YAMLs, the prompt template, or the
  custom provider — those are out of scope for adding a test case.
- Don't rename existing test directories.
- Don't try to invent additional `assert:` entries for the test — assertions
  are shared via `defaultTest:` in `promptfooconfig.yaml`.
