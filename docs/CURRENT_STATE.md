# WEwards Current State

Last updated: 2026-09-05 -07:00

## September enhancement checkpoint

W1/W2 implementation and local verification are GREEN after Astra review and
root independent whole-project review. Dashboard, Pipeline and Household
consume one household next move and at most two recomputed conditional successors,
using the existing strategy order. A WAIT can remain first. Optional encrypted
per-person organic monthly capacity is reachable on desktop and phone Profiles;
blank clears it to unknown. Cumulative feasibility checks each candidate/active
commitment deadline, including later obligations, without double counting or
inventing capacity. No hypothetical planning rows persist.

Current public offer amount, spend, window and known expiration require matching
fresh field evidence; unrelated last_verified changes cannot certify them. Missing
published expiration is disclosed without invalidating otherwise fresh terms.
A selected higher private offer needs its own confirmed spend/window terms.
Shared conditions also govern referral Apply links and user-specific pipeline
labels. Existing public/private encryption and ingestion review boundaries remain.

Verification: 23 focused and all 259 backend tests passed (full 3.738 seconds),
frontend typecheck and isolated production build passed. Fictional real API/browser
flows passed at desktop, phone touch/DPR2 and tablet sizes, including capacity
save/clear and referral action conditions. Evidence is in workspace output
`enhancement-20260905/wewards-final/`; local font CSS was substituted to keep
transports offline. Deployed runtime and external font delivery remain unverified.
The scoped source checkpoint contains only the 33 W1/W2 paths. Its separate
publication candidate passed 252 backend tests, typecheck, production build and
the same 27 browser cases; the additional working-tree ingestion tests and
preexisting ingestion changes remain local. Sanitize and exact-index review
passed. Runtime deployment remains separate. See ENHANCEMENT_PLAN.md.

Workspace hosting (2026-07-16 additive note): WEwards has a per-app tailnet
hosting stack at `toolbench/hosting/wewards/`. Remote access is LIVE at both
`https://wewards.tail26040e.ts.net` and the browser-trusted private name
`https://wewards.dwagon.app` (served by the `dwagon.app` domain gateway,
`toolbench/hosting/domain-gateway/`). Verified 2026-07-16: HTTPS returns HTTP
200 serving the real dashboard (title `WEwards — Household Rewards Dashboard`).
This note is additive only. (The `Dockerfile`/hosting changes previously
flagged by context-status were reconciled into Built State on 2026-07-16.)

## Project Mode

Primary mode: `personal-autopush`

Mode notes:

- WEwards is Davin's personal project repo.
- After Davin approves implementation work in this repo, run the relevant
  verification and sanitize/security preflight, then commit and push the
  approved changes to `origin/main` without asking again.
- Do not auto-commit if the working tree includes secrets, private data,
  databases, logs, browser profiles, generated media, dependency folders,
  unrelated user work, unresolved test failures, or unclear scope.
- Remote changes, deployment, dependency installs, and global config changes
  still require explicit approval.

## Project Identity

Project: WEwards

North star: reliable, explainable household credit-card churning decisions for
two users, using private household state and sourced public card data.

Target user: Davin, with the app supporting either a two-person household or a
solo-user setup when only one profile is active.

Current product goal: keep the apply/wait/refer/benefit/renewal decision loop
accurate, sourced, household-aware, and easy to act on.

Current non-goals:

- No automatic real-world card applications.
- No private-data LLM analysis.
- No broad app rewrite for vague polish requests.
- No live award-search dependency as the core workflow.
- No fabricated offer, rule, valuation, or source data.

## Active Implementation

Active path: repository root.

Primary entrypoints:

- Backend: `backend/main.py`
- Backend tests: `backend/tests/`
- Frontend: `frontend/src/App.tsx`
- Frontend pages: `frontend/src/pages/`
- Restart script: `scripts/restart-wewards.ps1`

Archived/inactive paths:

- Pre-rename naming may appear in historical ledgers. Do not revive or recreate
  pre-rename paths unless Davin explicitly asks.

Do not touch without approval:

- `.env`
- `data/`
- `logs/`
- virtual environments
- `frontend/node_modules/`
- `frontend/dist/`
- private household data or local runtime state

## Current Priority

Current approved priority: keep the core household decision loop reliable and
professional.

Near-term focus:

1. Public card data quality and provenance.
2. Decision gating that avoids false confidence.
3. Household/referral-aware next-action ordering.
4. Benefit tracking that filters source/disclosure noise.
5. UI surfaces that make the next action and binding reason obvious.

## Built State

Currently built:

- FastAPI backend and React/Vite frontend.
- PUBLIC/PRIVATE SQLAlchemy model split.
- Eligibility, scoring, catalog, household, benefits, redemption foundation.
- Public ingestion, validation, review queue, card references, and source
  reliability logic.
- Dashboard, Profiles, Card Plan, Pipeline, Household, Redemption, and Card
  Universe surfaces.
- Local decision-engine work through `95480d7` is ready for backup: web-search
  and rendered fallback defaults are enabled, Delta SkyMiles identity/source
  guards are fixed, Decision Engine V2 is present, and the 14-finding audit fix
  pass across scoring, pipeline, catalog, household, decision_context, and
  config is complete.
