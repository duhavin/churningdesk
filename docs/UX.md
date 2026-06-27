# WEwards UX And Application Surface Contract

## Product Feel

The UI should feel like a quiet, commercial-grade household finance operations
tool:

- Dense but readable.
- Clear hierarchy.
- Fast to scan.
- No decorative marketing sections.
- Every recommendation has a reason.
- Every uncertain data point is clearly marked.
- Mobile views remain useful, not just squeezed tables.

The app should feel like a decision cockpit for two users, not a credit-card
blog or dashboard demo.

## Global UX Rules

- Show the action first, then the reason.
- Use status badges consistently: `APPLY NOW`, `WATCH`, `WAIT`, `LOW PRIORITY`,
  `NEEDS DATA`, `SKIP`, `FUTURE`.
- Do not present unverified data as authoritative.
- Show loading, empty, error, and partial-data states.
- Do not bury review queue or missing-data alerts.
- Tables should be scannable on desktop and collapse into stable compact rows
  on mobile.

## Page Contracts

### Dashboard

Purpose: answer "what is the household state and what needs action?"

Must show active cards, annual fees, attention items, household value, top next
applications, and referral actions.

### Profiles

Purpose: maintain private per-user state.

Must show held cards, open dates, renewal dates, bonus state, 5/24, point
balances, benefit usage, and deadlines.

### Card Plan

Purpose: evaluate public card opportunities.

Must show card identity, current public offer, effective offer, public peak,
targeted/manual comparison when relevant, offer value, peak score as timing,
status, reason, and provenance/verification state where relevant.

### Pipeline

Purpose: tell each user what to apply for next and why.

Must show ordered queue, eligibility, binding rule, household/referral route,
Chase/5/24/Ink priority when relevant, and wait/skip reasons.

### Household

Purpose: optimize both people together.

Must show combined next moves, referral opportunities, household value impact,
category/use-card guidance, and overlap/diversification considerations.

### Card Universe

Purpose: maintain public data quality.

Must show watchlist, blacklist, review queue, source config, catalog health,
ingestion/discovery results, missing data, and proposed changes.

### Redemption

Purpose: later-stage redemption planning. It should not displace the core
card-acquisition/benefit/pipeline loop unless the user prioritizes it.

## Professional-Grade Standard

For UI work, a change is not done until:

- The page shows the right data from the right source.
- Empty/loading/error states are acceptable.
- Desktop and mobile layouts are usable.
- Text does not overflow important containers.
- The next action is understandable without reading docs.
- Frontend typecheck passes when frontend code changes.
- `PROJECT_EVOLUTION.md` records what changed and how it was verified.
