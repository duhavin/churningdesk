# DECISION_RULES — what counts as a "good deal" / "best next move"

This is the **spec** the scoring (`backend/logic/scoring.py`) and pipeline
(`backend/logic/pipeline.py`) must satisfy. If code and this doc disagree, fix one of them
in the same change — don't let them drift.

## Core principle: VALUE-FIRST, peak is timing

- A card's **status and rank are driven by VALUE**, not by % of peak.
- `peak_score` (current offer ÷ all-time **public** peak) is a **TIMING signal** — "is now a
  good moment to grab this card" — **not** the primary ranker.
- ❌ A 20,000 / 20,000 offer (100% of a tiny peak) must **NOT** outrank a 90,000 / 100,000
  offer (a large bonus near its peak). Big real value wins.

## Status thresholds

- **APPLY NOW** requires **both**: strong `peak_score` (≥ `APPLY_NOW_THRESHOLD`) **AND**
  clearing an absolute value floor — `offer_value ≥ MIN_APPLY_VALUE` **or**
  `effective_points ≥ MIN_APPLY_POINTS` (configurable in `config.py`). Below the floor →
  **WATCH** even at 100% of peak.
- **WATCH** requires `offer_value ≥ MIN_WATCH_VALUE`, else **LOW PRIORITY**.
- **WAIT** = eligible but offer well below peak (timing not right).
- **NEEDS DATA** = unknown peak or unknown value — never ranked as a deal, never fabricated.
- **SKIP** = permanently ineligible (e.g. Amex once-per-lifetime already earned, closed).
- **FUTURE** = tagged for a future trip, not yet time.

## Ranking of the actionable queue

Order by **estimated first-year / household value**, with strategic modifiers, then timing:
1. **Strategic bands** first: Chase-first while under 5/24; Chase business (Ink) prioritized
   (doesn't add to 5/24 but needs you under it).
2. **Value** (`offer_value`, or household value incl. referral capture) — primary sort.
3. **Preference nudge** for solid transferable-currency issuers (Amex MR, Chase UR,
   Capital One Miles) and large bonuses.
4. **`peak_score`** as a tiebreaker / timing flag only.

## Quality bias

- Favor **large bonuses (≈75k–100k+)** from **transferable-currency** issuers (Amex, Chase,
  Capital One).
- Treat small, cashback-only, or store/retail bonuses as **low priority by default**.

## Household + hygiene rules

- **Referrals both directions**: if the other user holds a card this user is eligible for,
  recommend applying via their referral link; value the move as welcome + referral.
- **No redundant cards per person**: never recommend a card a user already holds, or a same
  product-family duplicate for that same user (e.g. Sapphire Preferred vs Reserve, Venture vs
  Venture X). Re-eligible held cards return as **"requeue"**, not "open". The other household
  member holding the card is **not** a close/cancel reason; overlapping accounts can be valid
  for separate welcome bonuses, credits, lounge access, referrals, and individual spend.
- **Eligibility/timing gate everything** — an ineligible card is never APPLY NOW.

Additional same-family handling: same-family ladder cards can be surfaced as **alternate
strategies** only, with explicit upgrade/downgrade/close-then-apply language. They do not
enter the ordinary apply queue.

## Retention side

- Surface keep / **downgrade** / **cancel** before annual fees post or renewal windows
  close; downgrade when ongoing value < annual fee and a retention attempt failed.

Every recommendation must state its **binding reason**.
