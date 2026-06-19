# Churn — Credit Card Churning Dashboard + LLM Data Engine

A ground-up **FastAPI + React** application for two users (**Davin** and **Marilyn**)
that tracks held credit cards and every churning stat, computes **eligibility from
entered card dates** (5/24, Amex velocity, Sapphire 48-month, …), **LLM-discovers
and scrapes live public offer data** across the reputable card universe (no seed
data), produces a **scaled apply score + ranking**, and recommends an
eligibility-gated **application pipeline**.

Redemption / points-utilization and live award search are deferred (architected,
not built — see §13).

---

## Highlights

- **Eligibility engine, timing-driven** — 5/24, Chase Sapphire 48-month, Amex
  once-per-lifetime + velocity, Citi 8/65-day, Capital One inquiry sensitivity —
  all computed from `HeldCard.date_opened`. Recomputed on every card add/edit.
- **Scaled apply score (0–100)** = closeness of the current/targeted offer to its
  all-time peak, plus a dollar **offer value** and a cross-card **rank**.
- **Status** per card: `APPLY NOW · WATCH · WAIT · LOW PRIORITY · SKIP · FUTURE`.
- **LLM ingestion engine** behind a strict firewall — discovers the card universe,
  scrapes current + peak offers with provenance, and routes changes through a
  delta-gated **review queue**.
- **Application pipeline** — Chase-first while under 5/24, Ink prioritized, ordered
  by peak score then value, with the binding rule stated for each step.
- **Encryption at rest** for sensitive PRIVATE fields (Fernet).

---

## Architecture — the PRIVATE / PUBLIC boundary (non-negotiable)

| Domain | Contents | Rules |
|---|---|---|
| **PRIVATE** | Held cards, last-4, credit limits, open dates, point balances, targeted offers, identity | Local SQLite, sensitive fields **encrypted at rest** (Fernet). Never sent to any LLM/API/network. |
| **PUBLIC** | Card catalog, current/peak offers, multipliers, fees, eligibility rules, valuations, watchlist, blacklist | Public info, web-fetchable + LLM-extractable. Keyed by issuer + product name only. |

- **Ingestion firewall (least privilege):** the ingestion subsystem
  (`backend/ingestion/`) only ever operates on PUBLIC page text + product names.
  It does not import the PRIVATE models (`HeldCard`, `UserProfile`,
  `TargetRedemption`) and cannot read balances, last-4s, or targeted offers.
- **Encryption at rest:** `last4`, `credit_limit`, point balances, and
  targeted-offer figures are encrypted with **Fernet** (`backend/crypto.py`) via
  SQLAlchemy type decorators. Plaintext exists only in-memory for display; the DB
  only ever holds ciphertext.
  > ⚠️ **Losing `FERNET_KEY` makes encrypted data unrecoverable.** Back it up
  > securely **outside** the repo.
- `.gitignore` excludes `data/`, `*.db`/`*.sqlite`, `.env`, build artifacts,
  `__pycache__/`, and `node_modules/`.

---

## Tech stack

- **Backend:** Python 3.11+, FastAPI, SQLAlchemy 2, Pydantic 2, SQLite.
- **Frontend:** React + TypeScript + Vite + Tailwind, Recharts.
- **Encryption:** `cryptography` (Fernet).
- **LLM:** Anthropic API, structured (tool/JSON-schema) output only. Defaults to a
  cost-efficient model (`claude-haiku-4-5`, configurable via `ANTHROPIC_MODEL`).
- **Web fetch:** `httpx` + `trafilatura` → LLM extraction.

---

## Setup

### 1. Backend

```bash
cd churn_app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Generate a Fernet key and create your .env
cp .env.example .env
python -m backend.crypto          # prints a key — paste into FERNET_KEY in .env
```

Edit `.env`:

```ini
FERNET_KEY=<the key you just generated>     # REQUIRED for PRIVATE data
ANTHROPIC_API_KEY=<your key>                # optional — enables Run menu actions
ANTHROPIC_MODEL=claude-haiku-4-5
DATABASE_URL=sqlite:///data/churn.db
```

> Without `ANTHROPIC_API_KEY`, everything works except the Run-menu discovery /
> refresh actions (they return a clear 503). Without `FERNET_KEY`, the catalog /
> scoring UI works but adding PRIVATE data (held cards, balances) is blocked.

Run the API:

```bash
uvicorn backend.main:app --reload --port 8000
```

