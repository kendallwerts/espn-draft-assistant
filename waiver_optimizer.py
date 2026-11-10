"""Conservative, transparent fantasy lineup and waiver recommendations."""
from __future__ import annotations

from typing import Any

STARTERS = (("QB", 1), ("RB", 2), ("WR", 2), ("TE", 1), ("D/ST", 1), ("K", 1))
UNAVAILABLE = {"OUT", "IR", "INJURY_RESERVE", "SUSPENSION", "SUSPENDED"}
POSITIONS = {"QB", "RB", "WR", "TE", "D/ST", "K"}


def eligible(player: dict[str, Any]) -> bool:
    return (player.get("position") in POSITIONS
            and str(player.get("injury", "ACTIVE")).upper() not in UNAVAILABLE
            and isinstance(player.get("projected"), (int, float))
            and player.get("projected") is not None
            and not player.get("bye", False))


def best_lineup(roster: list[dict[str, Any]]) -> dict[str, Any]:
    """Return optimized projected starters, never activating current IR occupants."""
    remaining = [p for p in roster if eligible(p) and p.get("slot") != "IR"]
    chosen: list[dict[str, Any]] = []
    missing: list[str] = []

    def take(position: str, count: int) -> None:
        choices = sorted((p for p in remaining if p["position"] == position),
                         key=lambda p: float(p["projected"]), reverse=True)
        for player in choices[:count]:
            chosen.append({**player, "slot": position})
            remaining.remove(player)
        missing.extend([position] * (count - min(count, len(choices))))

    for pos, count in STARTERS:
        take(pos, count)
    flex = sorted((p for p in remaining if p["position"] in ("RB", "WR", "TE")),
                  key=lambda p: float(p["projected"]), reverse=True)
    if flex:
        chosen.append({**flex[0], "slot": "FLEX"})
    else:
        missing.append("FLEX")
    return {
        "starters": chosen,
        "projected_points": round(sum(float(p["projected"]) for p in chosen), 2),
        "unfilled_slots": missing,
    }


def safe_drops(roster: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Conservative shortlist; no claim is an automatic transaction.

    Weekly projections cannot establish season-long player value. Protect
    productive bench players, all injured/IR players, defenses, and scarce RBs.
    """
    healthy_rbs = sum(
        p.get("position") == "RB"
        and p.get("slot") != "IR"
        and str(p.get("injury", "ACTIVE")).upper() not in UNAVAILABLE
        for p in roster
    )
    result = []
    for p in roster:
        if p.get("slot") != "BENCH" or p.get("position") == "D/ST":
            continue
        if str(p.get("injury", "ACTIVE")).upper() not in ("ACTIVE", "NORMAL"):
            continue
        if p.get("position") == "RB" and healthy_rbs <= 5:
            continue
        projected = p.get("projected")
        if projected is None or float(projected) >= 8.0:
            continue
        result.append(p)
    return sorted(result, key=lambda p: float(p["projected"]))


def rank_claims(roster: list[dict[str, Any]],
                pool: list[dict[str, Any]],
                *, limit: int = 12) -> dict[str, Any]:
    """Rank distinct incoming players, with a safe drop and backup drop options.

    Claim-success probabilities require data not supplied by ESPN's pool.
    The demand indicator is descriptive, not a calibrated probability.
    """
    baseline = best_lineup(roster)
    owned_ids = {p.get("id") for p in roster}
    available = [
        p for p in pool
        if p.get("id") not in owned_ids
        and p.get("on_team_id") in (0, -1, None)
        and eligible(p)
    ]
    drops = safe_drops(roster)
    candidates = []
    for incoming in available:
        options = []
        for outgoing in drops:
            changed = [p for p in roster if p.get("id") != outgoing.get("id")] + [
                {**incoming, "slot": "BENCH"}
            ]
            optimized = best_lineup(changed)
            gain = round(optimized["projected_points"] - baseline["projected_points"], 2)
            if gain <= 0:
                continue
            options.append((gain, outgoing, optimized))
        if not options:
            continue
        options.sort(key=lambda item: (-item[0], float(item[1]["projected"])))
        gain, outgoing, optimized = options[0]
        demand = round(min(100, max(0, float(incoming.get("owned") or 0)
                                   + 2 * max(0, float(incoming.get("owned_change") or 0)))), 1)
        candidates.append({
            "add": incoming["name"], "add_id": incoming.get("id"),
            "position": incoming["position"],
            "drop": outgoing["name"], "drop_id": outgoing.get("id"),
            "backup_drops": [
                {"name": other["name"], "id": other.get("id"), "lineup_gain": other_gain}
                for other_gain, other, _ in options[1:4]
            ],
            "lineup_gain": gain,
            "new_projected_points": optimized["projected_points"],
            "demand_indicator": demand,
            "new_lineup": optimized["starters"],
            "caution": "Manual review required: weekly projections do not measure season-long value.",
        })
    candidates.sort(key=lambda c: (-c["lineup_gain"], c["demand_indicator"]))
    return {
        "current_lineup": baseline,
        "claims": candidates[:limit],
        "notes": [
            "Each ranked claim targets a different incoming player; backup drops are alternatives.",
            "Weekly waiver priority resets; no future priority is banked.",
            "Demand indicator is a heuristic, not a claim-success probability.",
            "Protect IR, injured players, defenses, productive bench players and thin RB depth.",
            "If no safe drop exists, show no claim rather than recommend sacrificing a valuable player.",
            "No verified rest-of-season value or opponent waiver intent is available.",
        ],
    }
