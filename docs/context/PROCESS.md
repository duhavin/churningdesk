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

### Rendered fallback

Deep refresh may use `backend/ingestion/rendered_fetch.py` after static HTTP and compact
extraction fail on an already-known public URL. It uses Crawl4AI to render a small capped URL
set, then sends the rendered result back through the same static parser, evidence logging,
review queue, public/targeted separation, and peak monotonicity rules. It does not discover
private/login/CAPTCHA pages and is not the default refresh path.

## 3. Verify / gate (don't trust blindly)
- `backend/ingestion/validate.py` — delta-gating: first sight commits with provenance;
  high-confidence official product-page current offer terms can auto-adopt. Broad,
  conflicting, ambiguous, or unsupported large offer changes and any eligibility-rule change
  go to a **review queue** (`ProposedChange`). Targeted/incognito highs are split from
  public peak; peak is monotonic (raise freely, review decreases); valuation pages can't
  write offer fields; every field logs `IngestionEvidence` (source_url, snippet, fetched_at,
  hash, confidence).
- **Human-in-the-loop:** the review queue (Card Universe tab) remains the backstop for
  ambiguous or unsupported changes. Unknown/unsupported → `NEEDS DATA`, never a fabricated
  value.

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

**Human stays in the loop at:** ambiguous review-queue changes and the real apply decision.
High-confidence official-source refreshes should update automatically.
Everything else is automated.

## Research resolver note

Deep refresh routes stale or incomplete cards through
`backend/ingestion/research_resolver.py`: focused cached searches, real cited URLs, cached
HTTP fetches, source adapters, then compact batched LLM snippets only for exceptions. This
is the web-search fallback named in step 2; it is not a browser-per-card workflow.
Before search, `CardReference` supplies known aliases, issuer domains, pinned URLs, and
learned verified URLs so repeat refreshes can go straight to useful static sources.
Trusted roundup pages are also inspected for matching card-specific review/detail links;
generic anchors like "Read our review" use the surrounding row/paragraph context to identify
the card. Those detail pages are fetched and parsed before falling back to compact LLM
snippets.
