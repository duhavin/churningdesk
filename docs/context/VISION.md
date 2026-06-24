# VISION — the north star

> A straightforward churning decision-making pipeline for two people. We log the cards we
> hold and our info; the app/agent scrapes to keep card data current and accurate; from
> just our current stack + timeline (open dates, renewals, 5/24, bonus history) it tells us
> the best route forward to optimize and maximize points. The household operates as a team:
> we refer each other to capture referral bonuses on top of welcome bonuses; we maintain the
> right cards for each spend category (dining, travel, groceries, gas, everyday); we
> accumulate valuable transferable travel points; and eventually we know how/when to redeem
> them. The experience should feel streamlined and largely automatic — the thinking is done
> for us, surfaced as clear next actions (apply now / wait / refer / renew / downgrade /
> use-this-card-here), with us just confirming before acting.

## The target experience

- Open the app → see **per-user and household state** at a glance (5/24, held cards,
  annual fees, point balances + value).
- See **one ranked list of the most valuable next moves**, each with the **binding reason**
  (e.g. "Apply now — 100k MR, near peak, Chase-first slot still open" / "Wait — 40% below
  peak" / "Refer User B into Ink — +$X to User A" / "Use the Gold card for groceries").
- Confirm and act. The app did the thinking; we sanity-check and apply.

## What "best route" means

- **Maximize long-run point/benefit accrual per unit of effort and per 5/24 slot** — not
  chasing every small bonus.
- **Prefer large welcome bonuses (≈75k–100k+) from solid, transferable-currency issuers**
  (Amex Membership Rewards, Chase Ultimate Rewards, Capital One Miles) over small offers,
  even if a small offer is technically at 100% of its (tiny) peak.
- Treat a near-peak signal as **timing** ("now is a good time"), not as proof of value.

## Team (household) optimization

- Both users are compared **together**, not in isolation.
- **Referrals both directions**: if one should open a card the other holds, route through
  the holder's referral link to capture the referral bonus *on top of* the welcome bonus.
- **No redundant cards per person**: never recommend a card (or same product-family) a user
  already holds; re-eligible cards return as "requeue". Household overlap can still be
  useful when both people have their own bonus history, credits, lounge access, or separate
  spend patterns.
- **Diversify** currencies and spend categories across the household.

## Streamlined, but verified

- The agent automates collection and the decisions; the **human is the final check** on
  (a) data accuracy and (b) the apply decision — because issuer offers/rules drift and
  targeted offers exist. Streamlined ≠ blind autopilot.

## End state (where this is going)

- **Category guidance**: "use card X for groceries / dining / travel / gas / everyday."
- **Benefits ledger**: the credits/perks we already hold and should use.
- **Redemption strategy**: transfer partners, when to book, when to buy points, and
  progress of household balances toward specific trip goals.

See `DECISION_RULES.md` for the precise scoring/ranking spec and `ROADMAP.md` for sequence.
