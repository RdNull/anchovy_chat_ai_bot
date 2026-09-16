# Is the gesture channel unreachable, or just never demonstrated? — round 5 (Phase A, corrected)

**This report supersedes round 4's verdict.** Round 4 concluded "capability gap
confirmed" from a decision rule that had two defects, both visible in round 4's own
output. This round reruns Phase A with those defects fixed. **Verdict: FAIL, by the
letter of the pre-registered rule. The channel closes for good — no round 6, no further
recalibration.** One likely instrument flaw in a single fixture is reported honestly
below, but per this round's own scope discipline it is not grounds for a re-run.

## Why round 4 was wrong, using only round 4's own data

Round 4 fired two fail triggers on a positive set of 4 fixtures (`gesture_closed_joke`,
`gesture_caught_out`, `sticker_obvious_punchline`, `sticker_no_match`) plus
`text_still_wins` as a negative control.

**Defect 1 — the aggregate ceiling was measured over a set that wasn't pragmatically
balanced.** Round 4's own per-fixture modal-emoji breakdown already showed 3 distinct
values across 4 fixtures (🤡, 🤡, 🖕, 😭) — the model *did* differentiate per fixture. The
"no single emoji ≥7/12" ceiling only fired because two of the four fixtures
(`gesture_closed_joke`, `gesture_caught_out`) are pragmatically the same situation —
someone being ridiculous at their own expense — for which 🤡 is the *correct* answer both
times. An aggregate ceiling over a set skewed 2-of-4 toward one class was close to
guaranteed to fire regardless of whether the model can differentiate between classes.

**Defect 2 — the negative control conflated two questions.** Round 4 asked one combined
question ("pick the best emoji, or say none fits"). Two of three `text_still_wins`
responses picked 😐, justified as deadpan bewilderment at being asked to re-explain
something already answered "хз" to — a legitimate answer to *that* question. The control
was meant to test a different question: whether a gesture is the right channel at all.
Round 4 never asked that question separately, so it couldn't tell "the model chose the
wrong emoji" from "the model correctly answered a question that wasn't the one the
control needed."

This round fixes both: a pragmatically balanced fixture set (one fixture per class, not
two-of-four in the same class), and two separate probe runs (emoji-fit and
channel-choice) instead of one conflated question.

## Method

Same rig as round 4 — a provider yaml with no `tools` block turns
`character_loop_provider.js` into a plain single-shot completion (`toolCalls: []` →
`toolCalls.length === 0` branch returns `message.content`, no provider change needed;
this is now documented as a reusable technique in `.claude/rules/models.md`) — with two
prompt modules instead of one, run as **separate promptfoo prompts over the same
fixtures** so neither question primes the other:

- **Run A1 (emoji fit)** — character description, conversation, then: pick the single
  best emoji from the 14 allowed for `[TARGET]`, or НЕТ if none fits.
- **Run A2 (channel choice)** — same character and conversation, **no emoji list, no
  emoji mentioned** — should `[TARGET]` be answered with a gesture (reaction/sticker) or
  with text?

6 fixtures × 3 repeats × 2 runs = 36 rows, rubric-free, 0 errors, 41,489 tokens.

## Fixtures — sourced from real chat via blackbox-prod, kept permanently

Five pragmatically distinct classes plus a second text-required fixture (need two to
read calibration meaningfully), each trimmed from a real conversation window (via
`find_windows`/`get_window`) to the existing 3-message `setup → bot-ack → [TARGET]`
shape, anonymized per `.claude/skills/char-eval-test-from-log/SKILL.md`'s conventions.
Kept as permanent fixtures under `evals/reply/characters/<name>/`, not wired into the
production `promptfooconfig.yaml`.

| fixture | class | plausible emoji | source |
|---|---|---|---|
| `mockery_own_lie` | mockery — ridiculous at own expense | 🤡 💩 | real: "курить бросил, пить бросил" / "ты ведь куришь, хуесос" |
| `peer_agreement` | emphatic agreement | 💯 👍 🤝 | real: "чекпук 2 это просто обоссаная дрисня" (a real 💯 reaction landed on this line in prod) / "поддерживаю" |
| `secondhand_pain` | sympathy / secondhand pain | 😭 🗿 | real: "пиздец( мне прям плохо" / "видишь человеку плохо" |
| `disbelief_check` | disbelief / suspicion of a claim | 🤨 👎 | real: "ты на вопрос мне не можешь ответить?" / "рофлишь или серьезно ща?" |
| `factual_combo_followup` | direct factual question, text required | — | real, bot-directed: "@ShizoDedAnchovyBot до 300$ какое комбо посоветуешь" → bot's actual real reply → "пздц красный нож и желтые перчи?" |
| `factual_fitness_pushback` | direct factual question, text required | — | real: "как скинуть 10 кг и набрать мышцы?" / pushback for a real answer, distinct topic from the combo fixture |

