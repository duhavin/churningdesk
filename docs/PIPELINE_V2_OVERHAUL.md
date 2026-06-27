# Pipeline V2 Overhaul

**Status:** In progress  
**Branch:** `rebuild/lean-v0.1`  
**Author:** Davin (initiated 2026-06-27)  
**Scope:** WEwards backend logic only — no frontend changes, no data migration, no new API routes

---

## Goal

Six targeted improvements to the decision engine that tighten the household synergy signal, broaden re-eligibility coverage, and integrate real usage data into renewal scoring. No architectural rewrites — focused surgical changes to existing functions.

---

## Change Log

| # | Change | File(s) | Status |
|---|--------|---------|--------|
| 1 | Referral value into ranking | `household.py` | ✅ Done |
| 2 | BenefitUsage → renewal decisions | `pipeline.py` | ✅ Done |
| 3 | Household apps per quarter guardrail | `config.py`, `household.py` | ✅ Done |
| 4 | Annual benefit dollar value in renewal | `pipeline.py` | ✅ Done |
| 5 | Expand re-eligibility windows | `eligibility.py` | ✅ Done |
| 6 | Expand family referral detection | `household.py` | ✅ Done |

---

## Change 1 — Referral Value Into Ranking

### Problem

`move_sort_key` in `household.py` places `_pipeline_rank_score` before `household_value`. A card ranked 2nd in the per-user pipeline always beats a card ranked 3rd even if the 3rd card has a $300 referral bonus attached. The referral bonus gets computed and displayed but does not influence which card rises to the top of the household view.

### Fix

Move `household_value` before `_pipeline_rank_score` in `move_sort_key`. Pipeline rank is still the tiebreaker; household value (welcome offer + referral bonus) is the primary ordering signal within the same status tier.

Also update the `reason` field in the move when a referral is present, so the household view shows "referral from [user] adds $X" inline.

**File:** `backend/logic/household.py`  
**Function:** `move_sort_key` (lines 322–331), moves-building loop (lines 277–318)

Before:
```python
return (
    STATUS_PRIORITY.get(row.get("status"), 0),
    1 if row.get("is_exceptional") else 0,
    _pipeline_rank_score(row.get("pipeline_rank")),
    row.get("household_value") or 0,
    ...
)
```

After:
```python
return (
    STATUS_PRIORITY.get(row.get("status"), 0),
    1 if row.get("is_exceptional") else 0,
    row.get("household_value") or 0,   # referral lifts this
    row.get("household_points") or 0,
    _pipeline_rank_score(row.get("pipeline_rank")),
    ...
)
```

Also update move's `reason` when referral is present:
```python
reason = nc["reason"]
if ref:
    reason += f" Referral from {ref['from_user']} adds ~${ref_val:,.0f}." if ref_val else f" Route via {ref['from_user']}'s referral link."
```

### Test

- Build household with a card that has a referral bonus — verify it sorts above an equal-value card without referral
- No new DB queries

---

## Change 2 — BenefitUsage → Renewal Decisions

### Problem

`_held_value_signal` scores renewal value purely from catalog data: number of benefits, number of earn categories, downgrade paths, annual fee. It ignores whether the user actually uses their benefits. A card with $300 in annual credits the user never redeems should score lower than one with $300 they consistently use.

### Fix

In `_held_actions`, load `BenefitUsage` rows for each held card at the start. Pass usages to `_held_value_signal`. If usages show high utilization (>= 60% used), add 10 points to the score. If zero usage on all trackable benefits, subtract 8 points.

**File:** `backend/logic/pipeline.py`  
**Functions:** `_held_actions` (line 495), `_held_value_signal` (line 464)

