# ROADMAP - built vs not, and what's next

## Built (working)

- **State capture**: held cards, dates, balances, bonus history, renewals; 5/24 + value.
- **Eligibility engine**: 5/24, Amex once-per-lifetime / velocity / 5-card, Sapphire 48mo,
  Chase Ink 90-day, Citi spacing, Capital One sensitivity, closed-to-new (tag-driven).
- **Scoring + per-user pipeline**: peak_score + offer_value + status; held-aware; excludes
  duplicates; explainable reasons; value-first gates for APPLY NOW / WATCH; rare huge
  offers flagged without letting small near-peak offers dominate.
- **Household synergy**: both users compared; **referrals both directions**; merged ranked
  moves; combined balances/value.
- **Category engine**: household-wide "what to use where" guidance for dining, travel,
  groceries, gas, and everyday spend from actual held cards, earn multipliers, and cpp.
- **Benefits ledger + usage tracker foundation**: active household cards surface verified
  catalog benefits plus NEEDS DATA gaps for cards without benefit data; Profiles track
  per-user usage by current benefit period so credits can be used before they expire, and
  Dashboard surfaces benefit alerts for expiring credits, upcoming refreshes, and anniversary
  bonuses on held cards.
- **Catalog health**: Card Universe has a read-only data-quality queue showing missing
  current offers, public peaks, currencies, valuations, benefits, multipliers, sources,
  stale sources, pending review, duplicate identities, and held-card data gaps.
- **Catalog cleanup**: Card Universe can reject obvious bad proposed changes and merge
  duplicate same-variant product rows while preserving useful public facts and dependent
  local links.
- **Redemption foundation**: household balances mapped to `TargetRedemption` goals,
  route/date/passenger context, `TransferPartner` routing, buy-points math, and a provider
  layer that runs benchmark estimates by default or seats.aero live availability when
  `SEATS_AERO_API_KEY` is set.
- **Ingestion**: cached concurrent HTTP, deterministic + batched-LLM extraction, targeted
  cached research resolver for stale/incomplete cards, source adapters before generic LLM,
  optional Crawl4AI rendered fallback for unresolved known public URLs, **cited web-search
  fallback**, delta-gated commits with provenance, targeted-vs-public peak split, peak
  monotonicity, non-offer-source guard, quality gates for noisy proposals, and separate
  supplemental cooldowns for benefit/multiplier gaps.
- **Card reference registry**: seeded known-card identities, short names, aliases,
  product-aware currencies, issuer domains, manually pinned URLs, and learned verified URLs
  so refresh starts from stable references instead of rediscovering every card.
- **Hardening**: encrypted PRIVATE fields, additive migrations, unique constraints,
  `updated_at`, `ManualTargetedOffer`, `SourceConfig`.

## Next layers (ordered)

1. **Research resolver refinement**: expand source adapters and fixture coverage as more
   issuers/products are added; keep search cached, cooldown-gated, and promoted into static
   sources when a reliable cited URL resolves.
2. **Benefits ledger - next pass**: improve parsed benefit structure, realized value rollups,
   and retention impact; tie to downgrade/cancel decisions.
3. **Redemption strategy - next pass**: add live seats.aero contract tests, optional live
   result caching policy controls, and an eventual feedback path from realized redemption
   cpp back into card scoring. Booking stays manual.

## Keep-compatible notes

- Preserve `earn_multipliers`, `best_category_uses`, `card_benefits`, `downgrade_paths`,
  `TransferPartner`, `AwardBenchmark`, `TargetRedemption` - later layers depend on them.
- Don't regress: N+1 avoidance (preloaded held), httpx keep-alive + batched cache writes,
  commit batching, encryption, web-search-as-cached-last-resort.
