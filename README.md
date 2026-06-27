# WEwards

WEwards is a local two-user credit-card churning decision engine. It combines user-entered private household state with sourced public card data to recommend the best next actions: apply, wait, refer, renew, downgrade, cancel, use a specific card, or mark data as needing review.

This repository is intended to ship as application code only. It does not include a populated SQLite database, `.env`, API keys, browser profiles, local caches, runtime logs, or personal account data.

## Core Idea

Most rewards apps optimize cards already in a wallet. WEwards is focused on household strategy:

- Which user should apply next.
- Whether the route should be direct or through the other user's referral.
- Whether the current offer is valuable enough and well-timed.
- Whether eligibility rules allow the move.
- Which held-card benefits, renewals, downgrades, or cancellations need action.
- Which catalog facts are missing or need review before a recommendation is trusted.

## Stack

- Backend: FastAPI, SQLAlchemy 2, Pydantic 2, SQLite.
- Frontend: React, TypeScript, Vite, Tailwind.
- Private-field encryption: Fernet through SQLAlchemy type decorators.
- Public-data ingestion: cached HTTP/static parsing first; LLM/web search only for public data and only behind explicit configuration.

## Security Model

Private user data includes held cards, last four digits, credit limits, open dates, point balances, targeted offers, benefit usage, and redemption goals. Sensitive private fields are encrypted at rest when `FERNET_KEY` is configured.

Public card data includes products, offers, peaks, benefits, earning multipliers, eligibility tags, valuations, ingestion evidence, and proposed changes.

The ingestion subsystem must only operate on public product/source data. It must not read private tables or send private user data to LLM/web flows.

## Fresh Setup

Create and fill `.env` from the example:

```bash
cp .env.example .env
python -m backend.crypto
```

Paste the generated Fernet key into `FERNET_KEY`. Add API keys only if you want ingestion or award-search features enabled.

Install backend dependencies:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Install frontend dependencies:

```bash
cd frontend
npm install
```

Run backend:

```bash
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Run frontend:

```bash
cd frontend
npm run dev
```

Default frontend port is `5176`; API requests proxy to `127.0.0.1:8000`.

## Fresh-Slate Data Behavior

On first backend startup, the app creates a local SQLite database under `data/`. That directory is ignored by Git. The initial database contains schema and public card-reference seed structure only; users must enter their own private household data.

Default profile labels are generic: `User A` and `User B`. Change them in configuration for your own local deployment if desired.

## Useful Commands

Backend import smoke:

```bash
python -c "import backend.main"
```

Frontend typecheck:

```bash
cd frontend
node node_modules/typescript/lib/tsc.js --noEmit -p tsconfig.json
```

Frontend build:

```bash
cd frontend
npm run build
```

Windows restart helper:

```powershell
.\scripts\restart-wewards.ps1
```

## Repository Hygiene

The repo intentionally ignores:

- `.env` and env variants.
- `data/`, SQLite DBs, search/cache files.
- `logs/`, browser profiles, runtime logs.
- virtual environments, `node_modules`, frontend build output.
- local agent/tool state.

Before pushing changes, run a secret/data scan and verify `git status --ignored` does not show private state as tracked.
