# Initiative: rolling-24h send cap + window/target logging — report

Branch `feat/initiative-daily-cap`. Full plan: task prompt + `/Users/anton/.claude/plans/task-initiative-mellow-whisper.md`.

## Why (recap)

Axiom over 17–20.09 showed the send rate was set by the cooldown, not the judge: once the cooldown
expired, every new batch of candidates got its own claim and its own shot at the judge, and scores
clustered tightly around the threshold (26 of 29 sends at exactly 0.70/0.75). This adds a deterministic
rolling-24h budget per chat, checked ahead of the judge, plus two log fields to separate a stale target
from a large window.

## Diff summary

| file | change |
|---|---|
| `src/settings.py` | `INITIATIVE_DAILY_LIMIT: int = Field(default=3, ge=1)`, module re-export |
| `manifests/configmap.yaml`, `.github/workflows/deploy.yml` | wire the new var through, same pattern as the other eight `INITIATIVE_*` |
| `src/initiative/models.py` | `InitiativeRun.replied_at: datetime \| None = None` |
| `src/initiative/repository.py` | `save_initiative_run` now returns the inserted id (`str`); new `mark_initiative_replied(run_id)` and `count_replied_last_24h(chat_id)` |
| `src/initiative/policies.py` | `pre_check` gets a fifth, last gate: `daily_limit` |
| `src/initiative/handlers.py` | `_claim_window` returns `(context, candidates, run_id)`; `run_initiative_checks` stamps `replied_at` right before `create_task` |
| `src/initiative/processors.py` | `INITIATIVE_EVALUATE` gains `target_distance` / `target_age_s`, omitted without a target |
| `src/initiative/CLAUDE.md`, root `CLAUDE.md` | updated for the new gate, the new run field, the new log fields |
| tests | `src/tests/initiative/*`, `src/tests/test_settings.py` |

Full suite: `docker compose exec bot pytest` → **628 passed**. Targeted run
(`src/tests/initiative src/tests/test_settings.py src/tests/test_deploy_config.py`) → **90 passed**.

## How the claimed run id reaches the send path

`_claim_window` already builds and saves the run document inside `INITIATIVE_RUN_LOCK`; it now also
returns the id `save_initiative_run` hands back from `insert_one`. `run_initiative_checks` carries that id
as a local (`context, candidates, run_id = await _claim_window(chat_id)`) through the evaluation and
decision steps and calls `await mark_initiative_replied(run_id)` immediately before
`asyncio.create_task(_run_initiative_reply(...))` — not inside `_run_initiative_reply` itself.

Two reasons for that placement, both from the task's own framing ("stamping at decision time... reserves
the slot"):
- `INITIATIVE_REPLY_SENT` is logged as the first line *inside* `_run_initiative_reply`, i.e. after the
  detached task already exists — stamping there would be after the slot is already spent, not at the
  point the decision is made.
- `_run_initiative_reply` is spawned as a detached task with only `chat_id`, `character`, `evaluation` in
  its signature. Threading `run_id` into it just to write a field that has nothing to do with the reply
  itself would widen that signature for a concern the caller already owns.

The dry-run branch (`INITIATIVE_ENABLED=False`) and the below-threshold branch both `return` earlier in
`run_initiative_checks`, before the stamp line — so "don't stamp on those paths" falls out of control flow
rather than being a condition on the stamp itself.

## Index decision

No index was added. `create_index` appears nowhere in this project — every Mongo collection is used
unindexed, and `get_last_initiative_run` already performs an unindexed `{'chat_id': ...}` + sort scan of
`initiative_runs` on every single message today. The collection holds on the order of 10² documents for one
chat (a handful of claims a day). A `(chat_id, replied_at)` compound index would be the right fix if the
collection grows enough to matter, but adding one now would mean introducing the project's first
index-creation path (a startup hook, and a decision about whether tests create it too, since
`clean_collections` drops the collection after every test) to speed up a scan that already costs less than
the scan sitting next to it. Revisit if `initiative_runs` grows past what a chat produces in a few months.

## Real log lines

Produced via a throwaway script run as `docker compose exec -e DATABASE_NAME=test_data bot python ...`
against the test database, through the project's real `JsonFormatter` (`src/logs.py`) — not hand-written.

**`INITIATIVE_SKIPPED reason=daily_limit`** — three prior runs stamped `replied_at` within the last 24h,
`INITIATIVE_DAILY_LIMIT=3`:

```json
{"level": "INFO", "logger": "bot", "module": "policies", "line": 72, "msg": "Skipping initiative reply", "event": "INITIATIVE_SKIPPED", "reason": "daily_limit", "count": 3, "limit": 3}
```

**`INITIATIVE_EVALUATE`** with the new fields — 3 candidates, target resolved to the middle one
(`target_index=2`), 3 minutes older than the newest candidate:

```json
{"level": "INFO", "logger": "bot", "module": "processors", "line": 102, "msg": "Initiative evaluation result", "event": "INITIATIVE_EVALUATE", "outcome": "ok", "score": 0.75, "reason": "зацепка висит", "target_index": 2, "elapsed_ms": 0, "target_distance": 1, "target_age_s": 180}
```

`target_distance=1` (one candidate newer than the target, out of 3) and `target_age_s=180` (3 minutes)
match the hand-built fixture exactly.

## Not part of this PR

The user sets two GitHub repo variables before deploy:

```bash
gh variable set INITIATIVE_COOLDOWN_MINUTES --body 180
gh variable set INITIATIVE_DAILY_LIMIT --body 3
```

`manifests/configmap.yaml` has no literal values — every `INITIATIVE_*` entry is an `envsubst` placeholder
fed from a repo variable at deploy time, so the 45→180 cooldown change and the new cap's actual production
value both live outside this repo's diff.

After deploy: run 7 days at cap 3 / cooldown 180, then apply the decision rule from the task (mechanical:
no chat-local day above 3 sends; keep: ≤1 pushback and ≥1 positive reaction in 7 days; escalate to the
judge if ≥2 pushbacks at ≤3 sends/day).
