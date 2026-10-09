# Facts pipeline detail

Loaded automatically when Claude reads a file under `src/facts/`. The root `CLAUDE.md` keeps the one-line summary.

## Record

`UserFact` (`models.py`): `nickname` (bare, no `@`), `kind` (`bio` / `habit` / `joke`), `status` (`candidate` / `confirmed`), `text`, `sightings` (distinct UTC days the fact was added or confirmed — stored in Mongo as ISO strings, since BSON has no date-only type), `created_at`, `last_seen_at`. There is no `confidence`: a fact is promoted by distinct-day sightings, and a confirmed fact is never touched by a scheduled job. Index `{nickname: 1, status: 1}` is created by `mongo.py:ensure_indexes` (the one place for index creation; idempotent) from `bot.py:post_init` and by the seed script.

## Extraction

`processors.py:extract_facts(new_messages, existing)` runs `facts/v3` (prompt `facts/v3.j2`) and returns `list[FactOp]` (`reason`, `op` add|confirm|replace, `target`, `nickname`, `kind`, `text`, `self_stated`; `reason` first on purpose). It never writes and raises on failure. `existing` is what the model sees: `number_facts` gives every stored fact a positional id (`f1`…, across the whole block) and `render_existing_facts` prints it grouped by `@nick` — status is not shown. The handler resolves `target` through the same `number_facts` map, so the numbering the model saw is the one checked.

`handlers.py:load_existing` builds `existing` for the window participants (`window_participants`: authors, `reply.nickname`, `@mentions`, minus `settings.BOT_NICKNAME` and every `BOT_NICKNAME[...]` form via `is_bot_nickname`): all confirmed facts plus up to `FACTS_PROMPT_CANDIDATES` candidates nearest, by vector, to that participant's own messages.

## Applying ops

`apply_op`, with `today` = UTC date of the run (`update_user_facts` reads it from the clock; freeze it with `freezegun` in tests):

- bot nickname → dropped (`bot_dropped`); `confirm`/`replace` with a missing target or another nickname's fact → dropped (`invalid_target`)
- `add` → first a vector search over the nickname's facts at 0.6; a hit becomes a `confirm` (`fallback=vector`), otherwise a new fact, `confirmed` only if `kind == bio` and `self_stated`
- `confirm` → add `today` to `sightings` (no-op for a day already there), stamp `last_seen_at`, promote when `len(sightings) >= FACT_CONFIRM_DAYS` (`JOKE_CONFIRM_DAYS` for `joke`), or at once for a self-stated `bio`
- `replace` → overwrite `text` and `kind`, `sightings=[today]`, status recomputed as for `add`, Qdrant point re-embedded

After all ops, `enforce_caps` trims each touched nickname to `FACTS_CONFIRMED_CAP` / `FACTS_CANDIDATE_CAP`, deleting the least recently seen. Every delete removes the Qdrant point too. Qdrant point ids are `uuid5(fact_id)` (`embeddings/facts.py:fact_point_id`), so any re-save — promotion, replace, backfill — overwrites the one point.

Observability: one `FACT_OP` per op (`op`, `kind`, `nickname`, `outcome` ∈ `created` / `confirmed` / `promoted` / `replaced` / `invalid_target` / `bot_dropped`, `reason`, `fallback`), `FACT_DELETED` for cap and expiry deletes, `FACT_EXTRACT` per run (`count` = ops).

## Ageing and seed

`tasks/facts.py:run_candidate_expiry` (daily, 03:00 Almaty) deletes candidates with `last_seen_at` older than `FACTS_CANDIDATE_TTL_DAYS`. There is no decay job.

`src/scripts/seed_facts.py` replaces the store with a JSON list of `{nickname, kind, text}` read from `--file` (`-` is stdin), all `confirmed`; `--dry-run` only validates and reports. The seed data is git-ignored (`docs/specs/` is, and `docs` is in `.dockerignore`) and must not enter the repo, the image or the workflow — it is piped into the running pod.

## Injection into the reply

The character has no facts tool (`get_user_facts` was called 0 times in 25 days; blackbox keeps its own). `handlers.py:select_fact_users` returns who the reply is about, most relevant first: with a target message its author, `target.reply.nickname`, then `@mentions` in its text; without one, the authors of the `USER` messages in the window, newest first. Bare nicknames, deduped, bot (bare and tagged) excluded. `facts_for_reply` walks that list, reads `confirmed` facts only (candidates never), skips people with none — a fact-less author does not spend a slot — and stops at `FACTS_INJECT_MAX_USERS` (default 3). Within a person: `bio`, `habit`, `joke`, then `last_seen_at` descending. `format_facts` renders one line per person (`@nick: a; b; шутка: c`) — jokes carry the `шутка: ` prefix so the model does not read them as real — and returns `None` when empty, which drops the block from `character_setup/v11.j2`. Call sites and the log fields are in the root `CLAUDE.md` (**Facts**).
