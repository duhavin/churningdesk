# ROADMAP — built vs not, and what's next

## Built (working)

- **State capture**: held cards, dates, balances, bonus history, renewals; 5/24 + value.
- **Eligibility engine**: 5/24, Amex once-per-lifetime / velocity / 5-card, Sapphire 48mo,
  Chase Ink 90-day, Citi spacing, Capital One sensitivity, closed-to-new (tag-driven).
- **Scoring + per-user pipeline**: peak_score + offer_value + status; held-aware; excludes
  duplicates; explainable reasons.
- **Household synergy**: both users compared; **referrals both directions**; merged ranked
  moves; combined balances/value.
- **Ingestion**: cached concurrent HTTP, deterministic + batched-LLM extraction, **cited
  web-search fallback**, delta-gated commits with provenance, targeted-vs-public peak split,
  peak monotonicity, non-offer-source guard.
- **Hardening**: encrypted PRIVATE fields, additive migrations, unique constraints,
  `updated_at`, `ManualTargetedOffer`, `SourceConfig`.

## Next layers (ordered)

1. **Decision realism (highest priority)** — make ranking **value-first** (not peak-% first);
   add value floors for APPLY NOW; use public peak as the denominator; filter the apply queue
   to actionable statuses (drop NEEDS DATA / LOW PRIORITY into a separate bucket); bias toward
   large transferable bonuses (Amex/Chase/Capital One). Spec: `DECISION_RULES.md`.
2. **Web-search lifecycle** — search once when stale + hard-to-find; cache/promote the
   resolved source; cooldown to avoid re-billing; default refresh stays cheap. Spec:
   `DATA_RELIABILITY.md`.
3. **"What to use where" — category engine** — best held card per spend category (dining,
   travel, groceries, gas, everyday) from `earn_multipliers` × cpp, household-wide. Read-only
   guidance surface.
4. **Benefits ledger** — surface the credits/perks we already hold (`card_benefits`) and
   should be using; tie to retention decisions (`downgrade_paths`).
5. **Redemption strategy** — household balances by currency → `TargetRedemption` goals:
   progress %, transfer-partner routing (`TransferPartner`), "buy points worth it?" math.
   Foundation/scaffold first (no live award API yet — keep it honest per `DATA_RELIABILITY.md`).

## Keep-compatible notes

- Preserve `earn_multipliers`, `best_category_uses`, `card_benefits`, `downgrade_paths`,
  `TransferPartner`, `AwardBenchmark`, `TargetRedemption` — later layers depend on them.
- Don't regress: N+1 avoidance (preloaded held), httpx keep-alive + batched cache writes,
  commit batching, encryption, web-search-as-cached-last-resort.
