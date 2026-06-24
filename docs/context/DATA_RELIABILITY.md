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

- `CardReference` provides stable identity/source hints first: aliases, expected currency,
  issuer domain, pinned URLs, and learned verified URLs. These hints can route refresh to
  the right page faster, but they do not count as evidence for offer/peak/benefit values.
- Deep refresh uses a **research resolver**, not a browser-per-card workflow. For each
  unresolved card it builds focused searches for current public offer, issuer/direct page,
  peak/history, and benefits/multipliers; caches search results; fetches real cited URLs
  through `PageCache`; and runs source adapters before any LLM extraction.
- When static HTTP has a known public product/detail URL but cannot extract usable facts,
  deep refresh can use Crawl4AI as a **bounded rendered fallback**. It renders only already
  known public URLs, never login/CAPTCHA/private pages, and then feeds the rendered page back
  through the same static parser, evidence logging, review queue, targeted/public split, and
  peak monotonicity rules. It is not a browser-per-card default workflow.
- Trusted roundup pages are treated as link hubs, not final benefit sources. If a Doctor of
  Credit / Frequent Miler / US Credit Card Guide / TPG roundup links to a matching
  card-specific review/detail page, including generic anchors like "Read our review" whose
  surrounding row names the card, fetch that detail page and use it for benefits,
  multipliers, usage categories, and other durable facts.
- Source adapters cover issuer pages plus Doctor of Credit, US Credit Card Guide, Frequent
  Miler, and The Points Guy style pages. They extract compact evidence snippets around
  offers, spend, annual fee, peaks, benefits, earn rates, and referrals.
- The LLM only receives compact snippets batched across cards. Full raw pages are not the
  normal extraction payload.
- Run cited web search **only** when a card is **stale AND** unresolved/incomplete after the
  cheap static + LLM passes (i.e. "out of date and hard to find"), and **not more often than
  a cooldown**.
- Benefit/multiplier gaps for priority cards use a separate supplemental cooldown so held
  cards can be prioritized without repeatedly re-searching the same unresolved gap.
- When it resolves a card: **cache the real cited source** on `product.source_url`, log every
  field to `IngestionEvidence`, and (when implemented) **promote** good cited offer URLs to
  `SourceConfig` so future refreshes fetch them statically. `PageCache` then makes repeats
  near-free. One deep pass → cheap forever after.
- Default refreshes do **not** web-search. It's a deliberate "deep refresh."
- Detailed benefit-completeness backfill is prioritized for cards the household actually
  holds. Non-held catalog cards may still retain already-sourced benefits in Card Plan, but
  missing benefit details should not force extra LLM/web work unless a user holds the card.

## Public vs targeted

- Keep **public** current/peak offers separate from **targeted / invite-only / incognito /
  phone / "as high as"** high-water marks (those go in `targeted_peak_*`). Targeted highs are
  benchmarks, never stored as the standard public offer.

## Trust gating

- **Provenance required**: every committed value carries `source_url` + `last_verified`
  (+ snippet/hash/confidence in `IngestionEvidence`).
- **Quality-gating**: obvious parser regressions are rejected before review: generic
  co-brand currency downgrades, raw copied benefit/article fragments, and category maps that
  collapse existing earn/use coverage.
- **Delta-gating**: first sight commits; large offer changes (> `OFFER_DELTA_THRESHOLD`) or
  any eligibility-rule change → `ProposedChange` review queue. Peak is monotonic (raise
  freely; any decrease → review).
- **Never fabricate.** Unknown/unsupported → `NEEDS DATA`. Do not infer offer amounts from
  model memory and present them as fact.
- **Human verification** is the final check for offers/rules that drive the pipeline.