```python
# _held_value_signal gains a third parameter:
def _held_value_signal(card, product, benefit_usages=None) -> tuple[int, list[str]]:
    ...
    # After existing scoring:
    if benefit_usages:
        trackable = [u for u in benefit_usages if u.amount_available]
        if trackable:
            avg_utilization = sum(
                min((u.amount_used or 0) / u.amount_available, 1.0)
                for u in trackable
            ) / len(trackable)
            if avg_utilization >= 0.6:
                score += 10
                drivers.append(f"benefit credits well-used ({avg_utilization:.0%})")
            elif avg_utilization == 0:
                score -= 8
                drivers.append(f"benefit credits unused ({len(trackable)} trackable)")
```

In `_held_actions`, before the loop:
```python
# Load BenefitUsage for all held cards in one query
from sqlalchemy import select
held_ids = [h.id for h in held if h.id]
usage_rows = db.scalars(
    select(models.BenefitUsage)
    .where(models.BenefitUsage.held_card_id.in_(held_ids))
    .where(models.BenefitUsage.period_key != "__all__")
).all() if held_ids else []
usage_by_held = {}
for u in usage_rows:
    usage_by_held.setdefault(u.held_card_id, []).append(u)
```

Then call `_held_value_signal(h, product, benefit_usages=usage_by_held.get(h.id, []))`.

### Test

- User with 100% benefit utilization on Amex Gold → renewal score should be higher
- User with 0% utilization → renewal score should be lower
- No usage data at all → score unchanged from today

---

## Change 3 — Household Applications Per Quarter Guardrail

### Problem

No guard against opening too many cards per quarter. The system can recommend 5 new cards at once with no awareness of recent application pace. Over-application hurts credit scores and risks denial.

### Fix

Add `MAX_APPS_PER_QUARTER` to config (default 4 for a two-user household). Count applications opened in the current quarter across both users. Add `at_pace_cap` bool and `pace_warning` string to the household response.

**Files:** `backend/config.py`, `backend/logic/household.py`

**config.py** — add:
```python
MAX_APPS_PER_QUARTER: int = int(os.getenv("MAX_APPS_PER_QUARTER", "4"))
```

**household.py** — in `build_household`:
```python
import datetime as dt
today = dt.date.today()
quarter_month = ((today.month - 1) // 3) * 3 + 1
quarter_start = dt.date(today.year, quarter_month, 1)

total_quarter_apps = sum(
    1 for u in users
    for h in active_by_user[u]
    if h.date_opened and h.date_opened >= quarter_start
)
at_pace_cap = total_quarter_apps >= config.MAX_APPS_PER_QUARTER
```

Add `at_pace_cap` and `total_quarter_apps` to the returned dict. Add `pace_warning` to moves when `at_pace_cap`:
```python
if at_pace_cap:
    move["pace_warning"] = (
        f"Household is at {total_quarter_apps}/{config.MAX_APPS_PER_QUARTER} "
        "apps this quarter — confirm credit score / inquiry tolerance before applying."
    )
```

### Test

- Household with 4 cards opened this quarter → `at_pace_cap=True`, `pace_warning` present
- Household with 2 cards this quarter → `at_pace_cap=False`, no warning

---

## Change 4 — Annual Benefit Dollar Value in Renewal

### Problem

`_held_value_signal` scores benefits by count (12 pts per benefit, max 36). A card with three $10 courtesy benefits scores the same as one with a $300 annual airline credit. The annual fee deduction uses actual dollars but the benefit score doesn't.

### Fix

Add `_annual_benefit_value` helper that extracts dollar amounts from `card_benefits` where the text references an annual cadence. Use this to compute a dollar-aware benefit score that replaces the count-based component when data is available.

**File:** `backend/logic/pipeline.py`

