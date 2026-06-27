# Project Evolution

This is the living change/audit ledger for the WEwards codebase.

Keep entries concise and focused on code behavior, data model changes, verification, and rollback notes. Do not record private household data, real account details, secrets, local absolute paths, browser profiles, local database contents, or user-specific app state.

## 2026-06-26 23:38 -07:00 - Source Conflict Auto-Repair And Refresh Crash Fix

**Status:** completed. **Scope:** public product source-quality repair, refresh result reporting, valuation refresh resilience.

**What Changed**

- Added automatic quarantine/repair for unsafe product sources that were stranding cards behind `NEEDS DATA` with source identity/product-specific conflicts.
- Refresh now runs the source repair before other refresh work and commits it immediately, so a later refresh error cannot roll back the source cleanup.
- Filtered unsafe learned reference URLs and disabled unsafe product-specific source configs before they can be reused as refresh hints.
- Allowed Atmos product pages to reference Alaska without becoming a false `source_identity_conflict`; those sources still need to be product-specific before they can support ranking.
- Surfaced source-repair counts in frontend refresh messages.
- Made valuation backfill idempotent when the extractor returns duplicate currencies, preventing refresh from crashing on the `valuation.currency` unique constraint.

**Verification**

- Changed-module Python compile passed.
- Focused source-quality/catalog-cleanup/card-reference/catalog-health tests passed.
- Added and passed a duplicate-currency valuation backfill regression test.
- Full backend test suite passed: 101 tests. Existing SQLAlchemy ResourceWarnings for unclosed in-memory SQLite connections still appear.
- Frontend TypeScript typecheck passed.
- Frontend build passed. Existing Vite large-bundle warning remains.
- Local public catalog source repair ran once and repaired 8 unsafe product-source records: 4 replaced, 4 cleared.
- Restarted WEwards on backend `8000` and frontend `5176`.
- Lightweight valuation refresh smoke passed with zero errors after restart.
- Live catalog health reported zero `source_identity_conflict`, zero `broad_source_not_product_truth`, and zero `source_not_product_specific` rows.

**Open Risks**

- Remaining `NEEDS DATA` rows are still expected where public catalog facts or source URLs are genuinely missing/incomplete.
- A deep public-data refresh may still depend on configured web-search/LLM/provider availability and external page behavior.
- Backend tests still emit existing SQLAlchemy ResourceWarnings unrelated to this change.

**Rollback Notes**

- Revert the source-quality, card-reference, catalog-cleanup, refresh-schedule, frontend refresh-message, and ingestion-guard test changes to restore the prior behavior. After rollback, rerun catalog health because previously quarantined unsafe sources should not be trusted for ranking without review.

## 2026-06-26 23:03 -07:00 - CodeGraph Context Index

**Status:** completed. **Scope:** local context tooling, generated graph index, Git ignore hygiene.

**What Changed**

- Initialized a local CodeGraph index for the WEwards project so future broad architecture, ingestion, decision-pipeline, data-reliability, and UI-flow work can start from graph-guided file selection instead of broad source reads.
- Added `.codegraph/` to the project `.gitignore` so generated graph state remains local and cannot be committed by accident.

**Verification**

- CodeGraph status reported the WEwards index initialized and up to date.
- Graph size: 80 files, 1,901 nodes, 5,233 edges.
- Git status showed `.codegraph/` ignored and only `.gitignore` tracked.

**Open Risks**

- The graph is frozen because CodeGraph file watching is disabled by the workspace wrapper. Run CodeGraph `sync` after meaningful source changes before relying on it.

**Rollback Notes**

- Remove `.codegraph/` and revert the `.gitignore` line if the local graph should be removed.

## 2026-06-26 - WEwards Workspace Migration, Rename, And Autopush Restoration

**Status:** completed. **Scope:** project identity, workspace registration, runtime naming, docs/current-state setup, personal autopush policy.

**What Changed**

