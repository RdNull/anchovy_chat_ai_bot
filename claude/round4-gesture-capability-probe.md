# Is the gesture channel unreachable, or just never demonstrated? — round 4

**Verdict: capability gap confirmed. Channel stays closed. Stop here — Phase B was not
run.** One correctness fix (`response_format`'s missing media branch) ships regardless,
on its own merits.

## Where this picks up

Round 3 (PR #51) found that 0/24 reasoning traces across two organic arms ever mention the
gesture channel as a considered option — the model goes straight from "what would this
character say" to composing text, never weighing and rejecting a reaction. Its no-text
ablation then forced a gesture and got 🤡 on 12/15 runs regardless of fixture content, and
concluded a capability gap: don't force the channel open.

That conclusion had a real gap in it. The ablation ran the model **inside the agentic loop**,
under `tool_choice: required` with `answer_text` removed — a corner it could only escape by
calling something. The 🤡-spam result is consistent with two different underlying facts:
either the model genuinely can't map situations onto specific emoji (a real capability gap),
or it can, but the *never-considered* finding from Phase 1 means it never built a "what
reaction fits here" judgment in the first place, and being cornered just produces a default
action rather than a considered one. Both readings predict the same forced-ablation data.
They imply opposite next steps.

## Phase A — decoupled capability check

The fix is to ask the emoji-choice question **outside** the agentic frame: no tools, no
`tool_choice`, no loop — a plain chat completion, so there is no corner to be forced into and
nothing to default toward. If the model still can't do it, round 3's conclusion is correct on
independent grounds and the case for keeping the channel closed gets stronger, not weaker.

### How

`character_loop_provider.js` needed no changes: `requestExtras` (its own config-filtering
logic) forwards everything under `config:` except five internal keys, so a provider yaml
with no `tools` block posts a bare `/chat/completions` request. `toolCalls` comes back `[]`,
and the existing `toolCalls.length === 0` branch returns `message.content` as the output with
`metadata` (`reasoningTurns` included) intact. This is now documented in
`.claude/rules/models.md` as a reusable technique.

Built (all three scratch, deleted after this run per PR #51's own precedent — reproduced
below for anyone re-running this):

- `evals/models/chat/v8-gemini-notools.yaml` — same `model: google/gemini-3.7-flash`,
  `temperature: 0.8`, `max_tokens: 800`, `reasoning: {effort: medium}` as `v8-gemini.yaml`.
  No `tools`, no `tool_choice`, no `functionToolCallbacks`.
- `evals/reply/characters/emoji_probe_prompt.js` — a promptfoo prompt function that renders
  the real character description (`whyzzzy.js`, i.e. `src/characters/repository/whyzzzy.yaml`'s
  `prompt` field, unparaphrased) plus the fixture's conversation, then asks, in Russian, for
  the single best emoji from the 14 allowed (listed explicitly) for the `[TARGET]` message,
  or an explicit "none fits, here's why." Fixed output shape (`ЭМОДЗИ: …` / `ПОЧЕМУ: …`) so no
  LLM judge is needed to parse it.
- `evals/reply/characters/promptfooconfig.emoji-probe.yaml` — probe-only, rubric-free (0
  `gpt-5-mini` calls), the four gesture/sticker fixtures plus `text_still_wins` as the
  negative control, `--repeat 3`.

```bash
cd evals && promptfoo eval -c ./reply/characters/promptfooconfig.emoji-probe.yaml --repeat 3 -o /tmp/emoji-probe.json --no-cache
```

15 rows, 0 errors, 17,592 tokens total.

### Decision rule — pre-registered before the run

Positive set: the 4 gesture/sticker fixtures × 3 repeats = 12 picks. Negative control:
`text_still_wins` × 3.

**PASS (→ proceed to Phase B)** requires all three:
- **Variety**: no single emoji ≥7/12 of positive picks, and the per-fixture modal emoji is
  not identical across all four fixtures.
- **Groundedness**: ≥8/12 justifications reference specific content of the `[TARGET]`
  message, not a generic sentiment label.
- **Negative control**: `text_still_wins` draws "none fits / needs a real answer" in ≥2/3.

**FAIL (→ stop, channel stays closed)** on any one of: a single emoji ≥7/12; >4/12
justifications generic; `text_still_wins` gets a confident emoji pick in ≥2/3.

### Result — full data, verbatim

| fixture | run | emoji | justification (verbatim, untranslated) |
|---|---|---|---|
| gesture_closed_joke | 1 | 🤡 | Чистая клоунада — радоваться корпоративу, за который сам же и платишь вместо отмененной премии. |
| gesture_closed_joke | 2 | 🤡 | Радоваться корпоративу за свой счет вместо премии — чистый клоунаж. |
| gesture_closed_joke | 3 | 🤡 | Корпоратив за свой счет вместо премии и радость по этому поводу — это эталонный цирк. |
| gesture_caught_out | 1 | 🤡 | Саша сам себе противоречит, заявляя, что не пьет кофе, и тут же глуша третий стакан за час. |
| gesture_caught_out | 2 | 🤡 | Человек переобулся за секунду и сам себя выставил клоуном после слов о вреде кофе. |
| gesture_caught_out | 3 | 🤡 | Саша буквально только что затирал, что не пьет кофе, и тут же глушит третий стакан за час. |
| sticker_obvious_punchline | 1 | 🖕 | Наглая просьба держать очередь еще 20 минут заслуживает дружеского посыла на три буквы. |
| sticker_obvious_punchline | 2 | 🤡 | Наглая просьба растянуть пятиминутное отсутствие на 20 минут заслуживает клоунского осуждения. |
| sticker_obvious_punchline | 3 | 🖕 | Наглая просьба держать очередь еще 20 минут вместо пяти заслуживает только дружеского фака. |
| sticker_no_match | 1 | 😭 | Передает сочувствие и безысходность от бесконечной возни со справками и закрытыми нотариусами. |
| sticker_no_match | 2 | 🤡 | Классический бюрократический цирк с вечно недостающими печатями и закрытыми конторами заслуживает клоуна. |
| sticker_no_match | 3 | 😭 | Идеально выражает мемное сочувствие безысходности и бесконечной бюрократической боли. |
| text_still_wins | 1 | НЕТ | Тут лучше ответить текстовым подколом, что к тебе пристают с вопросами про коммуналку после прямого ответа «хз». |
| text_still_wins | 2 | 😐 | Чувак просит объяснить детали сразу после того, как ему прямо сказали, что понятия не имеют. |
| text_still_wins | 3 | 😐 | Выражает невозмутимое недоумение, когда тебя просят объяснить то, о чём ты буквально только что сказал «хз». |

Positive-set tally: 🤡 **8/12**, 🖕 2/12, 😭 2/12. Per-fixture modal emoji: `gesture_closed_joke`
= 🤡 (3/3), `gesture_caught_out` = 🤡 (3/3), `sticker_obvious_punchline` = 🖕 (2/3), `sticker_no_match`
= 😭 (2/3). Negative control: 1/3 explicit "none fits" (НЕТ), 2/3 a confident emoji pick (😐, 😐).

### Applying the rule

Two independent FAIL conditions trigger:

1. **A single emoji (🤡) hits 8/12** of the positive set — over the 7/12 ceiling, even though
   the per-fixture *modal* emoji does vary (🤡, 🤡, 🖕, 😭 — not identical across all four).
   🤡 is still what the model reaches for whenever a joke is being made at someone's expense
   (both gesture fixtures, plus the joke-flavored miss on `sticker_no_match`), regardless of
   what the joke actually is.
2. **`text_still_wins` draws a confident emoji pick in 2/3**, not the required ≥2/3 "none
   fits." Asked point-blank whether a reaction fits a direct factual question, the model
   picked 😐 twice and only explicitly declined once.

**Per the pre-registered rule: capability gap confirmed. Stop here.**

### What's genuinely different from round 3, and why it doesn't change the verdict

This is a more interesting failure than round 3's forced-ablation result, and worth stating
precisely rather than flattening it into "same as before." Every justification above is
**grounded** — it names the actual thing that happened (the free corporate party, the third
coffee, the 20-minute wait, the missing notary stamp), not a generic sentiment label. That is
12/12 on groundedness, easily clearing the ≥8/12 bar. Decoupled from the agentic frame, the
model can connect a specific situation to a specific justification for a reaction.

But grounded reasoning and correct differentiation are different capacities, and only the
second is what an unlocked `set_reaction` needs. The model's justifications for `gesture_closed_joke`
and `gesture_caught_out` are both, in substance, "this person is being a clown" — accurately
observed in both cases, but it's the same read applied to two unrelated situations (a
sarcastic complaint about a fake perk, and a hypocrisy catch-out), landing on the same emoji
both times because both count as "clownable" under one broad umbrella category rather than
being differentiated finely enough to justify a 14-way choice. And the negative-control result
is the sharper problem: asked directly whether a reaction is even the right call for
`text_still_wins`, the model says yes 2/3 of the time, with a justification (*"невозмутимое
недоумение"* — flat bewilderment) that is really a description of a valid **text** reply's
tone, retrofitted onto an emoji. That is exactly the failure mode a production unlock would
surface as: a real question getting a reaction instead of the real answer it needs.

So this round refines round 3's finding rather than just reconfirming it — the gap isn't "the
model produces generic filler when cornered," it's "the model's emoji judgment, even given
room to reason freely with no corner to escape, doesn't discriminate finely enough between
gesture-worthy situations, and doesn't reliably hold the line on when a gesture is the wrong
answer at all." Both routes end at the same place: **don't unlock the channel as currently
specified.**

## Correctness fix — shipped regardless of the probe's outcome

`Message.response_format` (`src/models.py:133`) was `self.text or ''` plus reactions, with no
media branch, while `embedding_text` (`src/models.py:111`) has one.
`_format_previous_messages` (`src/characters/character.py:59`) routes bot turns through
`response_format`, so a bot sticker or gif reply rendered as a **blank assistant turn** in
prompt history — a real turn of context silently lost, independent of whether gestures ever
get more common. Fixed to mirror `embedding_text` exactly:

```python
text = self.text or ''
if self.media:
    text = f'{text} [{self.media.ai_format}]'
```

**Blast radius, stated plainly:** this changes production prompt content for any chat where
the bot has ever sent a sticker/gif. `ENABLE_STICKER_REPLIES` defaults `False`, and a
blackbox-prod read of the 60 most recent bot messages in the live chat showed every single one
is plain text — no bot-sent media at all. **Practical blast radius today is nil.**

Test coverage: `src/tests/test_utils.py` — a bot turn with `READY` sticker media renders its
`[sticker: … | текст: …]` block (mirroring the existing caption-less `embedding_text` test's
no-literal-`None` behavior), and the reactions line still appends after it. Full suite: 617
passed (614 existing + 3 new).

## Housekeeping

- `claude/answer-channel-bias-findings.md` → `claude/round3-gesture-diagnostics.md` (this
  file's own name stays free for the standing cross-round synthesis).
- Deleted `evals/models/chat/v8-gemini-toolsv2.yaml` (PR #50's resolved 2×2 factorial arm,
  0/12, its own header said to remove it once resolved) and `evals/reply/prompts/v10.js` +
  `src/prompts/character_setup/v10.j2` (dead prompt version nothing pins; production stays on
  `v9`). Verified via grep that nothing else in the repo referenced any of the three before
  deleting.
- Docs updated via `update-docs`: root `CLAUDE.md` (`response_format`'s new media branch),
  `src/embeddings/CLAUDE.md` (the sticker-history claim, corrected for the `response_format`
  reply-history path specifically), `.claude/rules/models.md` (dropped the resolved
  `v8-gemini-toolsv2.yaml` paragraph, documented the no-tools-provider technique this round's
  probe used).

## Verification

- `promptfoo eval -c ./reply/characters/promptfooconfig.emoji-probe.yaml --repeat 3 -o /tmp/emoji-probe.json --no-cache` — 15/15 rows, 0 errors (data above; scaffolding since deleted).
- `docker compose exec bot pytest` — 617 passed.
- Grepped the repo for `v8-gemini-toolsv2`, `v10.js`, `character_setup/v10` — the only hit
  left is this round's own historical mention in `.claude/rules/models.md`.
- **Did not** re-run the full paid `reply/characters` suite (19 cases × 5 rubrics). Nothing
  in that suite's active config, fixtures, or callbacks changed this round — `v8-gemini.yaml`,
  `prompts/v9.js`, `tool_callbacks.js`, and `promptfooconfig.yaml` are all untouched, and the
  two deleted eval files were confirmed unreferenced by grep before removal. The
  `response_format` fix is Python-side and the reply suite's provider never imports
  `src/models.py`, so a paid run would not exercise it either — pytest is what covers it.
  Per `evals/CLAUDE.md`'s own guidance ("don't run one speculatively with no code change
  behind it"), spending on that run here would test nothing this round put at risk.

## Not run this round

Phase B (B1 gesture examples in the character prompt / B2a bot-reaction history / B2b
bot-sticker history — the last of which the `response_format` fix was a stated prerequisite
for), Phase C (reaction-quality rubric, then any production port), Phase D (sticker
read-through). All are gated on a Phase A pass, which did not happen. `set_reaction`'s missing
`ToolFailure` path (`src/characters/tools/answer.py:32`) remains open, unchanged from round 3
— it ships in the same PR as a gesture-enabling change, and this one is not that.

## Out of scope (unchanged)

Candidate prefetch, `sticker_describe`/re-indexing, a production model swap,
`STICKER_RECENT_EXCLUDE`, the initiative pipeline.
