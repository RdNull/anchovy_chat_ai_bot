# Prompt evaluation detail

Loaded automatically when Claude reads a file under `evals/`. Companion to the root
`CLAUDE.md`'s **Commands** section and to `.claude/rules/models.md` (which loads on the
same paths and covers model/version pinning and the reply suite's provider internals —
this file is about *operating* promptfoo, not about what a given suite measures).

## Claude is allowed to run these

Running a suite is a **regular** action, not one that needs confirmation first: it costs
the user's own OpenRouter spend but touches no chat data, no production system, and
nothing irreversible. Run a suite whenever verifying a prompt/tool/eval change calls for
it — after editing a `promptfooconfig.yaml`, a fixture, a callback, or the prompt template
a suite renders. Don't run one speculatively with no code change behind it, and don't loop
`promptfoo eval` retrying a paid run because a first pass wasn't the answer you expected —
diagnose from the output first (see **Assessing results**), rerun only once the input
actually changed.

## How to run

**Always `cd` into `evals/` first, not into the suite's own subdirectory.** Every suite
here calls OpenRouter — either through promptfoo's built-in `openrouter:` provider id
(`facts`, `memory`, `initiative`, `recap`, `image_description`) or through the custom
`character_loop_provider.js` (`reply/characters`, `reply/setup`) — and both read
`OPENROUTER_API_KEY` from `evals/.env`, which promptfoo/dotenv only picks up when the
process's cwd is `evals/`. Running from inside a suite directory silently misses the
key and every case errors with `Missing OPENROUTER_API_KEY env var` — that error means
"wrong cwd", not "no key configured".

```bash
cd evals
promptfoo eval -c ./reply/characters      # reply/characters and reply/setup are separate suites
promptfoo eval -c ./memory
promptfoo eval -c ./facts
promptfoo eval -c ./initiative
promptfoo eval -c ./recap
promptfoo eval -c ./image_description
```

