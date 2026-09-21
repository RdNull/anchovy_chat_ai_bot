---
name: implement-spec
description: Implement a spec from docs/specs and write the implementation report. Use whenever the user asks to implement, build or work on a spec, or references a file in docs/specs (e.g. "implement docs/specs/012-foo.md", "do spec 12").
---

# implement-spec

Specs in `docs/specs/NNN-slug.md` come from a separate design session and are reviewed by the user.
The report you write goes back to that design session, so it must be accurate, not optimistic.

## 1. Read and check
- Read the whole spec.
- If `status` is `draft`, stop and ask whether to proceed.
- Read relevant code before planning.

## 2. Plan
- Plan against the acceptance criteria. Map each AC to where/how it gets implemented and verified.
- Surface in the plan, as explicit questions: ambiguities, conflicts between spec and code, open questions
  from the spec that block work. Don't decide these silently.
- Respect "Non-goals" and "Constraints".

## 3. Implement
- Set spec `status: in-progress`. Otherwise don't edit the spec.
- Main flow first, then edge cases, then tests. If a test-writing skill exists in this repo, follow it for tests.
- Any deviation from the spec is allowed only if it's recorded in the report with the reason.

## 4. Verify
- Check each acceptance criterion: run the tests or commands that prove it.
- Run the "Prod verification — tech" checks only if the user asks.

## 5. Report
- Write `docs/specs/NNN-slug.report.md` following `assets/report-template.md`.
- Every AC gets a status and concrete evidence (test name, file, command). "partial" and "no" are fine if true.
- Leave `status` at `in-progress`. Marking it `done` is for the design-side review.
- Tell the user the report path and a 2–3 line summary.
