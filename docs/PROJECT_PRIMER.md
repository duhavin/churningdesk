# Churn Project Primer

> Read this when starting a new session, onboarding to the codebase, or answering
> "what is this project and how should I work on it?"

## 1. What Churn Is

Churn is a local FastAPI + React application for User A and User B's household
credit-card churning decisions. It combines private household state with public
card data to recommend the best next actions.

It answers:

- Which card should User A or User B apply for next?
- Should the route be direct or through the other person's referral?
- Which held cards need attention because of renewal, min-spend, benefits, or
  re-eligibility?
- Which card should be used for groceries, dining, travel, gas, and everyday spend?
- Which offers are strong enough to act on, and which should wait?
- Which catalog data is missing or untrusted and needs review?

The app is a decision engine with a human final check, not an autopilot.

## 2. Product Priority

1. Correctness and data provenance.
2. Household value and eligibility.
3. Clear user action.
4. Clean, professional, low-friction interface.
5. Automation where safe; human review where data or real-world action matters.

## 3. Non-Negotiables

- Value-first, not peak-percentage-first.
- Peak score is timing, not value rank.
- Eligibility gates every recommendation.
- Every recommendation states the binding reason.
- Unknown data becomes `NEEDS DATA`.
- PUBLIC and PRIVATE data stay separated.
- PRIVATE data stays encrypted at rest and never enters ingestion/LLM flows.
- Public offer values require provenance.
- Major extracted changes go through `ProposedChange` review.
- Web search is cached last resort, not routine.
- Additive schema changes go through `backend/db.py::_ADDED_COLUMNS`.

## 4. Public / Private Boundary

PRIVATE data includes held cards, last4s, credit limits, open dates, bonus
history, point balances, targeted offers, benefit usage, and redemption goals.
Sensitive fields use Fernet encryption decorators.

PUBLIC data includes card products, public offers, public peaks, referral
bonuses, annual fees, benefits, multipliers, eligibility tags, valuations, source
configs, ingestion evidence, and proposed public changes.

Ingestion must operate only on PUBLIC product/source data.

## 5. Data / Decision Flow

1. Collect public pages using cached HTTP in `backend/ingestion/fetch.py`.
2. Parse snippets in `backend/ingestion/static_parse.py`.
3. Use `backend/ingestion/extract.py` as the only LLM extraction path.
4. Validate and gate changes in `backend/ingestion/validate.py`.
5. Commit safe changes or queue risky changes in `ProposedChange`.
6. Compute eligibility in `backend/logic/eligibility.py`.
7. Score products in `backend/logic/scoring.py`.
8. Build catalogs/pipelines in `backend/logic/catalog.py` and `pipeline.py`.
9. Merge household/referral logic in `backend/logic/household.py`.
10. Surface the result through React pages.

## 6. Frontend Surfaces

- Dashboard: household overview and urgent actions.
- Profiles: per-user held cards, balances, benefits, 5/24.
- Card Plan: scored catalog, timing/value, status.
- Pipeline: ordered apply queue with binding reasons.
- Household: combined strategy and referral routing.
- Redemption: later-stage redemption planning.
- Card Universe: admin/review/data quality surface.

## 7. Current Built State

The repo contains a working FastAPI app, React/Vite frontend, eligibility engine,
value-first scoring, household pipeline logic, PUBLIC/PRIVATE SQLAlchemy models,
encryption, ingestion pipeline, review queue, card reference registry, benefits,
redemption foundation, and focused backend tests.

Verify current behavior against code and `docs/PROJECT_EVOLUTION.md` before
making claims.

## 8. Non-Goals For Now

- No automatic real-world applications.
- No private-data LLM analysis.
- No broad app rewrite for vague polish requests.
- No live award-search dependency as core functionality without approval.
- No fabricated offer or rule values.
- No separate migration system.