- Made `WEwards` the canonical app/project name after the folder was moved into the workspace.
- Renamed visible frontend/backend/runtime labels, package metadata, environment-example keys, docs, and the restart helper from the old app name to `WEwards`.
- Renamed the restart helper to `scripts/restart-wewards.ps1` and updated its runtime environment variables to `WEWARDS_*`.
- Added `docs/CURRENT_STATE.md` so future workspace chats can load the project as a normal baselined workspace project.
- Restored Davin's explicit `personal-autopush` policy in `docs/CURRENT_STATE.md`, `AGENTS.md`, and `CLAUDE.md`: approved WEwards implementation work should be verified, sanitized, committed, and pushed without a second approval prompt.
- Migrated ignored local database filenames from the old app-name file to `data/wewards.db` without inspecting or printing database contents.

**Verification**

- Scoped current-source/current-index scan found no remaining old app-brand references in the WEwards source/docs or current workspace index/board/local context files. Lowercase domain phrases such as "credit-card churning" remain where they describe the rewards strategy domain.
- `.env` was checked without printing values; old app env-prefix and database-name strings were not present after key-level migration.
- Sanitization preflight confirmed `.env`, `frontend/.env`, virtualenvs, local data, logs, `frontend/node_modules/`, and `frontend/dist/` are ignored and not tracked.
- `.env.example` contains placeholder configuration only.
- Restart helper parsed successfully.
- Backend compile passed with `.\.venv-win\Scripts\python.exe -m py_compile backend\main.py backend\models.py backend\db.py`.
- Backend import passed with `.\.venv-win\Scripts\python.exe -c "import backend.main"`.
- Backend test suite passed: 96 tests. Existing SQLAlchemy ResourceWarnings for unclosed in-memory SQLite connections still appear.
- Frontend TypeScript typecheck passed.
- Frontend build passed. Existing Vite large-bundle warning remains.
- `git diff --check` passed with line-ending warnings only.

**Open Risks**

- The Git remote still points at the old repository name; changing it is a separate Git/GitHub decision.
- Commit `1f9f808` combines prior uncommitted reliability/UI work with the rename/autopush restoration because those changes were already present in the working tree before the Git-policy restoration.
- Older historical workspace ledger entries still mention old project names as history; current project docs and workspace indexes now point at `WEwards`.
- The app was not restarted in this pass.

**Rollback Notes**

- Revert the rename/autopush patches, move `scripts/restart-wewards.ps1` back to the prior helper name if needed, and restore the prior local database filename from the ignored local file if needed. Do not print or package local database contents during rollback.

## 2026-06-27 - Needs-Data And Benefit Noise Follow-Up

**Status:** completed. **Scope:** catalog decision gating, benefit normalization, refresh automation, Run menu.

**What Changed**

- Demoted missing public peak history from a hard Card Plan blocker to a visible data-quality warning. Cards with sourced current offer, fee, spend, freshness, and valuation can now surface as `WATCH` instead of `NEEDS DATA` while peak history remains unresolved.
- Kept bad source, stale source, missing current offer, missing annual fee, missing minimum spend/window, missing currency, missing valuation, and pending critical current-offer updates as blockers.
- Tightened benefit normalization so display and review paths reject raw page fragments, welcome-offer copy, pricing/legal text, reviews, JSON/meta fragments, and long copied source objects.
- Preserved clean string benefit storage for existing ingestion compatibility while allowing deterministic known-card benefits to remain structured.
- Made refresh automatically run review-queue cleanup so obviously bad benefit proposals are rejected without a separate manual cleanup step.
- Updated normal and deep refresh menu actions to request missing valuation backfill, and surfaced automatic cleanup counts in refresh result messages.

**Verification**

- Backend changed-module compile passed.
- Focused data-quality/benefit/catalog-health/ingestion/research resolver suite passed: 85 tests.
- Full backend test suite passed: 96 tests.
- Frontend TypeScript typecheck passed.

**Open Risks**

