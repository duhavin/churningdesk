# WEwards Project Intent Card

Last updated: 2026-07-16

Purpose: compact guardrail against within-project drift. This file should be
loaded before broad planning, UI/data changes, specialist handoffs, or claims
that a WEwards task is resolved.

## North Star

WEwards is a local, household-aware credit-card churning decision engine for
Davin's personal use. It should turn private household state plus sourced
public card data into accurate, explainable next actions for applying, waiting,
referring, using benefits, renewing, downgrading, or cancelling.

Owner goal (stated 2026-07-16): a **fully autonomous** churning decision engine
with accurate, up-to-date information — optimal suggestions and pipeline per
individual plus household synergy and referrals, **maximizing transferable
points for travel**. Data verification is automated (corroboration gates, not a
human review queue); Davin should never have to verify ingested data himself.

The product is not a generic card catalog, a raw data dump, or an agent that
sounds confident without source truth. The deliverable is a trustworthy
decision loop: accurate data in, useful household action out, proof preserved.

## Core Loop

1. Maintain PUBLIC card/product/source facts with provenance, freshness, review
   gates, and explicit `NEEDS DATA` states when confidence is missing.
2. Keep PRIVATE household state local and separate: held cards, timelines,
   benefit usage, balances, goals, and targeted offers never go to LLM/web
   flows.
3. Combine public and private facts through eligibility, scoring, referral,
   benefit, renewal, downgrade, cancellation, and redemption logic.
4. Show the smallest clear next action with the binding reason, source status,
   and what would change the recommendation.
5. Track outcomes, review corrections, and source reliability so the system
   becomes more accurate without fabricating or overfitting.

## Current Near-Term Direction

- Make public card data quality and provenance boringly reliable.
- Make the apply/wait/refer/benefit/renewal loop easier to understand at a
  glance, especially in Profiles, Card Plan, Pipeline, and Card Universe.
- Simplify cluttered UI into action-first surfaces: current offer, known peak,
  eligibility, benefit value/status, source health, and next move.
- Improve ingestion/tracking from real failures and tests before adding
  advanced agent autonomy, live award-search dependence, or broad new features.

## Resolution Standard

Every WEwards task must carry this frame before edits:

```text
Project intent:
Current problem frame:
User-visible resolution:
Data/source contract:
Non-drift boundaries:
Verification to prove resolution:
```

Do not call a task done until:

- the target workflow/page/module is named,
- the relevant data contract is checked from source to API to UI,
- unknown offer/benefit/rule values remain `NEEDS DATA` instead of invented,
- the user-visible outcome is simpler or more accurate,
- backend tests/import checks and frontend typecheck/build run when relevant,
- remaining risks are stated in `docs/PROJECT_EVOLUTION.md`.

## Data And Trust Contracts

- PUBLIC data includes card facts, current public offers, public peak history,
  benefits, multipliers, fees, credits, rules, source URLs, evidence, freshness,
  and review status.
- PRIVATE data includes household card ownership, targeted offers, usage,
  balances, goals, local preferences, and real action history.
- Public current offers, public peaks, targeted offers, targeted highs, and
  manual corrections are separate facts.
- Source truth beats model memory. Web/LLM extraction can assist only through
  approved PUBLIC flows with evidence and validation.
- Ambiguous, noisy, stale, or conflicting extracted data goes to review or
  `NEEDS DATA`; it does not become confident ranking input.

## Non-Drift Boundaries

- Do not rebuild the app broadly when one workflow slice is the issue.
- Do not bury Davin in paragraphs when a table, status chip, action row, or
  ranked card summary is clearer.
- Do not optimize for subagent activity, context packaging, or token savings at
  the cost of understanding and resolving the actual workflow.
- Do not promote live award search or advanced autonomy ahead of public-card
  truth, household decision quality, and professional core UX.
- Do not use stale Churn/backup metadata, pre-rename assumptions, or other
  projects as WEwards current truth.
- Do not expose private household state to external services, LLMs, crawlers,
  screenshots, or summaries.

## Benefit And Offer Work Standard

For card offers, peaks, benefits, and multipliers:

- show current public offer, known public peak, confidence/source, and last
  verified status separately;
- show targeted/household-specific values only as PRIVATE household state;
- keep benefit rows normalized and useful, not disclosure fragments;
- include the source/evidence trail needed to explain why a value is trusted;
- if a quick public source would settle a value but network/API use is not in
  scope, mark the missing source requirement instead of guessing.

## Agent Working Posture

- Start with the project intent and current problem frame, not with a broad
  code crawl.
- Read the smallest source set that can prove the workflow.
- Prefer one complete, verified improvement over several partial fixes.
- If a subagent or summary loses the thread, return to this card,
  `docs/CURRENT_STATE.md`, and the relevant context docs before continuing.
- If the task no longer improves the core decision loop, stop and reframe.