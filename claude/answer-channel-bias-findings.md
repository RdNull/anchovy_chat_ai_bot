# Why the bot never calls `set_reaction` (or `send_sticker`) — round 3

Diagnostic-only. **No production change ships from this round.** `character_setup` stays
pinned to v9; `src/characters/tools/`, `src/characters/character.py`, and
`evals/models/chat/v8-gemini.yaml`'s tool order are all untouched.

Prior rounds (PR #49, PR #50) established the harness is fair and that wording changes —
prompt template (Arm P) and tool descriptions (Arm T) — move nothing: 0/12 cells in a 2×2
factorial. This round stopped guessing at wording and instead (a) read the model's own
reasoning, which two prior rounds threw away, and (b) ran two structural probes.

## Phase 0 — the enum confound: resolved without a re-run

`v8-gemini.yaml`'s `set_reaction.emoji` gained its 14-item `enum` in `54ebfb3`, the same
commit as PR #50's arms, so git history alone can't order the enum against the 12 factorial
cells. The commit message resolves it directly:

> Phase 0 … the eval provider's `set_reaction` schema had no emoji enum … **Fixed in
> `v8-gemini.yaml` before baselining**, so the factorial measures a faithful mirror.

**PR #50's reaction cells ran with the enum present.** The 0/12 result is not an artefact.
This round's control arm (current `main`, same enum) reconfirms it for free: 0/9 gestures
across both gesture fixtures.

Also worth stating precisely: the harness cannot be swallowing a gesture call.
`character_loop_provider.js` pushes onto `toolsCalled` (line 116, now ~139) *before*
`extractAnswer` runs, so a `set_reaction` call with bad/unparseable args would still show up
in `metadata.toolsCalled`. It never has, in any round. The model genuinely never emits the
call under normal conditions.

## The instrument: reasoning capture

`chat/v8` runs `reasoning: { effort: medium }`, and `character_loop_provider.js` now
captures `message.reasoning` / `message.reasoning_details` per loop turn into
`metadata.reasoningTurns`, alongside `finishReason` and `reasoningTokens`. Verified working
on the first smoke run: `google/gemini-3.7-flash` via OpenRouter returns readable
thought-summary text (English prose with bold section headers, Gemini's standard
summarized-thinking format) — **no fallback self-report probe was needed.**

`finishReason` was `tool_calls` on every single turn across all 45 rows (`--repeat 3`,
3 arms × 5 fixtures); `reasoningTokens` peaked at 425 of the 800-token budget. **No turn was
truncated.** This rules out the mechanical hypothesis that `max_tokens: 800` (shared between
completion and thinking) was starving the model of room to consider a gesture — the model
had budget to spare and still didn't reach for one.

## Phase 1 — what the model actually says: it never considers a gesture at all

Scanned all 24 reasoning traces from the two organic arms (control + 2a) on the four
gesture/sticker fixtures, for any mention of the reaction/sticker concept. After filtering
two keyword false-positives (`react` inside "how Elzhan would **react**", "**Provoking
Reaction**" meaning provoking a reply) — **0/24 traces mention the gesture channel as a
considered option.** The model doesn't weigh and reject a reaction on stated grounds; the
option simply never surfaces in its visible thinking. It goes straight to composing a text
punchline, every time.

Representative quotes, verbatim, untranslated (all `finish: tool_calls`, no truncation):

`gesture_caught_out`, control arm — the fixture built specifically so a reaction ("caught
you mid-contradiction") should be the obvious answer:

> **Brewing Coffee, Contradicting Self**
>
> Initially, I claimed to avoid coffee for my heart. Now, my thoughts are entirely consumed
> by its absence and a desperate need for a refill.
>
> **Considering Coffee, Reconsidering Self**
>
> My internal monologue is now a rapid-fire debate on coffee consumption, pivoting from
> initial health concerns to a surprisingly swift rationalization. I'm exploring humorous,
> self-deprecating quips, leaning towards concise jabs like "heart said bye" or a slightly
> more sarcastic "good for the heart."

`gesture_closed_joke`, control arm — the fixture where the other person already landed their
own punchline:

> **Considering Elzhan's Cynicism**
>
> I'm thinking about how Elzhan would react to Biba's comment. He'd likely be dismissive,
> perhaps sarcastically calling the boss "generous" and then suggesting everyone chip in to
> "have fun."

Both traces go directly from "what would this character say" to a text punchline. Neither
entertains "or: just react" as a branch.

## Phase 2a — tool declaration order: null result, position bias ruled out

New scratch provider `evals/models/chat/v8-gemini-order.yaml`: byte-identical to
`v8-gemini.yaml` except the answer block is declared `set_reaction`, `send_sticker`,
`answer_text` instead of `answer_text`, `set_reaction`, `send_sticker` — `answer_text` moved
from first-of-three to last-of-three, the maximal version of the single variable (a bare
swap would only move it first→second). Context-tool order held fixed, since hoisting it too
would also change `find_stickers`' fire rate and confound the sticker read.

**Result: 0/15 gestures, identical to control's 0/15.** `--repeat 3` × 5 fixtures.

| fixture | control | order (2a) |
|---|---|---|
| `gesture_closed_joke` | 0/3 | 0/3 |
| `gesture_caught_out` | 0/3 | 0/3 |
| `sticker_obvious_punchline` | 0/3 send_sticker | 0/3 send_sticker |
| `sticker_no_match` | 0/3 find_stickers | 0/3 find_stickers |
| `text_still_wins` | 3/3 answer_text (pass) | 3/3 answer_text (pass) |

Declaring `answer_text` last among the answer tools made zero difference to anything,
including `find_stickers`' fire rate. **Position/primacy bias is ruled out as the cause.**
If this hadn't been tested, it would have remained PR #50's own flagged "untested hypothesis
#1" indefinitely — now it's closed.

## Phase 2b — no-text ablation: capability gap, not preference-ranking

New scratch provider `evals/models/chat/v8-gemini-notext.yaml`: `v8-gemini.yaml` with the
`answer_text` tool block removed outright, `send_sticker` kept (this is a no-text ablation,
not a reaction-only arm — dropping `send_sticker` would have thrown away
`sticker_obvious_punchline`, the one fixture with a real punchline sticker in the corpus).
Under `tool_choice: required` the model must call something; this asks a capability question
the organic arms cannot: forced off text, is the emoji it reaches for well-chosen?

**Result: 15/15 forced onto a gesture, but 12/15 (80%) landed on the exact same emoji, 🤡 —
regardless of fixture content.**

| fixture | outputs (3 repeats) |
|---|---|
| `gesture_closed_joke` | 🤡, 🤡, 🤡 |
| `gesture_caught_out` | 🤡, 🤡, 🤡 |
| `sticker_obvious_punchline` | 🖕, 🤡, 🗿 |
| `sticker_no_match` | 🤡, **STICKER** (see below), 🤡 |
| `text_still_wins` | 🤡, 🤡, 🤡 |

That's identical output across a corporate-bonus joke, a coffee-hypocrisy catch-out, and a
factual ЖКХ utility-meter question — three semantically unrelated fixtures, one emoji. Per
the decision rule fixed before this run (≥2/3 apt + different emoji across unrelated
fixtures → preference-ranking problem; mostly generic/repeated → capability gap), **this is
a capability gap, not a preference-ranking problem.**

The clearest single piece of evidence is the reasoning trace on `text_still_wins` (a
fact-question fixture where a real answer is needed) — even with `answer_text` unavailable,
the model's thinking composes specific, in-character **text**:

> **Defining Response Strategies**
>
> I'm exploring various witty and sarcastic retorts, aiming for concise, casual, and
> slightly rude banter that fits Elzhan's persona. Options like "Am I a utility consultant?"
> or "Stick a magnet on it and don't sweat it" are under consideration.

— and then, lacking the tool to say it, discards both lines for a bare `set_reaction(🤡)`.
Every no-text reasoning trace follows this shape: it plans a **punchline**, in prose, and
only then bolts a generic reaction onto the end because nothing else is callable. Not one of
the 15 traces reasons about *which emoji* fits the beat. 🤡 functions as a null/filler action
when the preferred channel is unavailable, not a targeted reaction to what was actually said.

`sticker_obvious_punchline` reinforces this: despite `send_sticker` being available and this
being the one fixture with a matching corpus sticker, **0/3 no-text traces even attempt
`find_stickers`** — straight to a generic reaction each time.

The one `STICKER:` output on `sticker_no_match` is not a counter-example: the queries behind
it were generic reaction-image concepts (`клоун`, `facepalm`, `грустный кот`), not anything
about the bureaucracy setup, and one happened to match an unrelated corpus entry. It's the
same "reach for a generic filler" behavior, just landing on `find_stickers` that one time
instead of `set_reaction` directly.

**Verdict, stated plainly per the task brief's own acceptance criterion:** forced reactions
are not any good. The honest conclusion is to **leave the channel closed rather than force it
open.** Unlocking `set_reaction` as currently specified would produce a bot that reaction-spams
🤡 indiscriminately across unrelated situations — a worse outcome than the current all-text
behavior, not a neutral one.

## Phase 4 — the sticker read-through

Both organic arms (control, 2a) ran the sticker fixtures in the same batch as the gesture
fixtures — no separate run needed.

- **`sticker_obvious_punchline`: 0/6 `send_sticker`, 0/6 even `find_stickers`**, across both
  arms.
- **`sticker_no_match`: 0/6 `find_stickers`**, across both arms — consistent with PR #50's
  finding that this tool never fires under normal conditions.

Since neither 2a nor 2b produced an organic (non-forced) movement in the reaction channel,
Phase 4's original framing — "whatever moves reactions, re-run stickers under that change" —
doesn't strictly trigger; nothing moved reactions in a production-relevant sense. But the
data answers the underlying question anyway: **under organic conditions, reactions and
stickers are both at exactly 0/15 for `gemini-3.7-flash`, with no measurable gap between
them.** Phase 1's finding explains both simultaneously — the model's visible reasoning never
surfaces *any* non-text channel as an option, gesture or sticker alike.

This is worth flagging as a refinement, not a contradiction, of the task brief's framing.
The brief's evidence for "reactions clear a threshold stickers don't" was a **diagnostic
control on a different model** (`claude-haiku-4.5`: 2/15 `set_reaction`, 0/15 stickers) — not
gemini-3.7-flash itself, which is what production runs. For the production model, no such
gap shows up organically.

The no-text ablation does still show a preference *within* the forced-gesture space:
`set_reaction` (13/15 including the one via `find_stickers`) heavily dominates
`send_sticker` (1/15) — consistent with `send_sticker`'s two-turn structural cost
(`find_stickers` → `send_sticker`) versus `set_reaction`'s one-turn cost. So the *relative*
ordering (reaction easier to reach than sticker) does hold once the model is forced to
gesture at all; it just isn't the thing keeping either channel closed under normal operation.
**Candidate prefetch stays evidence-backed rather than asserted** (the sticker-specific
structural cost is real), but it would not, on this evidence, unlock reactions — the two
channels share the same root cause (never considered) rather than the sticker cost being the
sole barrier.

## Phase 3 — no production change

Neither structural probe names an actionable fix:

- **2a (tool order): null.** Reordering did nothing; not the lever.
- **2b (no-text ablation): capability gap.** Forcing the channel open would degrade replies
  (indiscriminate 🤡), so the honest conclusion is not to force it.

Per the task's own "neither" branch: stop, don't stack a third wording variant. This is a
clean "diagnostics ran, here's what the model said, here's what we still don't know"
outcome. `set_reaction`'s missing `ToolFailure` path
(`src/characters/tools/answer.py:32-34`, raises `ValueError` instead of returning
`ToolFailure` like its two siblings) remains an open, low-priority latent bug — harmless
while the channel is dead, worth fixing the day any future round does ship a change here.
Not fixed in this diagnostic-only round per the task's own conditioning ("if Phase 3 ships a
production change, fix it in the same PR").

## What would change this conclusion

The model was never observed *weighing* a gesture and rejecting it — it simply doesn't
generate that branch of reasoning under `character_setup/v9`. Two rounds of wording changes
(prompt template, tool descriptions) failed to introduce that branch. This round adds two
more negative results (tool order; forced-capability quality) and a positive one (the model
can be made to call `set_reaction`, but not well). Whatever would actually change this is
not visible from this round's evidence — it isn't a wording problem, an ordering problem, or
a capability-training gap in the narrow sense (it "knows" what 🤡 means; it just doesn't map
situations onto specific emoji). It reads more like the model's implicit response format for
this task is simply "compose a line of chat text," full stop, and gestures are an
afterthought it reaches for only when explicitly cornered — and even then, badly.

## Out of scope, confirmed

Candidate prefetch (structural sticker cost confirmed real, but not the shared-barrier fix —
see Phase 4), `sticker_describe` vision prompt / re-indexing, a production model swap
(haiku's 2/15 is five fixtures on one model and would re-open every rubric PR #49
validated), `STICKER_RECENT_EXCLUDE` (still 0, still unmeasured), the initiative pipeline
(fires but never scores — 6 `INITIATIVE_WINDOW` → 6 `INITIATIVE_PRECHECK_FAILED` → 6
`INITIATIVE_SKIPPED` — flagged here, not fixed).

## Run data

`/tmp/probe-smoke.json` (Run 0, 3 rows) and `/tmp/probe-round3.json` (Run 1, 45 rows,
`--repeat 3`, 3 providers × 5 fixtures) are local scratch output, not committed. Total: 45
provider loops, 113,170 tokens, 0 `gpt-5-mini` judge calls (rubric-free probe config — three
of the five production rubrics are written to hand any gesture a fixed score and cannot
discriminate this question; see `evals/reply/characters/promptfooconfig.probe.yaml`,
deleted after this round). No graded run (`Run 2`) was needed: neither organic arm moved,
so there was nothing to grade against the production rubrics.