- Public valuation backfill still depends on configured web-search/LLM availability and published valuation sources.
- Rows with genuinely bad stored sources, such as a product pointing at a different card family page, still need refresh to replace the source before they can become decision-ready.
- Backend tests still emit existing SQLAlchemy ResourceWarnings for unclosed in-memory SQLite connections.

**Rollback Notes**

- Revert the catalog/scoring missing-peak gating changes, benefit normalization/validation changes, refresh cleanup addition, and Run menu valuation payload changes to restore the prior stricter `NEEDS DATA` behavior.

## 2026-06-27 - Offer Reliability And Household Route Hardening

**Status:** completed. **Scope:** public offer ingestion, catalog health, card references, household routing, Card Plan/Household/Dashboard UI.

**What Changed**

- Stopped promoting a current public offer into first-sight public peak data. Current offers now raise public peak only when an existing public peak is exceeded by an official product page with supporting evidence.
- Added field-level evidence gates for auto-committed official offer/fee/spend changes.
- Added official closed-to-new adoption: issuer pages that say a product is no longer accepting applications now tag the product `closed_to_new_applicants` and clear stale current-offer fields without erasing historical peak data.
- Marked Citi Custom Cash inactive in seeded references and prevented inactive seeds from creating new product shells.
- Kept reference-seeded cards such as Amex Gold visible as `NEEDS DATA` after fee/benefit-only refreshes instead of disappearing from Card Plan.
- Added Capital One Venture-family 48-month bonus eligibility and Venture-family household referral routing.
- Made catalog health classify unsafe source issues as `needs_data`.
- Tightened benefit-disclosure noise filters and fixed CardForm quick-fill identity selection.
- Removed light blue card/menu tint in light mode and made Run menu styling neutral.
- CardPlan now treats “Known offers” as actual current offers and falls back to card-reference source URLs.

**Verification**

- Focused backend reliability suite passed: 46 tests.
- Full backend test suite passed: 95 tests.
- Frontend TypeScript typecheck passed.
- Python changed-module compile passed.
- Official public spot checks used for the reliability audit: Citi Custom Cash closed to applications as of May 28, 2026; Chase Sapphire Reserve Business current public page shows 200,000 points after $30,000 in 6 months with a $795 annual fee.

**Open Risks**

- No live refresh/deep refresh was run against the local private catalog database in this pass.
- Backend tests still emit existing SQLAlchemy ResourceWarnings for unclosed in-memory SQLite connections.

**Rollback Notes**

- Revert the ingestion validation/schedule/static parser changes, reference seed changes, Venture eligibility/household route changes, catalog health status changes, and frontend UI/display patches to return to prior behavior. Re-run the focused reliability tests before trusting recommendations after rollback.

## 2026-06-26 - Dashboard Light Mode And Benefit Disclosure Filtering

**Status:** completed. **Scope:** Dashboard homepage styling, public benefit normalization/display.

**What Changed**

- Added explicit Dashboard homepage surface classes so desktop light mode uses neutral app surfaces instead of inheriting broad blue `bg-ink-*` hover/background remaps.
- Added editorial-disclosure benefit noise guards in extraction, validation, catalog display, and benefit tracker logic.
- Added a benefit tracker regression test proving disclosure copy is filtered while real official-source benefits still normalize.
- Cleaned locally stored public benefit rows that matched the new disclosure-noise guard.

**Verification**

- Backend changed-module compile passed.
- Backend import passed.
- Focused benefit disclosure regression test passed.
- Full benefit tracker test file passed.
- Frontend TypeScript typecheck passed.
- Local public catalog check reported zero stored benefit rows matching the disclosure-noise guard.

**Rollback Notes**

- Revert the Dashboard surface class/CSS additions and the disclosure-noise guard changes to return to the prior styling/filter behavior. Re-run refresh only after confirming source adapters are not reintroducing raw disclosure copy.

## 2026-06-26 - Desktop Expansion Polish And Shared Crawl4AI Defaults

**Status:** completed. **Scope:** rendered fallback setup, desktop UI interaction, profile balances/cards.

**What Changed**

