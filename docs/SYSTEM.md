# WEwards System Architecture

This document maps the application so agents can make changes without
rediscovering the repo or crossing data boundaries.

## Runtime Shape

- Backend: FastAPI, SQLAlchemy 2, SQLite.
- Frontend: React, TypeScript, Vite, Tailwind.
- LLM: Anthropic, used only by PUBLIC ingestion/extraction flows.
- Storage: local SQLite at `DATABASE_URL`, default `sqlite:///data/wewards.db`.
- Sensitive fields: Fernet encryption via `backend/crypto.py`.

## Backend Entry

`backend/main.py`

- Initializes DB via `init_db()`.
- Registers routers.
- Serves built frontend when `frontend/dist` exists.
- Exposes `/api/health` and `/api/users`.

## Database Layer

`backend/db.py`

- `engine`, `SessionLocal`, `get_db()`.
- `Base` declarative model root.
- `_ADDED_COLUMNS` is the canonical additive migration registry.
- `_run_additive_migrations()` applies missing columns and unique indexes.
- `init_db()` creates tables, runs migrations, and seeds card references.

Do not create a second migration mechanism without explicit approval.

## Model Ownership

PUBLIC:

- `CardProduct`, `CardReference`, `CardWatchlist`, `CardBlacklist`,
  `ProposedChange`, `IngestionEvidence`, `Valuation`, `SourceConfig`,
  `TransferPartner`, `AwardBenchmark`.

PRIVATE:

- `HeldCard`, `UserProfile`, `BenefitUsage`, `ManualTargetedOffer`,
  `TargetRedemption`.

Ingestion modules may use PUBLIC models only.

## Ingestion Pipeline

- `fetch.py` - cached concurrent HTTP for public pages.
- `static_parse.py` - deterministic parsing and compact snippets.
- `extract.py` - only LLM extraction module; PUBLIC snippets only.
- `validate.py` - delta gates values, writes evidence/proposals.
- `research_resolver.py` - focused cached research for stale/incomplete cards.
- `schedule.py` - PUBLIC-only `run_refresh` orchestration. Private held-card
  prioritization is computed before calling ingestion and passed as public
  product identity refs.
- `discover.py` - public card discovery.

## Decision Logic

- `eligibility.py` - issuer/application rules from held card dates/history.
- `scoring.py` - `peak_score`, `offer_value`, status, ranking support.
- `catalog.py` - scored catalog per user.
- `pipeline.py` - per-user application queue and binding reasons.
- `household.py` - combined household/referral optimization.
- `categories.py` - best card by category.
- `benefits.py` - public benefit definitions plus private usage.
- `catalog_health.py` - read-only catalog data-quality reasons and next actions.
- `logic/redemption/` - redemption foundation; later-phase.

## API Routers

- `cards.py` - held-card/private dashboard actions.
- `profiles.py` - profiles, balances, private state.
- `catalog.py` - public catalog, scoring, valuations, targeted offers,
  catalog-health queue.
- `pipeline.py` - application pipeline.
- `household.py` - household recommendations.
- `benefits.py` - benefit tracking.
- `categories.py` - category guidance.
- `ingestion.py` - proposed changes/source config/review queue.
- `run.py` - discovery/refresh actions.
- `watchlist.py`, `blacklist.py`, `references.py`, `redemption.py`.

## Frontend Map

- `App.tsx` - tab router, run actions, refresh banner, toast state.
- `lib/api.ts` - API client and shared frontend types.
- `components/TopNav.tsx` - tabs, user selector, run menu.
- `components/ui.tsx` - shared UI primitives.
- `pages/Dashboard.tsx` - household overview and action summary.
- `pages/Profiles.tsx` - per-user private state.
- `pages/CardPlan.tsx` - scored public catalog.
- `pages/Pipeline.tsx` - application queue.
- `pages/Household.tsx` - household/referral optimization.
- `pages/CardUniverse.tsx` - admin/catalog/review/source surface.
- `pages/Redemption.tsx` - redemption planning foundation.

## Verification Map

- Ingestion/research: `backend/tests/test_research_resolver.py`,
  `backend/tests/test_ingestion_guards.py`.
- Category/benefit/reference behavior:
  `test_categories.py`, `test_benefit_tracker.py`, `test_catalog_health.py`,
  `test_card_references.py`.
- Frontend changes: `npm run typecheck`; build or visual smoke for major UI work.
