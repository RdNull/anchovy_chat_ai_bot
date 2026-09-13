---
name: update-docs
description: >
  Use this skill when the user asks to update, refresh, or sync the project
  docs — `CLAUDE.md`, `README.md`, or both — after a subsystem changed.
  Covers how to scope the change, which sections of each file a single
  subsystem change touches, the audience split between the two documents,
  and how to verify every claim before writing it.
disable-model-invocation: false
---

## Current state
- Recent commits: !`git log --oneline -15`
- Docs last touched: !`git log -1 --format='%h %ad %s' --date=short -- CLAUDE.md README.md`

---

## The two documents have different readers

Never write the same paragraph into both. One change usually needs two different
sentences.

| | `CLAUDE.md` | `README.md` |
|---|---|---|
| Reader | Claude Code, mid-task | a human evaluating the project |
| Goal | navigate and change the code correctly | understand what was built and why it is interesting |
| Names | `module.py:function`, settings constants, log prefixes, collection names | subsystem names in prose; no `snake_case` symbol soup |
| Detail | exact order of operations, exact flags, exact file paths | the engineering decision and the reason behind it |
| Tone | flat and factual | showcase, but never inflated — no adjectives the code cannot back |
| Cut | anything the reader can grep for in seconds | anything that is an implementation detail rather than a design idea |

A rule of thumb that held for the memory rework: `CLAUDE.md` gets *"`apply_decay`
computes two survivor sets and applies one, baseline while `ENABLE_MEMORY_DECAY`
is off"*, `README.md` gets *"the decay policy ships in log-only mode, recording
the gap against the live baseline before being switched on"*.

### Root `CLAUDE.md` is a thin index, not the whole reference

The root file was split (2026-09) because its size was degrading adherence — a
long resident file is a file the model follows less reliably, not just a
warning to silence. It now holds only: commands, the message flow, one line per
subsystem with a `Full notes:` pointer, and cross-cutting invariants (settings
access, the window-size table, the `test_data` guard). Everything else moved to
files that load lazily, only when Claude touches the code they describe:

| detail lives in | loads when Claude reads |
|---|---|
| `src/memory/CLAUDE.md` | a file under `src/memory/` |
| `src/initiative/CLAUDE.md` | a file under `src/initiative/` |
| `src/blackbox/CLAUDE.md` | a file under `src/blackbox/` |
| `src/embeddings/CLAUDE.md` | a file under `src/embeddings/` (RAG + sticker replies) |
| `src/tests/CLAUDE.md` | a file under `src/tests/` |
| `.claude/rules/deployment.md` | `manifests/**`, `.github/workflows/**`, or `deploy-k8s.sh` |
| `.claude/rules/models.md` | `src/models/**`, `src/ai.py`, `src/model_manager.py`, or `evals/**` |

**A subsystem change goes to that subsystem's own file, not to root.** Only
these touch root: a change to the message flow itself, a new/changed command, a
new settings constant (Configuration access), a Code Style rule, or an
invariant that belongs in the always-resident set (destructive-action guards,
the window-size table). If you are tempted to grow a bullet in root past ~5
sentences, that is the signal the detail belongs in the module file instead —
shrink the root bullet to a one-line pointer and put the growth there.

The nested `CLAUDE.md` files and `.claude/rules/*.md` files are plain markdown
with the same voice as root (the rules files also carry a `paths:` frontmatter
block — copy it, do not touch it unless the set of relevant files changed).
There is no size pressure on them the way there is on root: `/context` warns
only on the root-level file's char count, and path-scoped rules do not count
toward that warning at all.

---

## Steps

### 1. Scope the change

Ask for the subsystem if the user named one; otherwise derive it:

```bash
git log --oneline <last-docs-commit>..HEAD
git diff --stat <last-docs-commit>..HEAD
```

Commit messages tell you *where* to look. They are not a source for doc text —
they describe intent at the time of writing, and several commits in a row often
walk back what the earlier ones claimed.

### 2. Read the source, not the log

Read every file in the changed package, in full. In this project the design
rationale lives in module and function docstrings — `src/memory/decay.py` and
`src/memory/keys.py` are the model — and that rationale is exactly what the docs
should compress. A docstring explaining *why cycles and not wall clock* is worth
a doc sentence; the function signature is not.

Also read, for the subsystem:
- `src/settings.py` — the real constant names and defaults
- `src/models/<local|cloud>/<task>/<version>.json` — the real model behind a
  version label. Never repeat a model name from the existing docs; it goes stale
  silently. Open the JSON the code actually loads.