```python
import re as _re

def _annual_benefit_value(product: models.CardProduct | None) -> float:
    if not product or not product.card_benefits:
        return 0.0
    total = 0.0
    raw = product.card_benefits
    items = raw if isinstance(raw, list) else []
    for item in items:
        text = (
            " ".join(str(v or "") for v in item.values())
            if isinstance(item, dict)
            else str(item)
        )
        low = text.lower()
        # Only count benefits that mention annual/calendar cadence
        if not any(t in low for t in ("annual", "per year", "each year", "cardmember year", "calendar year")):
            continue
        # Skip monthly/quarterly sub-amounts that happen to appear in annual benefit text
        if any(t in low for t in ("per month", "monthly", "each month", "quarterly")):
            continue
        for m in _re.finditer(r"\$\s*([0-9][0-9,]*(?:\.\d+)?)", text):
            try:
                total += float(m.group(1).replace(",", ""))
            except ValueError:
                continue
    return total
```

In `_held_value_signal`, replace the benefit count score with dollar-value score when available:
```python
benefit_value = _annual_benefit_value(product)
if benefit_value > 0:
    score += min(benefit_value / 8, 36)  # $8 annual benefit = 1 pt, capped at 36
    drivers.append(f"~${benefit_value:.0f} annual benefit value")
elif benefits:
    score += min(benefits * 12, 36)
    drivers.append(f"{benefits} benefit(s)")
```

### Test

- Card with "$300 airline credit annual" → benefit_value=300, adds 36 pts (capped)
- Card with three misc benefits but no dollar amounts → count-based fallback
- Card with "$10 per month" benefit text → excluded from annual extraction

---

## Change 5 — Expand Re-Eligibility Windows

### Problem

`bonus_eligible_again` handles only Amex (never) and Chase Sapphire (48 months). All other issuers return `False, None` — blocking re-queue recommendations that should eventually unlock.

Missing rules:
- Barclays: 24 months between same-card bonuses (approximate; varies by card)
- Citi: 24 months from last bonus earned on the same card
- Capital One Venture Business: 48 months (already in `eligibility()` but not in `bonus_eligible_again`)
- Airline cobrands (United, Southwest, JetBlue, Alaska, Hawaiian, Wyndham): generally 24 months

### Fix

Add issuer helpers and extend `bonus_eligible_again` with these rules.

**File:** `backend/logic/eligibility.py`

New constants:
```python
BARCLAYS_REELIGIBILITY_MONTHS = 24
CITI_REELIGIBILITY_MONTHS = 24
AIRLINE_COBRAND_REELIGIBILITY_MONTHS = 24
CAPONE_VENTURE_BUSINESS_REELIGIBILITY_MONTHS = 48
```

New helper:
```python
def _is_barclays(issuer: str) -> bool:
    return "barclays" in (issuer or "").lower()

def _is_airline_cobrand(product_name: str) -> bool:
    low = (product_name or "").lower()
    return any(term in low for term in (
        "united", "southwest", "jetblue", "alaska", "hawaiian", "wyndham", "british airways"
    ))
```

Extended `bonus_eligible_again`:
```python
def bonus_eligible_again(held, as_of=None):
    today = as_of or dt.date.today()
    if not held.welcome_bonus_earned:
        return True, None
    if _is_amex(held.issuer):
        return False, None
    if _is_chase(held.issuer) and "sapphire" in (held.product_name or "").lower():
        if held.bonus_earned_date:
            again = add_months(held.bonus_earned_date, SAPPHIRE_MONTHS)
            return (again <= today), again
    if _is_barclays(held.issuer):
        if held.bonus_earned_date:
            again = add_months(held.bonus_earned_date, BARCLAYS_REELIGIBILITY_MONTHS)
            return (again <= today), again
    if _is_citi(held.issuer):
        if held.bonus_earned_date:
            again = add_months(held.bonus_earned_date, CITI_REELIGIBILITY_MONTHS)
            return (again <= today), again
    if _is_capone(held.issuer) and "venture" in (held.product_name or "").lower() and "business" in (held.product_name or "").lower():
        if held.bonus_earned_date:
            again = add_months(held.bonus_earned_date, CAPONE_VENTURE_BUSINESS_REELIGIBILITY_MONTHS)
            return (again <= today), again
    if _is_airline_cobrand(held.product_name):
        if held.bonus_earned_date:
            again = add_months(held.bonus_earned_date, AIRLINE_COBRAND_REELIGIBILITY_MONTHS)
            return (again <= today), again
    return False, None
```