- Defaulted Crawl4AI rendered fallback state and Playwright browser cache to the shared workspace toolbench when present, while ignoring blank env overrides so project roots are not used accidentally.
- Added canonical frontend point-currency options shared by desktop/mobile Profiles, including Citi ThankYou Points and case-insensitive de-duplication.
- Reworked desktop Dashboard, Household, Profile cards, and Pipeline action rows so the same compact cards expand/collapse for details instead of static non-clickable tables.
- Added desktop Profile Active/Closed card toggles so closed/cancelled held cards remain visible, and moved held-card actions into expanded details instead of a cramped right-side stack.
- Cleaned remaining monospace UI number styling and softened the Profile value-by-currency chart with a quieter palette and matching legend dots.
- Added per-card 5/24 status labels in Profile card rows so closed cards clearly show whether they still count or have aged out.

**Verification**

- Frontend TypeScript typecheck passed.
- Backend changed-module compile passed.
- Backend import passed and Crawl4AI availability check returned true.
- Backend test suite passed: 89 tests.
- Focused closed-card 5/24 regression test passed.
- Blank Crawl4AI env override resolves to the shared toolbench default instead of the repo root.

**Rollback Notes**

- Revert the shared currency helper, Profile/Dashboard/Household/Pipeline row rendering changes, and Crawl4AI default-path helpers to restore prior static-table behavior and project-local Crawl4AI defaults.

## 2026-06-26 - Vite Cloudflare Tunnel Host Allowlist

**Status:** completed. **Scope:** frontend dev-server config.

**What Changed**

- Added a Vite config that allows temporary `*.trycloudflare.com` tunnel hosts and proxies `/api` to the local backend on port 8000.

**Verification**

- Frontend TypeScript typecheck passed.
- Frontend restarted on port 5176.
- Local Vite response passed.
- Simulated `abc.trycloudflare.com` Host header returned 200.

**Rollback Notes**

- Remove `frontend/vite.config.js` to restore Vite default host restrictions.

## 2026-06-24 - GitHub Fresh-Slate Preparation

**Status:** completed. **Scope:** repository hygiene, security, documentation.

**What Changed**

- Prepared the repository for private GitHub storage as a reusable bare-bones application structure.
- Converted shipped two-user defaults and documentation examples to generic `User A` / `User B` language.
- Removed historical project-state notes from this ledger to avoid committing private workflow details or local verification state.
- Added a rule that this log must not contain private data, secrets, absolute local paths, or runtime state.
- Made the default profile roster configurable through `WEWARDS_USERS`.
- Hardened `.gitignore` for env variants, runtime logs, local browser profiles, databases, build output, virtualenvs, and local agent/tool state.
- Removed local `.env`, SQLite/cache data, runtime logs, browser profiles, and local tool state from the working folder where Windows allowed removal.

**Verification**

- Secret/name/path scan found no real profile names, absolute local workspace paths, or obvious token/key patterns in the publishable tree.
- `git ls-files` scan found no tracked local DB/log/build/data artifacts except `.env.example`, which is intentionally tracked.
- Backend compile/import passed using temporary SQLite/cache paths.
- Frontend typecheck passed.
- Frontend build passed; existing Vite large-bundle warning remains.
- Backend tests passed: 75 tests.

**Rollback Notes**

- Restore the previous documentation history only from a private local backup if needed. Do not publish private historical app-state notes.

## 2026-06-26 - Add-Card Quick Fill Uses Card References

**Status:** completed. **Scope:** frontend held-card entry workflow.

**What Changed**

- The held-card add/edit form now builds quick-fill options from both the scored catalog and the public card reference registry.
- Known reference cards remain selectable even when they are hidden from the user-scored catalog because they are already held, low priority, or otherwise not an application candidate.
- Reference-only quick-fill fills identity fields and lets the backend variant matcher attach the canonical catalog product when available.

**Verification**

- Frontend typecheck passed.

**Rollback Notes**

- Revert the `CardForm` reference-source prop and caller changes to return quick-fill to scored-catalog-only behavior.

