# DATA_MODEL — entities + conventions

## Conventions (do not break)

- **cpp is cents-per-point** → dollars = `points * cpp / 100`. Used identically in
  `scoring.py`, `routers/profiles.py`, `logic/household.py`. (Don't reintroduce the 100× bug.)
- **Effective values** follow a scraped + manual-override pattern:
  - current offer: `current_offer_effective = current_offer_override ?? current_offer_points`
    (a fresh public scrape clears the override).
  - referral: `referral_bonus_effective = referral_bonus_override ?? referral_bonus_points`.
- **Peak is durable history**: raise freely; a **decrease** must go to review. Public peak and
  targeted peak (`targeted_peak_*`) are separate.
- **Peak is a timing signal, not the value ranker** (see `DECISION_RULES.md`).
- **Raw product names stay raw** for provenance and matching. User-facing surfaces should
  use the computed canonical reference from `backend/product_identity.py`
  (`canonical_key` + short `display_name`) so "American Express® Gold Card" renders as
  "Amex Gold" without losing the sourced legal name.
- **PUBLIC vs PRIVATE firewall**: ingestion writes PUBLIC tables only and must not import or
  read PRIVATE models. PRIVATE figures (last4, credit_limit, balances, targeted offers) are
  **encrypted at rest** via `backend/crypto.py` type decorators (`EncryptedString/Int/Float/
  JSON`).
- **Additive DB migrations**: SQLite `create_all` won't add columns to existing tables.
  Register every new column in `backend/db.py::_ADDED_COLUMNS` (runs on startup against
  `data/churn.db`).

## PUBLIC tables (ingestion-managed)

- **CardProduct** — catalog identity + offers: `current_offer_*` (+ `_override`/`_effective`),
  `peak_offer_*`, `targeted_peak_offer_*`, `referral_bonus_*` (points/override/effective/cash),
  `annual_fee`, `first_year_credit_value`, `earn_multipliers`, `best_category_uses`,
  `card_benefits`, `downgrade_paths`, `eligibility_tags`, `tag`, `currency`, provenance
  (`source_url`, `last_verified`), `updated_at`. UniqueConstraint on (issuer, product_name).
- **Valuation** — `cpp_scraped` / `cpp_override` → `cpp_effective` per currency.
- **SourceConfig** — DB-managed ingestion sources (active/priority/kind).
- **ProposedChange** — review queue. **IngestionEvidence** — per-field provenance.
- **TransferPartner**, **AwardBenchmark** — (redemption layer; partially used).

## PRIVATE tables (encrypted; never sent to LLM)

- **HeldCard** — a user's card: `date_opened` (drives eligibility), `annual_fee`,
  `renewal_date`, bonus history, `min_spend_*` (requirement/deadline/progress/completed),
  encrypted `last4`/`credit_limit`/`my_targeted_offer_points`, `status`, `updated_at`.
- **BenefitUsage** — PRIVATE per-user tracking for sourced card benefits/credits:
  `held_card_id`, `benefit_key`, current `period_key`, available amount, encrypted
  `amount_used`/`notes`, plus `suppressed` for user-level benefit pause/ignore
  preferences. Public benefit definitions still come from `CardProduct.card_benefits`
  with source provenance; usage and suppression preferences stay private. A
  `period_key` of `__all__` stores a persistent preference across recurring
  monthly/semiannual periods.
- **UserProfile** — encrypted `point_balances` (`{currency: balance}`), notes.
- **ManualTargetedOffer** — per-user targeted offer for any product (held or not), encrypted,
  with `expires_at`.
- **TargetRedemption** — trip goals (program, points needed) for the redemption layer.

## Status vocabulary

`APPLY NOW · WATCH · WAIT · LOW PRIORITY · NEEDS DATA · SKIP · FUTURE` (see `DECISION_RULES.md`).

## Card reference registry

`CardReference` is a PUBLIC identity/source-hint table for known cards. It stores
`canonical_key`, short `display_name`, aliases/search terms, expected currency/type, issuer
domain, manually pinned URLs, and learned verified source URLs. It must not store offer
amounts, peaks, fees, or benefits; those still require sourced ingestion evidence on
`CardProduct` / `IngestionEvidence`.

## Current implementation notes

- `CardProduct` now tracks both `last_web_search_at` for offer/peak research and
  `last_supplemental_search_at` for benefit/multiplier research cooldowns.
- `ProposedChange` stores durable quality metadata: `reason_code`, `review_note`,
  `risk_level`, and `quality_score`.
- Manual catalog creation and watchlist seeding should upsert by canonical product variant,
  not exact scraped name only.
- Duplicate same-variant `CardProduct` rows can be merged through the catalog cleanup path:
  the most complete row becomes canonical, missing public facts are merged in, dependent
  rows are repointed, and duplicate product rows are removed.