- 2026-07-06 ingestion text-quality pass: `backend/text_sanitize.py` sanitation
  layer (mojibake repair + scrape-junk detection), hardened benefit gates with
  description salvage and concept dedupe, a referral research query (referral
  bonuses were never researched before — 0/43 populated), free-text sanitation
  in the apply path, and a `sanitize-text` catalog sweep (endpoint + Card
  Universe button) that was run locally: junk re-scan now flags 0/43 cards.
- 2026-07-14: Docker packaging; the app also runs as a tailnet-only container
  (`wewards-wewards-1` behind a Tailscale sidecar) with `.env` injected at
  runtime and `data/` bind-mounted (shared with local runs). Chromium is baked
  in so Crawl4AI works in the container. Redeploy = rebuild via the workspace
  `/docker` flow so the container matches the working tree.
- 2026-07-16 audit + fix pass (fully-autonomous doctrine): re-bonus windows
  enforced on the apply path (closed cards count; shared rule table across
  apply + requeue); points-first scoring (`POINTS_FIRST`, default on) with
  honest cash-only tracking/filtering and bundle valuation (no targeted+cash
  double-count); high-value below-peak offers surface as WAIT; super-family
  referrals labeled honestly; household/pipeline/referral ranking unified per
  DECISION_RULES; autonomous review verification (official/independent-source
  corroboration, self-expiring proposals, no human queue); strict host
  matching; plausibility + unit gates on all commit paths; referral evidence
  parity; fetch-failure honesty (stale-if-error age cap, no false
  `last_verified`); peak-research rotation/cooldown (additive column
  `peak_research_attempted_at`); discovery memory-mode eligibility dropped.
  227 backend tests pass; frontend typecheck/build clean.

Known active risks:

- Public card data can become stale or mismatched if source pages drift.
- Benefit and offer extraction can produce noise unless parser, normalization,
  and apply-time validation gates remain strict.
- Refresh/verification flows must distinguish between fixed data, cooldown
  skips, pending review, missing public fixed offers, and true source failures.
- Private data must remain local and out of LLM/web flows.
- Pre-rename naming can confuse scripts, docs, or ports.

## Run, Build, Test, Serve

Backend dev:

```powershell
python -m uvicorn backend.main:app --reload --port 8000
```

Frontend/stable local:

```powershell
.\scripts\restart-wewards.ps1
```

Dedicated ports:

- Backend API: `8000`
- Frontend/static proxy: `5177`

Backend verification:

```powershell
python -m py_compile backend\main.py backend\models.py backend\db.py
python -c "import backend.main"
python -m unittest discover backend.tests
```

Frontend verification:

```powershell
cd frontend
npm run typecheck
npm run build
```

## Source Of Truth

Product/workflow truth:

- `AGENTS.md`
- `CLAUDE.md`
- `docs/PROJECT_PRIMER.md`
- `docs/PROJECT_INTENT_CARD.md`
- `docs/NEXT_TASKS.md`
- `docs/context/VISION.md`
- `docs/context/PROCESS.md`
- `docs/context/DECISION_RULES.md`

Data truth:

- PUBLIC card/product/source data in backend models and seeded references.
- PRIVATE household data only in local encrypted/private storage.
- Source/evidence fields for public data changes.

Docs/ledger truth:

- `docs/CURRENT_STATE.md` for current operating state.
- `docs/PROJECT_EVOLUTION.md` for historical change ledger.

## Sensitive And Private Boundaries

Private data:

- Held cards, last4s, credit limits, open dates, balances, point totals,
  targeted offers, benefit usage, redemption goals, and local user state.

Secrets/credentials:

- `.env`, API keys, Fernet key, local database paths, provider credentials.

Files never to commit:

- `.env`
- `.venv/`, `.venv-win/`
- `data/`
- `logs/`
- `frontend/.env`
- `frontend/node_modules/`
- `frontend/dist/`
- cache and `__pycache__` folders

External API/LLM restrictions:

- Ingestion may use approved PUBLIC data flows only.
- PRIVATE data never goes to LLMs, web search, crawlers, or external APIs.

## Git And Backup Policy

Remote:

- `origin` still points at the pre-rename repository identity:
  `git@github.com:duhavin/churningdesk.git`.

Default branch: `main`

Current backup target:

- Commit, tag, and push the current WEwards state requested 2026-07-05.
- Rollback tag: `v2026-07-05-wewards-backup`.

Autopush rule:

- For approved WEwards implementation work, run verification and
  sanitize/security preflight, then commit and push to `origin/main`
  automatically.
- If the preflight finds sensitive files, unrelated user work, failing critical
  checks, or unclear scope, stop and report before committing.
- Remote rename, GitHub settings changes, dependency installs, deployment, and
  global config changes are outside autopush and still need explicit approval.

## Handoff Notes

Next chat should read:

- workspace `AGENTS.md`
- WEwards `AGENTS.md`
- `CLAUDE.md`
- `docs/PROJECT_PRIMER.md`
- `docs/PROJECT_INTENT_CARD.md`
- `docs/CURRENT_STATE.md`
- `docs/NEXT_TASKS.md`
- latest 5 relevant entries from `docs/PROJECT_EVOLUTION.md`

Do not assume:

- pre-rename naming is still canonical,
- private household state is safe to inspect or share,
- public offer/benefit values are valid without source/evidence,
- dirty Git state is safe to commit without sanitize preflight.

## Update Rule

Update this file after meaningful changes to project mode, active path,
run/serve commands, fixed ports, Git policy, sensitive boundaries, built state,
current priorities, or next approved task. Also update
`docs/PROJECT_EVOLUTION.md`.
