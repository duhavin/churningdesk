"""PUBLIC seed: standard transfer-partner routes for the household's
transferable currencies (Amex Membership Rewards, Chase Ultimate Rewards,
Capital One miles).

These are stable, public 1:1-class facts sourced from the issuers' own
transfer-partner pages (source_url). Seeding runs ONLY when the
transfer_partner table is empty — user edits are never overwritten, and
`last_verified` stays NULL until a human or refresh confirms a row, so the
UI stays honest about verification state. Bonus percentages are NEVER
seeded: promos are time-limited and enter via the bonus research flow or
manual entry.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models

_MR_URL = "https://global.americanexpress.com/rewards/transfer-partners"
_UR_URL = "https://www.chase.com/personal/credit-cards/ultimate-rewards/transfer-partners"
_C1_URL = "https://www.capitalone.com/credit-cards/benefits/miles-transfer/"

# (from_currency, to_program, ratio, source_url)
SEED_TRANSFER_PARTNERS: list[tuple[str, str, str, str]] = [
    # --- Amex Membership Rewards ---
    ("Membership Rewards", "Aer Lingus AerClub (Avios)", "1:1", _MR_URL),
    ("Membership Rewards", "Air Canada Aeroplan", "1:1", _MR_URL),
    ("Membership Rewards", "Air France/KLM Flying Blue", "1:1", _MR_URL),
    ("Membership Rewards", "ANA Mileage Club", "1:1", _MR_URL),
    ("Membership Rewards", "Avianca LifeMiles", "1:1", _MR_URL),
    ("Membership Rewards", "British Airways Avios", "1:1", _MR_URL),
    ("Membership Rewards", "Cathay Pacific Asia Miles", "1:1", _MR_URL),
    ("Membership Rewards", "Delta SkyMiles", "1:1", _MR_URL),
    ("Membership Rewards", "Emirates Skywards", "1:1", _MR_URL),
    ("Membership Rewards", "Etihad Guest", "1:1", _MR_URL),
    ("Membership Rewards", "Iberia Avios", "1:1", _MR_URL),
    ("Membership Rewards", "JetBlue TrueBlue", "1:0.8", _MR_URL),
    ("Membership Rewards", "Qantas Frequent Flyer", "1:1", _MR_URL),
    ("Membership Rewards", "Qatar Airways Avios", "1:1", _MR_URL),
    ("Membership Rewards", "Singapore KrisFlyer", "1:1", _MR_URL),
    ("Membership Rewards", "Virgin Atlantic Flying Club", "1:1", _MR_URL),
    ("Membership Rewards", "Choice Privileges", "1:1", _MR_URL),
    ("Membership Rewards", "Hilton Honors", "1:2", _MR_URL),
    ("Membership Rewards", "Marriott Bonvoy", "1:1", _MR_URL),
    # --- Chase Ultimate Rewards ---
    ("Ultimate Rewards", "Aer Lingus AerClub (Avios)", "1:1", _UR_URL),
    ("Ultimate Rewards", "Air Canada Aeroplan", "1:1", _UR_URL),
    ("Ultimate Rewards", "Air France/KLM Flying Blue", "1:1", _UR_URL),
    ("Ultimate Rewards", "British Airways Avios", "1:1", _UR_URL),
    ("Ultimate Rewards", "Emirates Skywards", "1:1", _UR_URL),
    ("Ultimate Rewards", "Iberia Avios", "1:1", _UR_URL),
    ("Ultimate Rewards", "JetBlue TrueBlue", "1:1", _UR_URL),
    ("Ultimate Rewards", "Singapore KrisFlyer", "1:1", _UR_URL),
    ("Ultimate Rewards", "Southwest Rapid Rewards", "1:1", _UR_URL),
    ("Ultimate Rewards", "United MileagePlus", "1:1", _UR_URL),
    ("Ultimate Rewards", "Virgin Atlantic Flying Club", "1:1", _UR_URL),
    ("Ultimate Rewards", "World of Hyatt", "1:1", _UR_URL),
    ("Ultimate Rewards", "IHG One Rewards", "1:1", _UR_URL),
    ("Ultimate Rewards", "Marriott Bonvoy", "1:1", _UR_URL),
    # --- Capital One miles ---
    ("Capital One Miles", "Aeromexico Rewards", "1:1", _C1_URL),
    ("Capital One Miles", "Air Canada Aeroplan", "1:1", _C1_URL),
    ("Capital One Miles", "Air France/KLM Flying Blue", "1:1", _C1_URL),
    ("Capital One Miles", "Avianca LifeMiles", "1:1", _C1_URL),
    ("Capital One Miles", "British Airways Avios", "1:1", _C1_URL),
    ("Capital One Miles", "Cathay Pacific Asia Miles", "1:1", _C1_URL),
    ("Capital One Miles", "Choice Privileges", "1:1", _C1_URL),
    ("Capital One Miles", "Emirates Skywards", "1:1", _C1_URL),
    ("Capital One Miles", "Etihad Guest", "1:1", _C1_URL),
    ("Capital One Miles", "Finnair Plus", "1:1", _C1_URL),
    ("Capital One Miles", "Qantas Frequent Flyer", "1:1", _C1_URL),
    ("Capital One Miles", "Singapore KrisFlyer", "1:1", _C1_URL),
    ("Capital One Miles", "TAP Miles&Go", "1:1", _C1_URL),
    ("Capital One Miles", "Turkish Miles&Smiles", "1:1", _C1_URL),
    ("Capital One Miles", "Virgin Red", "1:1", _C1_URL),
    ("Capital One Miles", "Wyndham Rewards", "1:1", _C1_URL),
]


def seed_transfer_partners(db: Session) -> int:
    """Seed standard routes ONCE, only into an empty table."""
    existing = db.scalar(select(models.TransferPartner.id).limit(1))
    if existing is not None:
        return 0
    for from_currency, to_program, ratio, source_url in SEED_TRANSFER_PARTNERS:
        db.add(
            models.TransferPartner(
                from_currency=from_currency,
                to_program=to_program,
                ratio=ratio,
                source_url=source_url,
            )
        )
    db.commit()
    return len(SEED_TRANSFER_PARTNERS)
