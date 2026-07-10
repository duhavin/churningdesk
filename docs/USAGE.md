# WEwards Usage And Operations

## Backend

From `<repo-root>`:

```powershell
python -m uvicorn backend.main:app --reload --port 8000
```

Health:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

## Frontend

```powershell
cd frontend
npm run dev
```

Open `http://127.0.0.1:5177`.

The frontend run script serves the built `frontend/dist` app through
`frontend/serve-dist.mjs` and proxies `/api` to `8000`. Run `npm run build`
after frontend edits, or use `scripts\restart-wewards.ps1`, which builds before
starting `5177`.

## Stable Local Restart

WEwards should always run with:

- Backend API on `http://127.0.0.1:8000`
- Frontend/Vite on `http://127.0.0.1:5177`

Use the restart script instead of manually starting extra ports:

```powershell
.\scripts\restart-wewards.ps1
```

The script:

- Stops WEwards Python/Node processes.
- Clears stale listeners on `8000`, `5177`, and the old backend fallback `8017`.
- Handles orphaned Windows multiprocessing workers that can keep serving stale
  uvicorn code after their parent PID disappears.
- Builds the frontend, then starts exactly one backend on `8000` and one
  frontend static/proxy server on `5177`.
- Verifies `5177/api/run/status` and `5177/api/catalog/duplicates` return JSON.

If process or socket inspection is denied, rerun PowerShell as Administrator.
Do not start WEwards on alternate ports unless this file is intentionally
updated at the same time.

## Production-Style Local Serve

```powershell
cd frontend
npm run build
cd ..
python -m uvicorn backend.main:app --port 8000
```

Open `http://localhost:8000`.

## Environment

`.env` is local and must not be committed.

- `FERNET_KEY` - required for encrypted PRIVATE data.
- `ANTHROPIC_API_KEY` - enables PUBLIC ingestion/discovery/refresh LLM work.
- `ANTHROPIC_MODEL` - extraction/discovery model.
- `ANTHROPIC_SEARCH_MODEL` - cited search fallback model.
- `DATABASE_URL` - defaults to `sqlite:///data/wewards.db`.
- `WEB_SEARCH_*` - expensive fallback controls.
- `CRAWL4AI_ENABLED` - enables the rendered fallback used by Deep refresh after static
  extraction misses known public URLs. Defaults to `true`.
- `CRAWL4_AI_BASE_DIRECTORY` / `CRAWL4AI_BASE_DIR` - Crawl4AI local DB/cache root. Defaults
  to the shared workspace toolbench Crawl4AI state folder when present, otherwise
  `data/crawl4ai`. Keep browser profiles, Crawl4AI state, and large crawl output out of the
  committed repo.
- `CRAWL4AI_MAX_URLS_PER_REFRESH` / `CRAWL4AI_TIMEOUT_MS` - cap rendered fallback work.
- `OFFER_DELTA_THRESHOLD` - review queue threshold.
- `AUTO_COMMIT_SMALL_CHANGES` - high-confidence small change behavior.

## Page Workflows

- Dashboard: household status, urgent actions, top next applications, referrals.
- Profiles: private per-user state, balances, held cards, benefits.
- Card Plan: public catalog, offer timing/value, status, ranking.
- Pipeline: ordered application plan and binding reasons.
- Household: combined household/referral strategy.
- Card Universe: admin/data quality, watchlist, blacklist, review queue, sources.
- Redemption: later-stage redemption and transfer planning.

## Run Menu

- Discover cards: public discovery of reputable cards.
- Refresh offers: refresh stale/incomplete public card data from cached/static pages.
- Deep refresh: static/cache first, then capped Crawl4AI rendering for unresolved known
  public pages, then capped cited web-search fallback.
- Valuation refresh: update point valuations.
- Award search: later-phase provider-backed redemption work.

Refresh output should clearly report committed changes, proposed changes,
warnings, deferred cards, and errors.

## Data Review Rules

- Review queue is the human data-quality gate.
- Approve only when source/evidence supports the value.
- Reject fabricated, unclear, targeted-as-public, or mismatched-source values.
- Unknown values should remain `NEEDS DATA`.

## Verification Commands

Backend:

```powershell
python -m py_compile backend\main.py backend\models.py backend\db.py
python -c "import backend.main"
python -m unittest discover backend.tests
```

Frontend:

```powershell
cd frontend
npm run typecheck
npm run build
```

## Common Failure States

- Missing `FERNET_KEY`: private encrypted data cannot be stored/read safely.
- Missing `ANTHROPIC_API_KEY`: normal UI works; Run-menu LLM actions fail/disable.
- Stale public offers: cards should show `NEEDS DATA`, not guessed values.
- Web-search cap hit: refresh should report deferred cards.
- Proposed changes pending: Card Universe review queue needs approval.