## 2026-06-26 - Bilt References And Token-Aware Quick Fill

**Status:** completed. **Scope:** public card references and held-card entry workflow.

**What Changed**

- Added Bilt Blue, Bilt Obsidian, and Bilt Palladium to the public card reference seed list and seeded catalog shell behavior.
- Added Bilt product identity handling so Bilt cards use short display names and `Bilt Rewards` currency.
- Replaced the native browser datalist quick-fill with an in-app token-aware search so aliases and reordered words work consistently.

**Verification**

- Backend changed-module compile passed.
- Focused backend tests passed for card references and display-name identity.
- Backend import passed.
- Frontend typecheck passed.
- Local app restart passed; Bilt references and catalog shell rows are visible through the live API.

**Rollback Notes**

- Revert the Bilt seed/product identity additions and `CardForm` search control changes.

## 2026-06-26 - Card Plan Light Mode And Mobile Card Archive

**Status:** completed. **Scope:** frontend display surfaces.

**What Changed**

- Added theme-aware Card Plan table classes so desktop light mode no longer keeps the dark table background, header, expanded row, and manual-offer input styling.
- Added a mobile Profile archive toggle so closed/cancelled held cards remain accessible without cluttering the active card stack.

**Verification**

- Frontend typecheck passed.

**Rollback Notes**

- Revert the Card Plan theme class/CSS additions and the `MobileProfile` archive-toggle state/render changes.

## 2026-06-26 - Sapphire Reserve Business Issuer Offer Extraction

**Status:** completed. **Scope:** public card reference and static parser reliability.

**What Changed**

- Added a public reference seed for Chase Sapphire Reserve Business with the direct Chase business Sapphire URL.
- Preserved script-free issuer-page text as a static parser fallback so nested hero markup can still yield welcome-offer evidence.
- Added a compact bonus/spend parser path for issuer hero copy where nested spans remove expected spaces.

**Verification**

- Focused backend tests passed for card references, display-name identity, and Sapphire Reserve Business static extraction.

**Rollback Notes**

- Revert the Chase Sapphire business reference seed, parser fallback changes, and related tests.

## 2026-06-26 - Automatic Official-Source Data Adoption And Decision Quality Gates

**Status:** completed. **Scope:** public ingestion reliability, catalog scoring quality, pipeline/household recommendation safety.

**What Changed**

- Added shared public source-quality helpers for product-specific source validation, broad-roundup detection, issuer-domain checks, and close-variant/cobrand conflict detection.
- Allowed high-confidence official issuer product pages to auto-adopt current public offer terms, minimum spend, spend window, annual fee, and first-year credit values with evidence.
- Kept broad/current-offer sources useful for first-sight evidence, but blocked broad or conflicting sources from overwriting large existing offers, writing unsafe product-specific facts, or becoming product-level source truth.
- Routed web-search rows through the same scan-row application path as static rows so source stripping, evidence logging, targeted/public separation, and commit/proposal behavior are consistent.
- Added catalog decision-quality blockers so stale, pending, missing, broad-source, conflicting-source, or incomplete public data becomes `NEEDS DATA` and cannot enter `APPLY NOW`, Pipeline next cards, Household moves, or Dashboard actions.
- Required a verified public peak for actionable status; targeted peaks remain informational and cannot make a card decision-ready by themselves.
- Added concise data-quality issue fields to catalog, pipeline, household, and frontend DTOs.

**Verification**

- Focused ingestion/data-quality tests passed.
- Full backend test suite passed with isolated test users: 89 tests.
- Backend compile and `import backend.main` passed.
- Frontend TypeScript typecheck passed.
- PUBLIC/PRIVATE ingestion firewall scan found no private model references.

**Rollback Notes**

- Revert `backend/source_quality.py` and the source-quality calls in ingestion, catalog, pipeline, household, and frontend DTOs to restore the prior review-queue-only behavior. Revert the new tests if the policy is intentionally relaxed.