`evals/.env` itself cannot be read directly (blocked by this session's own rules) — that's
fine, nothing here needs its contents, only its presence in the right working directory.

### Scoping a run (cost control)

Every case is at minimum one provider call plus several `llm-rubric` judge calls (one
`gpt-5-mini`/`gpt-5` call per rubric per case — the `reply/characters` suite alone runs
five rubrics). A full suite is a real, billed batch. While iterating on one case or one
fixture, scope the run instead of paying for the whole suite each time:

```bash
promptfoo eval -c ./reply/characters --filter-pattern '^gesture_closed_joke$'
promptfoo eval -c ./reply/characters --filter-first-n 3
promptfoo eval -c ./reply/characters --filter-range 0:5
```

`reply/characters` (and its `promptfooconfig.gestures.yaml` probe) grades one character per
run, chosen by the `CHARACTER` env var (a code from `src/characters/repository/`, default
`whyzzzy`; read by `reply/characters/character.js`). Never run every character on every
iteration: give a new character a scoped smoke run, and the full suite once per character
before calling it verified.

```bash
CHARACTER=chatzhpt promptfoo eval -c ./reply/characters --filter-pattern '^basic$'
```

The `web_search_*`, `gesture_*` and `sticker_*` cases assert tool choice tuned on whyzzzy's
persona, so a red one on another character may be a persona difference, not a regression.

Run the full, unscoped suite at least once before reporting a change verified — a
scoped run only tells you the case you touched didn't break, not that nothing else did.

### Getting machine-readable output

The terminal table truncates long outputs and doesn't show per-assertion scores. Always
pair a run meant for assessment with `-o`:

```bash
promptfoo eval -c ./reply/characters -o /tmp/eval-result.json --no-cache
```

`--no-cache` matters here: promptfoo caches provider+prompt+assertion results by default,
so a rerun after only changing rubric wording (not the fixture or provider) can silently
replay a stale grade. Use it whenever the assertions themselves changed, not just when a
fixture did.

## Assessing results

Don't rely on the terminal summary (`N passed / N failed`) alone — read the JSON:

```js
const data = require('/tmp/eval-result.json');
// Rows come back grouped by provider only through `promptIdx` (an index into
// `data.results.prompts`, in `providers:` declaration order) — `r.provider.label` is not
// the per-provider-file label from the yaml, it's always the shared `.js` provider path.
for (const r of data.results.results) {
  console.log(r.testCase.description, 'promptIdx', r.promptIdx, '->', r.response.output);
  console.log('  toolsCalled:', r.response.metadata?.toolsCalled);   // reply suite only
  for (const t of r.response.metadata?.reasoningTurns || []) {       // reply suite only
    console.log('  turn:', t.tools, t.finishReason, t.reasoningTokens, '->', t.reasoning);
  }
  for (const c of r.gradingResult.componentResults) {
    console.log('  ', c.assertion?.type, c.assertion?.metric, c.score, c.pass);
  }
}
```

- `response.output` — what the provider actually returned (the model's final answer, or a
  gesture sentinel like `REACTION:<emoji>` / `STICKER:<id>` for the reply suite).
- `response.metadata.toolsCalled` — reply suite only, from `character_loop_provider.js`:
  every tool name the model invoked, in order. This is what a `channel-choice` or
  `tool-choice` `javascript` assert actually reads (`context.metadata.toolsCalled`).
- `response.metadata.reasoningTurns` — reply suite only, one entry per loop turn
  (`tools`, `finishReason`, `reasoningTokens`, `reasoning`, `content`, `args`). Nothing
  asserts on it; it exists for you to read when a `toolsCalled` result needs an explanation
  rather than a score — e.g. *why* the model didn't call a tool it was expected to. Check
  `finishReason` before reading `reasoning` as intent: a turn cut short (not `'tool_calls'`)
  means `max_tokens` (shared with the thinking budget on a reasoning model) starved the
  turn, which is a mechanical explanation, not a preference one.
- `gradingResult.componentResults[]` — one entry per `defaultTest`/per-case assert, each
  with its own `score` (0–1) and `pass` (the suite's pass threshold, not necessarily
  `score === 1`). A `llm-rubric` failing is a model/prompt finding; a `word-count` or
  `not-regex` failing is almost always a fixture or harness problem.

### When the production rubrics can't answer the question

`defaultTest`'s five `llm-rubric` asserts in `reply/characters` are written for *scoring a
reply*, and some of that wording deliberately flattens gesture cases (a forced `REACTION:`/
`STICKER:` gets a fixed 0.5 or 1.0 by rubric instruction — see the assert text itself). That
makes them the wrong tool for a diagnostic question like "did the model even consider this
tool" — they can't discriminate it, and every rubric call is a paid `gpt-5-mini` request.

For that kind of run, park a scratch `promptfooconfig.<name>.yaml` beside the real one (not
`promptfooconfig.yaml` itself — promptfoo's config discovery joins the exact filename, so a
differently-named file is invisible to `-c ./reply/characters` and needs no unwiring before
merge) with the same prompt/fixtures/provider shape but a `defaultTest.assert` trimmed to
only the mechanical asserts a `channel-choice`/`tool-choice` question needs — typically just
the `javascript` assert reading `metadata.toolsCalled`. Delete it once the question resolves,
alongside any scratch provider yaml it points at.

**Check the case's own comment before treating a red row as a regression.** Several
suites keep a case deliberately red on purpose — e.g. `reply/characters`'s
`web_search_stale_belief` is commented `KNOWN RED, on purpose`, a standing probe of a gap
the prompt hasn't closed yet. A newly-red case with no such comment is a real finding to
report; a case that's always been red and says so is not.

**A `0 errors` run with everything answering as expected is not automatically "nothing to
report".** The interesting result of the suite this file's sibling task added was that
every case answered as `answer_text` — zero `set_reaction`/`send_sticker` calls even in
cases built to make a gesture the obvious right answer. That's a model/prompt finding
surfaced *by* a correctly-passing harness, not a harness bug; don't mistake "the harness
found nothing wrong with itself" for "the model behaved as hoped".

## Verifying a harness change itself (not a prompt/model question)

When the thing under test is the eval harness — a new assertion, a rewritten callback, a
changed rubric — a real provider run answers "does the model do X", not "does the harness
score X correctly". Force the specific output you need to check and read the assertion
result directly, rather than hoping a real case happens to produce it:

- A temporary env-var or hardcoded branch in the custom provider (see
  `reply/character_loop_provider.js`) to force a specific `output`, scoped to one case via
  `--filter-pattern`, then revert the patch before reporting.
- For a static-only check (regex/word-count math, JSON fixture shape, YAML parsing), a
  throwaway `node -e` snippet is cheaper than a real API call and doesn't touch
  `OPENROUTER_API_KEY` at all — reach for a live run only for what only a live run can
  tell you (whether the model actually calls a tool, whether an `llm-rubric` judge scores
  a given output the way the rubric wording intends).

Never leave a forcing patch in the diff — confirm with `git diff <file>` that it's gone
before reporting the verification done.

## `promptfoo view`

Opens a local results browser. It works, but it's a server the agent can't usefully drive
headlessly — prefer the `-o`/JSON route above for anything you need to reason about
yourself, and mention `promptfoo view` to the user only when they want to look at a run
themselves.