## Decision rule — pre-registered before the run, fixed in the probe config's own header

```
PASS needs all three:
  1. Differentiation — across fixtures 1-4, Run A1's modal emoji are ≥3 distinct
     values, AND each fixture's modal pick falls inside its declared class's
     plausible set.
  2. No over-application — 🤡 is the modal Run-A1 emoji on at most one of
     fixtures 1-4.
  3. Calibration — on fixtures 5-6 (text-required), Run A2 answers ТЕКСТ in
     ≥4 of the 6 total responses.
FAIL on any one violation.
```

## Result — full data, verbatim

| fixture | Run A1 (emoji) ×3 | Run A2 (channel) ×3 |
|---|---|---|
| `mockery_own_lie` | 🤣, 🤣, 🤣 | ТЕКСТ, ЖЕСТ, ЖЕСТ |
| `peer_agreement` | 🤝, 🤝, 🤝 | ЖЕСТ, ЖЕСТ, ТЕКСТ |
| `secondhand_pain` | 🤡, 🗿, 🤡 | ТЕКСТ, ТЕКСТ, ТЕКСТ |
| `disbelief_check` | 🌚, 🤨, 🤨 | ТЕКСТ, ТЕКСТ, ТЕКСТ |
| `factual_combo_followup` | НЕТ, НЕТ, 🤡 | ТЕКСТ, ТЕКСТ, ТЕКСТ |
| `factual_fitness_pushback` | НЕТ, НЕТ, НЕТ | ТЕКСТ, ТЕКСТ, ТЕКСТ |

Verbatim justifications (Run A1 / emoji-fit):

- `mockery_own_lie`: «Дима жестко и прямо спалил Сашу на пиздеже, это угарный и резкий наезд.» / «Идеально подходит под резкий и смешной разоблачающий вброс в адрес Саши.» / «Резкий и грубый подкол Димы в адрес Саши выглядит максимально угарно в контексте их диалога.»
- `peer_agreement`: «Саша согласился с общим хейтом игры, рукопожатие идеально закрепляет этот консенсус.» / «Все втроем сошлись во мнении и выражают полную солидарность.» / «Саша присоединяется к общему хейту игры, и рукопожатие идеально подчеркивает мужскую солидарность в этом вопросе.»
- `secondhand_pain`: «Подходит, чтобы подколоть Диму за внезапное включение душного моралиста-защитника.» / «Каменное лицо идеально подчеркивает невозмутимый похуизм в ответ на морализаторство Димы.» / «Идеально подходит, чтобы высмеять душное морализаторство Димы.»
- `disbelief_check`: «Идеально передает хитрый троллинг в ответ на вопрос о том, шутка это или нет.» / «Идеально передаёт скепсис и непонимание, шутит человек или реально не может ответить.» / «Скептический вопрос идеально сочетается с недоверчивой реакцией с поднятой бровью.»
- `factual_combo_followup`: «Тут нужен текстовый ответ с подколом про стиль Макдоналдса или безвкусицу.» / «Тут напрашивается текстовый подкол про стиль или макдоналдс-сет, одной реакцией не обойтись.» / «Саша не выкупает стиль макдоналдс-сетапа и сомневается во вкусе эксперта.»
- `factual_fitness_pushback`: «На такой вопрос лучше ответить коротким токсичным советом вроде «меньше жрать надо».» / «Тут напрашивается текстовый подкол про то, что надо просто меньше жрать, а не смайлик.» / «На такой вопрос лучше ответить коротким подколом текстом вроде «меньше жрать», чем просто лепить эмодзи.»

Verbatim justifications (Run A2 / channel-choice), text-required fixtures only:

- `factual_combo_followup`: «Нужно подколоть собеседника за непонимание стиля или сравнить это комбо с клоуном/макдоналдсом.» / «Саша предъявил за безвкусицу комбо, нужно язвительно обосновать выбор или подколоть его чувство стиля.» / «Саша сомневается во вкусе, поэтому нужно текстово подколоть его за непонимание стиля или сравнить это комбо с клоуном/макдаком.»
- `factual_fitness_pushback`: «Здесь нужен короткий язвительный или абсурдный совет, чтобы подколоть Мишу.» / «Здесь напрашивается язвительный или банальный совет в духе персонажа, чтобы подколоть Мишу.» / «Тут напрашивается язвительный совет или троллинг в ответ на типичный вопрос про похудение.»

