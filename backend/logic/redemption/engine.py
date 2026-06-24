"""Household redemption progress and accumulation guidance."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import config, models
from ...crypto import MissingKeyError
from .. import catalog as catalog_logic
from .providers import AwardProvider, default_provider


def _balances_for(db: Session, user: str) -> dict[str, float]:
    profile = db.get(models.UserProfile, user)
    if not profile:
        return {}
    try:
        return dict(profile.point_balances or {})
    except MissingKeyError:
        return {}


def _combined_balances(db: Session) -> dict[str, float]:
    out: dict[str, float] = {}
    for user in config.USERS:
        for currency, balance in _balances_for(db, user).items():
            out[currency] = out.get(currency, 0.0) + (balance or 0.0)
    return out


def _ratio_value(ratio: str | None) -> float:
    if not ratio:
        return 1.0
    if ":" in ratio:
        left, right = ratio.split(":", 1)
        try:
            return float(right.strip()) / float(left.strip())
        except (TypeError, ValueError, ZeroDivisionError):
            return 1.0
    try:
        return float(ratio)
    except ValueError:
        return 1.0


def _programs(value: str | None) -> list[str]:
    return [p.strip() for p in (value or "").split(",") if p.strip()]


def _target_to_dict(target: models.TargetRedemption) -> dict:
    return {
        "id": target.id,
        "user": target.user,
        "name": target.name,
        "origin": target.origin,
        "destination": target.destination,
        "region": target.region,
        "cabin_or_tier": target.cabin_or_tier,
        "preferred_programs": target.preferred_programs,
        "est_cost_points": target.est_cost_points,
        "target_value_cash": target.target_value_cash,
        "buy_points_cpp": target.buy_points_cpp,
        "travel_start_date": target.travel_start_date.isoformat() if target.travel_start_date else None,
        "travel_end_date": target.travel_end_date.isoformat() if target.travel_end_date else None,
        "passenger_count": target.passenger_count,
        "frequency": target.frequency,
        "priority": target.priority,
        "notes": target.notes,
    }


def _progress_for_target(
    db: Session,
    target: models.TargetRedemption,
    balances: dict[str, float],
    partners: list[models.TransferPartner],
    provider: AwardProvider,
) -> dict:
    result = provider.estimate_cost(db, target)
    programs = _programs(target.preferred_programs) or ([result.program] if result.program else [])
    needed = result.points_cost or target.est_cost_points or 0

    program_options: list[dict] = []
    for program in programs:
        direct: list[dict] = []
        transfers: list[dict] = []
        program_available = 0.0
        direct_balance = sum(
            amount for currency, amount in balances.items()
            if currency.strip().lower() == program.strip().lower()
        )
        if direct_balance:
            direct.append({"program": program, "points": round(direct_balance)})
            program_available += direct_balance
        seen_transfer_sources: set[tuple[str, str, str]] = set()
        for partner in partners:
            if partner.to_program.strip().lower() != program.strip().lower():
                continue
            transfer_key = (
                partner.from_currency.strip().lower(),
                partner.to_program.strip().lower(),
                (partner.ratio or "1:1").strip().lower(),
            )
            if transfer_key in seen_transfer_sources:
                continue
            seen_transfer_sources.add(transfer_key)
            balance = sum(
                amount for currency, amount in balances.items()
                if currency.strip().lower() == partner.from_currency.strip().lower()
            )
            if not balance:
                continue
            ratio = _ratio_value(partner.ratio)
            converted = balance * ratio
            transfers.append(
                {
                    "from_currency": partner.from_currency,
                    "to_program": partner.to_program,
                    "ratio": partner.ratio or "1:1",
                    "source_points": round(balance),
                    "converted_points": round(converted),
                }
            )
            program_available += converted
        program_options.append(
            {
                "program": program,
                "available": program_available,
                "direct": direct,
                "transfers": transfers,
            }
        )

    if program_options:
        best_option = max(
            program_options,
            key=lambda option: (
                (option["available"] / needed) if needed else option["available"],
                option["available"],
            ),
        )
    else:
        best_option = {"program": None, "available": 0.0, "direct": [], "transfers": []}

    selected_program = best_option["program"]
    direct = best_option["direct"]
    transfers = best_option["transfers"]
    total_available = best_option["available"]

    progress_pct = round((total_available / needed) * 100, 1) if needed else 0.0
    more_needed = max(0, round(needed - total_available)) if needed else None
    realized_cpp = (
        round((target.target_value_cash or 0) / needed * 100.0, 3)
        if needed and target.target_value_cash
        else None
    )
    buy_points_worth_it = (
        bool(target.buy_points_cpp and realized_cpp and target.buy_points_cpp < realized_cpp)
    )

    best_transfer = transfers[0] if transfers else None
    if more_needed and transfers:
        closing = next((t for t in transfers if t["converted_points"] >= more_needed), transfers[0])
        transfer_text = (
            f"transfer {closing['from_currency']} {closing['ratio']} to close the gap"
            if closing
            else None
        )
    else:
        transfer_text = None

    passenger_count = target.passenger_count or 1
    route = " to ".join([p for p in [target.origin, target.destination or target.region] if p])
    trip_label = f"{target.name}{f' ({route})' if route else ''}"
    household_label = "+".join(config.USERS)
    guidance = (
        f"{household_label} have {round(total_available):,} usable points"
        f"{f' via {selected_program}' if selected_program else ''} toward {trip_label}; "
        f"{progress_pct}% to the {needed:,} point target for {passenger_count} traveler"
        f"{'' if passenger_count == 1 else 's'}"
        if needed
        else f"{target.name} needs a points estimate before progress can be computed"
    )
    if transfer_text:
        guidance += f"; {transfer_text}"
    guidance += f"; award data: {result.mode}."

    return {
        "target": _target_to_dict(target),
        "programs": programs,
        "selected_program": selected_program,
        "needed_points": needed,
        "available_points": round(total_available),
        "progress_pct": min(progress_pct, 999.0),
        "more_points_needed": more_needed,
        "direct_balances": direct,
        "transfer_options": transfers,
        "best_transfer": best_transfer,
        "award_result": result.__dict__,
        "realized_cpp": realized_cpp,
        "buy_points_cpp": target.buy_points_cpp,
        "buy_points_worth_it": buy_points_worth_it,
        "guidance": guidance,
    }


def build_redemption_plan(db: Session, provider: AwardProvider | None = None) -> dict:
    provider = provider or default_provider()
    balances = _combined_balances(db)
    vmap = catalog_logic.valuation_map(db)
    balance_rows = [
        {
            "currency": currency,
            "balance": balance,
            "cpp": vmap.get(currency.strip().lower()),
            "value": round(balance * (vmap.get(currency.strip().lower(), 0.0)) / 100.0, 2),
        }
        for currency, balance in sorted(balances.items())
    ]
    targets = list(db.scalars(select(models.TargetRedemption)).all())
    targets.sort(key=lambda t: (t.priority if t.priority is not None else 999, t.id))
    partners = list(db.scalars(select(models.TransferPartner)).all())
    progress = [_progress_for_target(db, t, balances, partners, provider) for t in targets]
    best_uses = sorted(progress, key=lambda p: (p["realized_cpp"] or 0, p["progress_pct"]), reverse=True)
    return {
        "mode": provider.mode,
        "live_enabled": provider.mode == "live",
        "note": "Live seats.aero availability enabled; booking remains manual."
        if provider.mode == "live"
        else "Benchmark estimates active. Add SEATS_AERO_API_KEY for live award availability; booking remains manual.",
        "balances": balance_rows,
        "targets": [_target_to_dict(t) for t in targets],
        "transfer_partners": [_partner_to_dict(p) for p in partners],
        "progress": progress,
        "best_uses": best_uses,
    }


def _partner_to_dict(partner: models.TransferPartner) -> dict:
    return {
        "id": partner.id,
        "from_currency": partner.from_currency,
        "to_program": partner.to_program,
        "ratio": partner.ratio,
        "source_url": partner.source_url,
        "last_verified": partner.last_verified.isoformat() if partner.last_verified else None,
    }