### Test

- Barclays card with bonus_earned_date 25 months ago → `True, date`
- Citi card with bonus earned 20 months ago → `False, date (4 months out)`
- Airline cobrand 24 months ago → `True, date`
- Capital One Venture Business 47 months ago → `False, date (1 month out)`

---

## Change 6 — Expand Family Referral Detection

### Problem

`REFERRAL_FAMILY_KEYS` only covers Capital One Venture and Venture Business. Amex Gold / Business Gold referrals and Chase Ink cross-card referrals are not detected, so the household view misses synergy opportunities.

Missing referral families:
- Amex Gold ↔ Amex Business Gold (same MR ecosystem, cross-product referrals supported)
- Chase Ink Preferred / Cash / Unlimited / Premier (any Ink holder can refer to another Ink applicant)

### Fix

Add a `REFERRAL_SUPER_FAMILIES` dict that maps multiple family keys to the same referral group string. This enables cross-variant detection (e.g., Ink Cash holder → Ink Preferred applicant) without changing the matching algorithm's fundamental structure.

**File:** `backend/logic/household.py`

```python
REFERRAL_FAMILY_KEYS = {
    ("capital_one", "capital_one_venture"),
    ("capital_one", "capital_one_venture_business"),
    ("american_express", "amex_gold"),
    ("american_express", "amex_business_gold"),
    ("chase", "chase_ink_preferred"),
    ("chase", "chase_ink_cash"),
    ("chase", "chase_ink_unlimited"),
    ("chase", "chase_ink_premier"),
}

REFERRAL_SUPER_FAMILIES: dict[tuple, str] = {
    ("chase", "chase_ink_preferred"): "chase_ink",
    ("chase", "chase_ink_cash"): "chase_ink",
    ("chase", "chase_ink_unlimited"): "chase_ink",
    ("chase", "chase_ink_premier"): "chase_ink",
    ("american_express", "amex_gold"): "amex_mr_gold",
    ("american_express", "amex_business_gold"): "amex_mr_gold",
}
```

In `_held_referral_keys`, after adding the family key:
```python
super_family = REFERRAL_SUPER_FAMILIES.get(product_family)
if super_family:
    keys.add(("super_family", super_family))
```

Same in `_candidate_referral_keys`:
```python
super_family = REFERRAL_SUPER_FAMILIES.get(candidate_family)
if super_family:
    keys.add(("super_family", super_family))
```

When a match is made via super_family, set `family_route=True` and note the super-family name in the route string.

### Test

- User A holds Ink Cash, User B applies for Ink Preferred → referral detected via super_family=chase_ink
- User A holds Amex Gold, User B applies for Amex Business Gold → referral detected via super_family=amex_mr_gold
- Non-related cards → no super_family match

---

## Rollback

All changes are additive or behavioral. No schema changes, no new columns, no data migration.

To rollback any individual change: `git revert` the relevant commit, or manually revert the specific function to its prior form (captured in git diff).

---

## Verification

```bash
cd "C:\Users\Davin\Desktop\Coding Projects\WEwards"
.venv-win\Scripts\python.exe -m pytest backend/tests/ -q
```

All 112 existing tests must continue to pass. New behavioral changes are verified by reasoning through the sort key math or reading the pipeline output — no new test files are added in this pass (V3 will add targeted tests for eligibility windows).

---

## What Is Not In Scope

- Frontend changes
- New API endpoints
- New DB columns
- Score threshold tuning (handled via config.py env vars)
- Seats.aero / redemption pipeline
- Any change outside `backend/logic/` and `backend/config.py`
