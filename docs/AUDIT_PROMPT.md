# Codebase Audit Prompt and Protocol

Use this file when the user asks for a codebase audit, professional-grade review,
efficiency review, reliability review, optimization pass, or similar request.

## Reusable User Prompt

Run the audit protocol in `docs/AUDIT_PROMPT.md` for
`<repo-root>`.

Audit the full codebase for inefficiencies, redundancies, architectural drift,
data validity gaps, reliability risks, UX rough edges, and optimization
opportunities. Focus on the project goal: a clean two-person churning decision
engine that produces valid, explainable, value-first next actions while staying
cost-conscious with web search and LLM usage.

Do not edit `<separate-churn-control-repo>`. Preserve the
PUBLIC/PRIVATE firewall, cpp convention, review queue, cached web-search
last-resort design, and additive migration rules. Provide prioritized findings
with evidence, risk, concrete remediation, and verification steps. Do not
implement fixes unless explicitly asked.

## Required Context

Before auditing, read:

- `AGENTS.md`
- `CLAUDE.md`
- `docs/PROJECT_PRIMER.md`
- latest entries in `docs/PROJECT_EVOLUTION.md`
- `docs/NEXT_TASKS.md`
- `docs/SYSTEM.md`
- `docs/UX.md`
- every relevant file in `docs/context/`

If any requested audit scope conflicts with these docs, stop and flag the
conflict before proceeding.

## Audit Objectives

Assess whether the app is:

- Correct: value-first, eligibility-gated, explainable, and realistic for a
  two-person household card stack.
- Reliable: public data has provenance, unknowns become `NEEDS DATA`, risky
  deltas go to review, private data never enters ingestion or LLM flows.
- Efficient: refreshes use cached HTTP, source adapters, compact LLM snippets,
  and web search only as a cached last resort.
- Cohesive: Dashboard, Household, Profiles, Card Plan, Pipeline, Redemption,
  and Card Universe each have a clear role and avoid duplicate/conflicting
  logic.
- Maintainable: modules are scoped, typed where practical, tested around key
  contracts, and free of accidental monoliths or stale dead code.
- Professional: mobile and desktop UI states are clean, compact, and stable;
  API/frontend contracts are explicit enough to prevent shape errors.

## Required Checks

Use `rg` first for source discovery. At minimum check:

```powershell
rg -n "HeldCard|UserProfile|ManualTargetedOffer|BenefitUsage|TargetRedemption" backend\ingestion
rg -n "\bany\b|TODO|FIXME|console\.log|debugger" frontend\src backend
rg -n "def _category_coverage|CATEGORY_ALIASES|earn_multipliers|best_category_uses" backend\logic backend\routers
```

Check large files and likely orchestration hotspots:

```powershell
Get-ChildItem -Path backend,frontend\src -Recurse -File |
  Where-Object { $_.Extension -in '.py','.ts','.tsx' } |
  ForEach-Object {
    $lineCount = (Get-Content -Path $_.FullName | Measure-Object -Line).Lines
    [PSCustomObject]@{ Lines = $lineCount; Path = $_.FullName }
  } |
  Sort-Object Lines -Descending |
  Select-Object -First 20
```

Run the baseline verification unless there is a clear reason not to:

```powershell
$files = Get-ChildItem -Path backend -Recurse -Filter *.py | ForEach-Object { $_.FullName }
.\.venv-win\Scripts\python.exe -m py_compile $files
.\.venv-win\Scripts\python.exe -c "import backend.main"
.\.venv-win\Scripts\python.exe -m unittest discover backend/tests
node node_modules\typescript\lib\tsc.js --noEmit -p tsconfig.json
```

Run the frontend command from `frontend/`.

## Review Areas

### Data Reliability

- Public source provenance for current offers, peaks, benefits, earn rates,
  eligibility tags, and valuations.
- Evidence snippets and content hashes.
- Review queue gates for large deltas and uncertain changes.
- Targeted/public offer separation.
- Co-brand currency guards.
- `NEEDS DATA` behavior instead of fabricated values.

### Public / Private Firewall

- `backend/ingestion/` must not query private models or use private household
  state directly.
- Private values stay encrypted at rest where modeled that way.
- LLM calls only receive public snippets/catalog facts, never private user data.

### Decision Quality

- Scoring is value-first.
- Peak score is a timing signal, not the primary rank.
- Eligibility gates every recommendation.
- Referral route combines welcome value and referrer value without confusing the
  public offer itself.
- Product-family logic avoids duplicates without blocking legitimate standard,
  premium, and business-card strategies.

### Efficiency and Cost

- Refresh is cached, concurrent, and source-adapter-first.
- Web search is a stale/unresolved fallback, not routine.
- LLM payloads are compact snippets and batched where possible.
- Repeated app-page loads avoid avoidable N+1 patterns.

### Frontend and UX

- API response shapes are typed in TypeScript where they drive UI behavior.
- Mobile cards are compact and stable.
- Long text is summarized for UI and detailed data is kept behind drill-downs.
- Empty/loading/error states are explicit.
- Dashboard, Household, Profiles, Pipeline, Card Plan, Redemption, and Card
  Universe have non-overlapping jobs.

### Maintainability

- Large files have clear internal boundaries or should be staged for extraction.
- Shared logic is centralized.
- Schema additions are registered in `backend/db.py::_ADDED_COLUMNS`.
- Tests cover scoring, pipeline, research, benefits, redemption, and boundary
  behavior.

## Output Format

Write findings in severity order:

1. Severity and title.
2. Evidence with file references.
3. Why it matters.
4. Recommended fix.
5. Verification after fixing.

Then provide:

- A remediation roadmap split into immediate, next, and later work.
- Any verification results from the current audit.
- A note on what was not changed.

Do not bury high-risk findings under broad summaries.