- `src/prompts/<task>/` — which prompt version the processor asks for
- `src/tests/` and `evals/` — new test dirs and eval suites are doc-visible
- `src/bot.py:setup_scheduler` — if a background job was added

### 3. Audit the existing sections for staleness

The failure mode is not a missing section, it is a section that is confidently
wrong. Before adding anything, re-read what is already there about the subsystem
and check each claim against the code you just read. The `README.md` memory
bullet had listed "facts, decisions, open loops, constraints, preferences" long
after the model shrank to `traits`/`recent` plus chat state.

### 4. Find every touched section

One subsystem change lands in more places than expected. Grep first — across
root, every module file, and the rules directory, since the detail you need to
update is probably not in root any more:

```bash
grep -rn -i '<subsystem>' CLAUDE.md README.md src/*/CLAUDE.md .claude/rules/
```

`CLAUDE.md` (root) — check each of:
- the numbered **Core flow** list (steps 1–4), including the scheduler line if a job changed
- the **"Where the detail lives"** table under **Architecture** — add a row if the change introduces a new module-level `CLAUDE.md` or rules file; otherwise leave it
- the subsystem's own bullet under **Key subsystems** — keep it short and point at its detail file rather than growing it
- **Configuration access** — new settings constants belong in that sentence
- the destructive-action guard under **Testing notes** — only if the guard itself changed, not for new test files
- the **Commands** block — new or split eval suites, scripts, backfills

The detail file for the subsystem — check the table above for which one:
- update the deep, ordered walkthrough there (the numbered stages, the exact
  flags, the observability lines) — this is where nearly all of a subsystem
  change's doc burden lands
- if the subsystem has no detail file yet (a genuinely new module), decide
  whether it needs one now or is still small enough for a root bullet; a
  bullet past ~5 sentences is the signal it needs its own file — see "Root
  `CLAUDE.md` is a thin index" above for the two file shapes (nested
  `CLAUDE.md` for a `src/` subtree, `.claude/rules/*.md` with `paths:`
  frontmatter for anything spanning non-`src/` locations like manifests or
  workflows)

`README.md` — check each of:
- the feature bullet under **Key Features**
- the **Tech Stack** table row (the *Role* column goes stale first)
- the ASCII **Architecture** diagram — usually two spots: the inline pipeline and
  the `[scheduler]` blocks at the bottom
- **Testing** and the prompt-evaluation bullet

### 5. Write

Prefer restructuring over appending. When a flow gains stages, a numbered list of
stages beats five more sentences in an existing paragraph; when a `README.md`
feature bullet grows past a paragraph, break the mechanism into sub-bullets and
keep the lead paragraph as the summary.

Keep the existing voice — em dashes, lowercase symbol references in backticks,
no headings deeper than `###` in `CLAUDE.md`.

Write facts, not evaluations. "Decay ships off; phase 1 logs what the policy
would evict" is a fact. "A robust, well-designed decay system" is not, and it is
the first thing to delete on the next read.

### 6. Verify before claiming

Every symbol, path, setting, and default in the new text must exist:

```bash
grep -n 'SETTING_NAME' src/settings.py
grep -rn 'function_name' src --include='*.py'
ls <every path you referenced>
```

Cross-file mirrors deserve an explicit note in `CLAUDE.md` — e.g.
`src/memory/keys.py:normalize` and `evals/memory/memory_lib.js:norm` must change
together, and a doc line is the only thing that makes that discoverable.

---

## Editing mechanics

Use a Python heredoc with an `assert` on every anchor rather than `sed`. A `sed`
expression that matches nothing exits 0 and silently changes nothing, and these
files are long enough that the no-op is easy to miss:

```bash
python3 - <<'PY'
import pathlib

path = pathlib.Path('CLAUDE.md')
text = path.read_text()

old = """<exact current text>"""
new = """<replacement>"""
assert old in text
text = text.replace(old, new)

path.write_text(text)
print('ok')
PY
```

Batch all replacements for one file into a single script, each with its own
`assert`. Re-read the file with `grep -n` afterwards to confirm placement.

---

## Do not

- Do not rewrite sections unrelated to the change, however tempting.
- Do not delete accurate content to make room; these files are references, not
  summaries.
- Do not copy prose between `CLAUDE.md` and `README.md`.
- Do not document a planned or half-built state as if it shipped. If a flag is
  off by default, say so and say what the off state does.
- Do not add a "Recent changes" or changelog section — `git log` is that.
