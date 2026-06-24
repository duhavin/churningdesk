# CLAUDE.md - Churn Project Context for Claude/Codex

> Session-load context for agents working in `<repo-root>`.
> This gives Claude/Codex the same kind of persistent operating contract used in
> the ALPHACORE workspace.

## What This Is

Churn is a local, two-user credit-card churning decision engine for User A and
User B. It combines private household state with public card data to produce
clear, explainable next actions: apply, wait, refer, renew, downgrade, cancel,
use a specific card for a category, or mark data as needing review.

The product is not a generic card catalog. The deliverable is the household
decision system: accurate data in, optimal explainable actions out, with humans
approving data changes and real applications.

## Read These First

For broad tasks, onboarding, UI work, scoring work, ingestion work, or ambiguous
requests, read in this order:

1. `AGENTS.md` - hard rules, workspace boundary, architecture map.
2. `docs/PROJECT_PRIMER.md` - one-doc project context.
3. `docs/PROJECT_EVOLUTION.md` - living audit/change/revert ledger.
4. `docs/NEXT_TASKS.md` - current priorities and deferred work.
5. Relevant docs in `docs/context/`.
6. `docs/SYSTEM.md`, `docs/USAGE.md`, and `docs/UX.md` as needed.

## Non-Negotiable Principles

1. Value-first decisions. Large real household value beats small offers at 100%
   of a tiny peak.
2. Peak score is timing, not the primary ranker.
3. Eligibility gates every recommendation.
4. Every recommendation must state its binding reason.
5. PUBLIC/PRIVATE firewall is mandatory. Ingestion touches public data only.
6. PRIVATE data stays local/encrypted and never goes to LLM/web flows.
7. Unknown or unsupported data becomes `NEEDS DATA`; never fabricate.
8. Public offers, targeted offers, public peaks, and targeted/incognito highs
   stay separate.
9. Large extracted changes and eligibility-rule changes go through review.
10. Web search is a cached last resort, not routine refresh.
11. Additive DB migrations go through `backend/db.py::_ADDED_COLUMNS`.
12. Meaningful changes update `docs/PROJECT_EVOLUTION.md`.

## Architecture Map

- `backend/ingestion/` - PUBLIC-only collection, extraction, validation, refresh.
- `backend/logic/` - eligibility, scoring, catalog, pipeline, household,
  benefits, redemption logic.
- `backend/routers/` - API surfaces.
- `backend/models.py` - PUBLIC/PRIVATE SQLAlchemy entities.
- `backend/db.py` - engine, session, additive migrations, startup seeding.
- `frontend/src/pages/` - Dashboard, Profiles, Card Plan, Pipeline, Household,
  Redemption, Card Universe.
- `frontend/src/components/` - shared UI primitives and navigation.

## How To Work

Before coding:

- Restate the relevant guideline(s) from this file and
  `docs/context/DEFINITION_OF_DONE.md`.
- Identify the owning module/page.
- Name the data contract: where the value comes from, whether it is
  PUBLIC/PRIVATE, and whether it is truth, state, or derived.
- Keep the change tightly scoped.

For broad prompts like "make this professional grade" or "fix the app":

- Do not rewrite the app.
- Identify the page/workflow and the data that should be shown.
- Implement one reviewable slice.
- Verify with targeted backend tests and frontend typecheck/build when relevant.
- Add a `PROJECT_EVOLUTION.md` entry with verification and rollback notes.

## What Not To Do

- Do not edit `<separate-churn-control-repo>` unless explicitly asked.
- Do not send private household data to LLMs or web APIs.
- Do not use model memory as source evidence for offers/rules.
- Do not promote redemption/live award-search work ahead of the core
  apply/benefit/pipeline loop unless approved.
- Do not replace working code with a broad redesign when a focused change will
  fix the workflow.

## Verification Expectations

- Backend logic: relevant `backend/tests/*` tests and `python -m py_compile`.
- Frontend: `npm run typecheck`; build or visual smoke for meaningful UI work.
- Ingestion/data changes: provenance, review behavior, no PRIVATE imports, no
  fabricated values.
- Docs/context changes: update `docs/PROJECT_EVOLUTION.md`.