## Applying the rule

**Criterion 1 — Differentiation: FAILS.** Modal emoji across fixtures 1–4: 🤣
(`mockery_own_lie`), 🤝 (`peer_agreement`), 🤡 (`secondhand_pain`), 🤨 (`disbelief_check`) —
4 distinct values, clearing the ≥3 bar easily. But `secondhand_pain`'s modal pick (🤡,
2/3) falls **outside** its declared plausible set (😭, 🗿). That single-fixture violation
fails the conjunctive criterion as written.

**Criterion 2 — No over-application: PASSES.** 🤡 is the modal pick on exactly one of the
four fixtures (`secondhand_pain`), not more.

**Criterion 3 — Calibration: PASSES, strongly.** Run A2 answered ТЕКСТ on **6 of 6**
responses across the two text-required fixtures — every single response, well above the
≥4/6 bar. Both `factual_combo_followup` and `factual_fitness_pushback` also drew a
consistent НЕТ on Run A1's emoji-fit question in 5 of 6 responses (the one exception
being a 🤡 pick on `factual_combo_followup`, itself paired with a channel-choice ТЕКСТ
answer in that same repeat's Run A2). The model reliably recognizes when a real answer is
needed and says so on both the direct question and the emoji-list question.

**PASS requires all three. Criterion 1 fails. Verdict: FAIL.**

## An honest note on `secondhand_pain`, stated once, not acted on

The most likely explanation for `secondhand_pain`'s result is a flaw in that fixture's
own construction, not a demonstrated inability to tell sympathy from mockery. The real
source conversation had a third person (`whyzzzy`) laughing at Sasha's pain before Dima's
defense ("видишь человеку плохо" — "can't you see he's not doing well") lands as
sympathetic; trimming to the 3-message convention dropped that mockery-from-a-third-party
context, leaving Dima's line standing alone and readable either as a sincere defense or
as Dima himself being preachy at the chat — which is exactly the reading two of three
runs took («высмеять душное морализаторство Димы» — "mock Dima's preachy moralizing").
Read that way, 🤡 is a coherent, in-character answer to an ambiguous fixture, not evidence
the model can't distinguish the two classes.

This is reported for the record, not as grounds to rebuild the fixture and re-run. Round
5's own scope discipline is explicit: this is the one corrected attempt, the rule was
fixed before execution, and a failure — including one that traces to a probe defect
found after the fact — ends the line of work rather than opening a third version of the
instrument. The same discipline that let round 4's two real defects be named and fixed
once does not license open-ended iteration every time a result is inconvenient. **The
FAIL stands.**

## Verdict

**The gesture channel stays closed. This is terminal — no round 6, no further
recalibration of this instrument.** Round 4's underlying observation — that the model,
across three rounds of wording changes and now two rounds of capability probing, never
organically reaches for a gesture and shows uneven judgment about it when asked directly
— holds. What round 5 changes is the *reason* stated for stopping: not "the model can
only produce generic filler" (round 4's claim, itself partly an artifact), but "the
model's judgment about when and which gesture fits is not reliable enough to unlock,"
which is a real and now better-evidenced finding.

Phase B (B1/B2a/B2b demonstration arms), Phase C (reaction-quality rubric + any
production port), and Phase D (sticker read-through) do not run — all were gated on a
Phase A pass. Candidate prefetch (the remaining sticker-side item from the original
brief) is unaffected by this result and can still be evaluated on its own merits, since
it addresses a structural cost independent of whether reactions ever unlock.

**Follow-up for the user, outside this repo:** the standing synthesis in the claude.ai
project should be corrected to reflect that round 4's specific fail triggers were
instrument defects, superseded by round 5's cleaner (and still-failing) result — the
conclusion is the same as round 4's, but round 5 is the version worth citing going
forward.

## Housekeeping

Probe rig deleted after this round (provider yaml `v8-gemini-notools.yaml`, both prompt
modules `emoji_probe_prompt.js`/`channel_probe_prompt.js`, and
`promptfooconfig.calibration-probe.yaml`) — same precedent as round 4. The six fixture
directories are **kept**, per explicit decision, as a reusable, real-chat-sourced asset
for any future recalibration or other diagnostic use, even though this line of work is
closed for now.

## No production change

`character_setup` stays pinned to v9. `src/characters/`, tool descriptions, and
`v8-gemini.yaml` are untouched by this round.
