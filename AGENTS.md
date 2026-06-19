# AGENTS.md — Churn (two-person churning decision engine)

> ## ⛔ READ BEFORE YOU CODE (hard rule)
> Before writing or editing **ANY** code, read this file and **all** of `docs/context/`.
> In your **first message** of any coding task, restate which guideline(s) apply and
> confirm the change aligns with the **Vision** and **Definition of Done**. If a request
> conflicts with these docs, **stop and flag it** instead of drifting.

## Workspace boundary

- Work **only** in `C:\Users\Davin\Desktop\Churn CLAUDE`.
- A separate app exists at `C:\Users\Davin\Desktop\Churn\churn_app` — **never** edit it or
  treat it as a target. (Read-only reference only, and only if explicitly asked.)

## What this is

A local, two-user (**Davin + Marilyn**) credit-card churning decision engine. We log the
cards we hold; the app scrapes to keep card data current; from our stack + timeline it
tells us the **best next moves** to maximize points/benefits as a household. The
deliverable is **the decision logic** — accurate data in, optimal explainable actions out.

## Context index (the guardrails — keep these current)

- [`docs/context/VISION.md`](docs/context/VISION.md) — north star; the finished-product feel + user outcomes.
- [`docs/context/PROCESS.md`](docs/context/PROCESS.md) — the collect → ingest → verify → decide → recommend → act loop + where the human stays in.
- [`docs/context/DECISION_RULES.md`](docs/context/DECISION_RULES.md) — how "good deal" / "best next move" are defined (**value-first**, not peak-% first). Spec for scoring + pipeline.
- [`docs/context/DATA_RELIABILITY.md`](docs/context/DATA_RELIABILITY.md) — data sourcing + trust rules; where peaks come from; provenance; NEEDS DATA.
- [`docs/context/DATA_MODEL.md`](docs/context/DATA_MODEL.md) — key entities + conventions (cpp, peak monotonicity, PUBLIC/PRIVATE firewall, additive migrations).
- [`docs/context/ROADMAP.md`](docs/context/ROADMAP.md) — what's built vs not; ordered next layers.
- [`docs/context/DEFINITION_OF_DONE.md`](docs/context/DEFINITION_OF_DONE.md) — the checklist every change must pass.

## Architecture map (grounding)

- `backend/ingestion/` — PUBLIC-only engine: `fetch` (cached concurrent HTTP) →
  `static_parse` → `extract` (only LLM caller; structured + cited web search) →
  `validate` (delta-gate, targeted-vs-public peak split, monotonic peak, non-offer guard,
  evidence) → `schedule` (`run_refresh` orchestrator) + `discover`.
- `backend/logic/` — `eligibility`, `scoring`, `catalog`, `pipeline`, `household`.
- `backend/models.py`, `backend/db.py` (additive migrations), `backend/routers/`.
- `frontend/src/pages/` — Dashboard, Household, Profiles, Card Plan, Pipeline, Card Universe.

If anything here drifts from the code, fix the doc in the same change.
