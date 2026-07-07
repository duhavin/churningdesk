"""Household redemption progress and accumulation guidance."""
from __future__ import annotations

import datetime as dt

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


def _bonus_state(partner: models.TransferPartner, today: dt.date | None = None) -> tuple[float, bool]:
    """(effective_ratio, bonus_active). A transfer bonus applies on top of the
    base ratio until its end date passes; expired bonuses are ignored without
    any cleanup."""
    today = today or dt.date.today()
    base = _ratio_value(partner.ratio)
    pct = partner.bonus_pct or 0.0
    active = bool(pct > 0 and (partner.bonus_end_date is None or partner.bonus_end_date >= today))
    return (base * (1.0 + pct / 100.0) if active else base), active


def _program_availability(
    program: str,
    balances: dict[str, float],
    partners: list[models.TransferPartner],
    today: dt.date | None = None,
) -> dict:
    """Direct balance + every transfer route into ``program`` (bonus-aware)."""
    direct: list[dict] = []
    transfers: list[dict] = []
    available = 0.0
    direct_balance = sum(
        amount for currency, amount in balances.items()
        if currency.strip().lower() == program.strip().lower()
    )
    if direct_balance:
        direct.append({"program": program, "points": round(direct_balance)})
        available += direct_balance
    seen: set[tuple[str, str, str]] = set()
    for partner in partners:
        if partner.to_program.strip().lower() != program.strip().lower():
            continue
        key = (
            partner.from_currency.strip().lower(),
            partner.to_program.strip().lower(),
            (partner.ratio or "1:1").strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        balance = sum(
            amount for currency, amount in balances.items()
            if currency.strip().lower() == partner.from_currency.strip().lower()
        )
        if not balance:
            continue
        effective_ratio, bonus_active = _bonus_state(partner, today)
        converted = balance * effective_ratio
        transfers.append(
            {
                "from_currency": partner.from_currency,
                "to_program": partner.to_program,
                "ratio": partner.ratio or "1:1",
                "effective_ratio": round(effective_ratio, 4),
                "bonus_pct": partner.bonus_pct if bonus_active else None,
                "bonus_end_date": partner.bonus_end_date.isoformat()
                if (bonus_active and partner.bonus_end_date)
                else None,
                "bonus_active": bonus_active,
                "source_points": round(balance),
                "converted_points": round(converted),
            }
        )
        available += converted
    # Bonused routes first, then by yield — the best conversion leads.
    transfers.sort(key=lambda tr: (not tr["bonus_active"], -tr["converted_points"]))
    return {"program": program, "available": available, "direct": direct, "transfers": transfers}


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


def _dates_hint(target: models.TargetRedemption) -> str | None:
    start = target.travel_start_date.isoformat() if target.travel_start_date else None
    end = target.travel_end_date.isoformat() if target.travel_end_date else None
    if start and end:
        return f"{start}..{end}"
    return start or end


def _progress_for_target(
    db: Session,
    target: models.TargetRedemption,
    balances: dict[str, float],
    partners: list[models.TransferPartner],
    provider: AwardProvider,
    valuations: dict[str, float] | None = None,
) -> dict:
    result = provider.estimate_cost(db, target)
    programs = _programs(target.preferred_programs) or ([result.program] if result.program else [])
    needed = result.points_cost or target.est_cost_points or 0

    program_options: list[dict] = [
        _program_availability(program, balances, partners) for program in programs
    ]

    # Every decent award option, not just the single best estimate: ask the
    # provider per preferred program (and unrestricted when none are set),
    # dedupe, and mark which options the household can already cover.
    search_programs: list[str | None] = list(programs) or [None]
    if None not in search_programs and len(search_programs) < 4:
        search_programs.append(None)  # also surface out-of-preference deals
    raw_options: list = []
    for search_program in search_programs:
        try:
            raw_options.extend(
                provider.search_award_space(
                    db,
                    origin=target.origin,
                    region=target.destination or target.region,
                    program=search_program,
                    cabin=target.cabin_or_tier,
                    dates=_dates_hint(target),
                )
            )
        except Exception:
            continue  # a failed search never blocks progress math
    availability_cache: dict[str, float] = {
        option["program"].strip().lower(): option["available"] for option in program_options
    }
    award_options: list[dict] = []
    seen_options: set[tuple] = set()
    for option in raw_options:
        key = (
            (option.program or "").strip().lower(),
            (option.cabin or "").strip().lower(),
            option.points_cost,
        )
        if key in seen_options or not option.program:
            continue
        seen_options.add(key)
        program_key = option.program.strip().lower()
        if program_key not in availability_cache:
            availability_cache[program_key] = _program_availability(
                option.program, balances, partners
            )["available"]
        covered = bool(
            option.points_cost and availability_cache[program_key] >= option.points_cost
        )
        award_options.append(
            {
                "program": option.program,
                "cabin": option.cabin,
                "points_cost": option.points_cost,
                "source": option.source,
                "freshness": option.freshness,
                "mode": option.mode,
                "note": option.note,
                "covered_by_household": covered,
            }
        )
    award_options.sort(key=lambda o: (o["points_cost"] is None, o["points_cost"] or 0))
    award_options = award_options[:8]

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

    # Value verdict: is this redemption a GOOD use of the points, measured
    # against the baseline valuation of the currency actually being spent
    # (the transfer source when a transfer is needed, else the program held)?
    spend_currency = None
    if transfers:
        spend_currency = transfers[0]["from_currency"]
    elif direct:
        spend_currency = direct[0]["program"]
    elif selected_program:
        spend_currency = selected_program
    baseline_cpp = None
    if valuations and spend_currency:
        baseline_cpp = valuations.get(spend_currency.strip().lower())
    value_verdict = None
    verdict_note = None
    if realized_cpp is not None and baseline_cpp:
        ratio_vs_baseline = realized_cpp / baseline_cpp
        if ratio_vs_baseline >= 1.25:
            value_verdict = "excellent"
            verdict_note = f"{realized_cpp:.2f} cpp vs {baseline_cpp:.2f} baseline — outsized value; book it."
        elif ratio_vs_baseline >= 1.0:
            value_verdict = "good"
            verdict_note = f"{realized_cpp:.2f} cpp vs {baseline_cpp:.2f} baseline — solid use of points."
        elif ratio_vs_baseline >= 0.8:
            value_verdict = "fair"
            verdict_note = (
                f"{realized_cpp:.2f} cpp is below the {baseline_cpp:.2f} baseline — "
                "compare cash price or another program first."
            )
        else:
            value_verdict = "poor"
            verdict_note = (
                f"{realized_cpp:.2f} cpp burns points well below the {baseline_cpp:.2f} baseline — "
                "pay cash or pick a better award."
            )

    best_transfer = transfers[0] if transfers else None
    if more_needed and transfers:
        closing = next((t for t in transfers if t["converted_points"] >= more_needed), transfers[0])
        if closing:
            bonus_note = (
                f" (+{closing['bonus_pct']:g}% bonus"
                + (f" through {closing['bonus_end_date']}" if closing.get("bonus_end_date") else "")
                + ")"
                if closing.get("bonus_active") and closing.get("bonus_pct")
                else ""
            )
            transfer_text = f"transfer {closing['from_currency']} {closing['ratio']}{bonus_note} to close the gap"
        else:
            transfer_text = None
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
        "award_options": award_options,
        "realized_cpp": realized_cpp,
        "baseline_cpp": baseline_cpp,
        "value_verdict": value_verdict,
        "verdict_note": verdict_note,
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
    progress = [
        _progress_for_target(db, t, balances, partners, provider, valuations=vmap)
        for t in targets
    ]
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
        "bonus_pct": partner.bonus_pct,
        "bonus_end_date": partner.bonus_end_date.isoformat() if partner.bonus_end_date else None,
        "bonus_active": _bonus_state(partner)[1],
        "effective_ratio": round(_bonus_state(partner)[0], 4),
        "source_url": partner.source_url,
        "last_verified": partner.last_verified.isoformat() if partner.last_verified else None,
    }
