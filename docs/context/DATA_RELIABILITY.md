# DATA_RELIABILITY — sourcing + trust rules

Wrong data = wrong card path. Data quality is the linchpin of the whole engine.

## Where each value comes from

- **Current offer** (points/cash, min spend, window, annual fee): issuer pages + reputable
  "best current bonus" roundup pages (Doctor of Credit, Frequent Miler, US Credit Card
  Guide, The Points Guy). These are good at *current* offers.
- **All-time PEAK offer**: **NOT** on the roundup pages and never on the issuer page (issuers
  only publish the current offer). Peaks live on **per-card offer-history trackers** and are
  written in messy prose. Get them via a **targeted, cached deep web-search pass** for that
  specific card — not the default roundup sources.
- **Point valuations (cpp)**: published valuation pages — used **only** for cpp, never to
  write offer fields (`validate._is_non_offer_source` guard).

> Why peaks are "hard": issuers don't publish them; trackers are inconsistent, JS-rendered,
> and conflate targeted/incognito highs with public highs. A proper per-card search resolves
> it — the 4 default sources simply don't carry it.

## Web search = cached last resort

- Run cited web search **only** when a card is **stale AND** unresolved/incomplete after the
  cheap static + LLM passes (i.e. "out of date and hard to find"), and **not more often than
  a cooldown**.
- When it resolves a card: **cache the real cited source** on `product.source_url`, log every
  field to `IngestionEvidence`, and (when implemented) **promote** good cited offer URLs to
  `SourceConfig` so future refreshes fetch them statically. `PageCache` then makes repeats
  near-free. One deep pass → cheap forever after.
- Default refreshes do **not** web-search. It's a deliberate "deep refresh."

## Public vs targeted

- Keep **public** current/peak offers separate from **targeted / invite-only / incognito /
  phone / "as high as"** high-water marks (those go in `targeted_peak_*`). Targeted highs are
  benchmarks, never stored as the standard public offer.

## Trust gating

- **Provenance required**: every committed value carries `source_url` + `last_verified`
  (+ snippet/hash/confidence in `IngestionEvidence`).
- **Delta-gating**: first sight commits; large offer changes (> `OFFER_DELTA_THRESHOLD`) or
  any eligibility-rule change → `ProposedChange` review queue. Peak is monotonic (raise
  freely; any decrease → review).
- **Never fabricate.** Unknown/unsupported → `NEEDS DATA`. Do not infer offer amounts from
  model memory and present them as fact.
- **Human verification** is the final check for offers/rules that drive the pipeline.