### 2. Frontend

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173  (proxies /api → :8000)
```

Open **http://localhost:5173**.

### 3. Production (single process)

Build the frontend; FastAPI then serves it from `frontend/dist`:

```bash
cd frontend && npm run build && cd ..
uvicorn backend.main:app --port 8000
# open http://localhost:8000
```

---

## Using it

1. **Dashboard** — add held cards (open dates drive everything). Watch the
   per-user "needs attention" feed (retention calls ≤45d, min-spend deadlines,
   re-eligible bonuses, pending review items, unreviewed discovered cards).
2. **Run menu (top nav)** →
   - **Discover cards** — LLM enumerates the reputable card universe (run
     occasionally).
   - **Refresh offers** — fetch + LLM-extract current/peak offers for known cards
     (run more often). Backfills point valuations.
   - *Run award search* — later phase (needs a live award API).
3. **Card Plan** — the scored catalog: current vs peak (target), `peak_score`,
   `offer_value`, status, rank; filter by issuer / status / tag; add/edit products.
4. **Profiles** — computed 5/24 (with contributing cards), manual point balances
   (encrypted), total estimated value with a per-currency chart.
5. **Application Pipeline** — the ordered, eligibility-gated apply queue plus
   keep/downgrade/cancel guidance for held cards, each with its binding rule.
6. **Card Universe / Review (admin)** — run the engine, manage watchlist /
   blacklist, approve/reject the delta-gated `ProposedChange` queue, review newly
   discovered cards, edit valuations and overrides, see source config.

**Targeted offers** are private and entered on a held card (Dashboard → Add/Edit
card → "My targeted/referral offer"). The engine then compares public vs targeted
and flags `targeted ▲` in the Card Plan when your targeted offer wins.

---

## How scoring works (§6)

```
effective_points = max(current_public_points, my_targeted_offer_points or 0)
peak_score       = round(min(effective_points / peak_offer_points, 1.0) * 100)   # 0–100
offer_value      = effective_points × cpp/100 + current_offer_cash
                   + first_year_credit_value − annual_fee                        # dollars
```

`cpp` is cents-per-point (`Valuation.cpp_effective = override ?? scraped`).
Status thresholds (`APPLY NOW ≥90`, `WATCH 75–89`, `WAIT 50–74`, else
`LOW PRIORITY`) and eligibility gating live in `backend/config.py` /
`backend/logic/scoring.py`.

---

## Project layout

```
churn_app/
├── backend/
│   ├── main.py              # FastAPI app + routers + SPA serving
│   ├── config.py            # env-driven config, users, sources, thresholds
│   ├── crypto.py            # Fernet field encryption  (python -m backend.crypto → key)
│   ├── db.py                # SQLite engine/session
│   ├── models.py            # SQLAlchemy models (PRIVATE + PUBLIC separated)
│   ├── schemas.py           # Pydantic request schemas
│   ├── ingestion/           # PUBLIC-only firewall: discover, fetch, extract, validate, schedule
│   ├── logic/               # eligibility, scoring, catalog, pipeline
│   └── routers/             # cards, catalog, watchlist, blacklist, profiles, pipeline, ingestion, run
├── frontend/                # React + TS + Vite + Tailwind
│   └── src/{pages,components,lib}
├── data/                    # git-ignored SQLite db
├── .env.example
├── requirements.txt
└── README.md
```

---

## Scope limits & verification (§13)

1. **Targeted offers are manual.** The engine compares the public offer to the
   targeted offer you enter; it cannot see your real targeted/referral offers.
2. **Discovery is bounded** to reputable issuers and is not exhaustive — the
   **watchlist** is the manual backstop; the **blacklist** removes unwanted cards
   from the catalog, scoring, pipeline, and future discovery.
3. **Scraping is fragile / ToS-sensitive.** Structured trackers are preferred over
   bank-site DOM scraping; every value stores `source_url` + `last_verified`.
4. **LLM extraction can be wrong.** A review queue + delta-gating protect the data:
   first sight commits with provenance; offer changes >15% or any eligibility-rule
   change require approval; small high-confidence updates may auto-commit
   (`AUTO_COMMIT_SMALL_CHANGES`). The engine acts only on committed values.
5. **Award availability** is a later phase needing a real-time award API
   (e.g. seats.aero — verify current availability/terms); benchmarks ≠ live seats.
6. **Issuer rules drift.** The ingestion engine refreshes them, but **confirm 5/24,
   Amex velocity/lifetime, and the Sapphire 48-month rule** before relying on the
   pipeline. The current rule implementations live in
   `backend/logic/eligibility.py`.

**The application logic is the deliverable; the engine keeps data current, but the
human is the final check before acting.**
