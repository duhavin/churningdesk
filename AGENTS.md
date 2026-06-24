# AGENTS.md - Churn (two-person churning decision engine)

> ## READ BEFORE YOU CODE
> Before writing or editing code, read this file, `CLAUDE.md`,
> `docs/PROJECT_PRIMER.md`, latest `docs/PROJECT_EVOLUTION.md`,
> `docs/NEXT_TASKS.md`, and the relevant files in `docs/context/`.
> In your first message for any coding task, restate which guideline(s) apply and
> confirm the change aligns with the Vision and Definition of Done. If a request
> conflicts with these docs, stop and flag it.

## Workspace Boundary

- Work only in `<repo-root>`.
- A separate app exists at `<separate-churn-control-repo>`. Never edit
  it or treat it as a target unless explicitly asked.

## What This Is

A local, two-user (User A + User B) credit-card churning decision engine. We log
the cards we hold; the app keeps public card data current; from our stack and
timeline it tells us the best next moves to maximize household points/benefits.

The deliverable is the decision logic and the user-facing action surface:
accurate data in, optimal explainable actions out.

## Context Index

- `CLAUDE.md` - session operating contract for Claude/Codex.
- `docs/PROJECT_PRIMER.md` - single-doc mental model.
- `docs/PROJECT_EVOLUTION.md` - living audit/change/revert ledger.
- `docs/AUDIT_PROMPT.md` - reusable full-codebase audit protocol.
- `docs/NEXT_TASKS.md` - current priorities and deferred work.
- `docs/SYSTEM.md` - backend/frontend/data architecture map.
- `docs/USAGE.md` - run commands and workflows.
- `docs/UX.md` - professional-grade page contracts.
- `docs/context/VISION.md` - north star.
- `docs/context/PROCESS.md` - collect -> ingest -> verify -> decide -> recommend -> act.
- `docs/context/DECISION_RULES.md` - value-first scoring/pipeline rules.
- `docs/context/DATA_RELIABILITY.md` - sourcing, provenance, review queue.
- `docs/context/DATA_MODEL.md` - entities, cpp convention, firewall, migrations.
- `docs/context/ROADMAP.md` - built vs not.
- `docs/context/DEFINITION_OF_DONE.md` - checklist every change must pass.

## Non-Negotiable Principles

1. Value-first decisions. Peak score is timing, not primary ranking.
2. Eligibility gates everything; ineligible cards are never APPLY NOW.
3. Every recommendation states its binding reason.
4. PUBLIC/PRIVATE firewall is mandatory.
5. Unknown/unsupported data becomes `NEEDS DATA`, never a fabricated value.
6. Public current offers, public peaks, targeted offers, and targeted highs stay separate.
7. Review queue protects large offer changes and eligibility-rule changes.
8. Web search is cached last resort, not routine refresh.
9. Additive DB columns belong in `backend/db.py::_ADDED_COLUMNS`.
10. Every meaningful change gets a `docs/PROJECT_EVOLUTION.md` entry.

## Architecture Map

- `backend/ingestion/` - PUBLIC-only engine: `fetch` -> `static_parse` ->
  `extract` -> `validate` -> `schedule` + `discover`.
- `backend/logic/` - `eligibility`, `scoring`, `catalog`, `pipeline`,
  `household`, `benefits`, `redemption`.
- `backend/models.py`, `backend/db.py` - models, encryption boundary, migrations.
- `backend/routers/` - API surfaces.
- `frontend/src/pages/` - Dashboard, Profiles, Card Plan, Pipeline, Household,
  Redemption, Card Universe.

If this drifts from code, fix the doc in the same change and log it in
`PROJECT_EVOLUTION.md`.

## Handling Broad Requests

For requests like "make it professional grade", "fix the dashboard", or
"improve the system":

1. Identify the exact page/workflow.
2. Identify the data contract and source of truth.
3. State what will not be changed.
4. Implement one scoped slice.
5. Verify with targeted tests/typecheck/build.
6. Update `docs/PROJECT_EVOLUTION.md`.

Do not bundle unrelated scoring, ingestion, and UI changes into one opaque pass.

## Definition Of Done

A change is not done until it satisfies `docs/context/DEFINITION_OF_DONE.md`,
plus:

- Relevant tests or compile/typecheck run.
- UI changes checked for loading/empty/error states.
- Public/private boundary preserved.
- Documentation updated if intent, rules, data model, or workflows changed.
- `docs/PROJECT_EVOLUTION.md` entry added with rollback notes.
