# PROCESS — the end-to-end loop

The app automates a repeating loop. Each stage names the code that owns it and where the
human stays in.

## 1. Collect (PUBLIC web → cache)
- `backend/ingestion/fetch.py` — cached, concurrent HTTP (`PageCache`: ETag/Last-Modified/
  content-hash + TTL under `data/http_cache`; shared httpx client/keep-alive).
- Sources come from `SourceConfig` (DB-managed), falling back to `config.DEFAULT_SOURCES`.
- **PUBLIC only.** No login/CAPTCHA/private pages. Never touches PRIVATE data.

## 2. Ingest / extract (text → structured fields)
- `backend/ingestion/static_parse.py` — HTML → compact snippets, deterministic offer rows.
- `backend/ingestion/extract.py` — the **only** LLM caller. Structured extraction on
  snippets (cheap), and a **cited web-search** pass (expensive last resort).
- `backend/ingestion/schedule.py::run_refresh` orchestrates:
  static parse → deterministic short-circuit → batched LLM → **cited web-search fallback**
  (gated; only for stale + hard-to-find cards; see `DATA_RELIABILITY.md`).

## 3. Verify / gate (don't trust blindly)
- `backend/ingestion/validate.py` — delta-gating: first sight commits with provenance;
  large offer changes (> threshold) and any eligibility-rule change go to a **review queue**
  (`ProposedChange`). Targeted/incognito highs are split from public peak; peak is monotonic
  (raise freely, review decreases); valuation pages can't write offer fields; every field
  logs `IngestionEvidence` (source_url, snippet, fetched_at, hash, confidence).
- **Human-in-the-loop:** the review queue (Card Universe tab) is where a person approves/
  rejects proposed changes. Unknown/unsupported → `NEEDS DATA`, never a fabricated value.

## 4. Decide (data → conclusions)
- `backend/logic/eligibility.py` — timing rules from `date_opened` (5/24, Amex lifetime/
  velocity/5-card, Sapphire 48mo, Chase Ink 90, Citi spacing, closed-to-new).
- `backend/logic/scoring.py` — `peak_score` (timing) + `offer_value` (dollars) + status.
- `backend/logic/catalog.py` — per-user scored catalog (held-aware, hides verified
  no-bonus cards).
- `backend/logic/pipeline.py` — per-user apply queue (excludes held + family dupes).
- `backend/logic/household.py` — both users merged: referrals both ways + ranked moves.

## 5. Recommend (conclusions → clear actions)
- Surfaced in the React tabs (Dashboard / Pipeline / Household / Card Plan). Every item
  carries a **reason** (apply now / wait / refer / renew / downgrade / use-here).

## 6. Act (human confirms)
- The human applies, refers, renews, or downgrades — then records it (e.g. "Record
  application" creates the held card), which feeds back into state.

## 7. Re-loop
- New held-card state recomputes eligibility/scoring/pipeline automatically. Periodic
  refresh keeps offers/peaks current; web search is the cached last resort, not routine.

**Human stays in the loop at:** review queue (data accuracy) and the apply decision.
Everything else is automated.
