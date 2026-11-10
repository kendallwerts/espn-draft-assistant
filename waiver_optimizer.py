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
            chosen.append({"slot": position, **player})
            remaining.remove(player)
        missing.extend([position] * (count - min(count, len(choices))))

    for pos, count in STARTERS:
        take(pos, count)
    flex = sorted((p for p in remaining if p["position"] in ("RB", "WR", "TE")),
                  key=lambda p: float(p["projected"]), reverse=True)
    if flex:
        chosen.append({"slot": "FLEX", **flex[0]})
    else:
        missing.append("FLEX")
    return {
        "starters": chosen,
        "projected_points": round(sum(float(p["projected"]) for p in chosen), 2),
        "unfilled_slots": missing,
    }


def rank_claims(roster: list[dict[str, Any]],
                pool: list[dict[str, Any]],
                *, limit: int = 12) -> dict[str, Any]:
    """Evaluate add/drop pairs and return mutually exclusive ranked alternatives.

    Probabilities are deliberately omitted: waiver order and opponent intent are
    not observable in this input. No transaction is submitted.
    """
    baseline = best_lineup(roster)
    owned_ids = {p.get("id") for p in roster}
    available = [
        p for p in pool
        if p.get("id") not in owned_ids
        and p.get("on_team_id") in (0, -1, None)
        and eligible(p)
    ]
    # Never drop IR, injured players, or a starting player automatically.
    # Defenses are also protected from automatic drops; user can decide to stream.
    drops = [
        p for p in roster
        if p.get("slot") == "BENCH"
        and p.get("position") != "D/ST"
        and str(p.get("injury", "ACTIVE")).upper() in ("ACTIVE", "NORMAL")
    ]
    candidates = []
    for incoming in available:
        for outgoing in drops:
            changed = [p for p in roster if p.get("id") != outgoing.get("id")] + [
                {**incoming, "slot": "BENCH"}
            ]
            optimized = best_lineup(changed)
            gain = round(optimized["projected_points"] - baseline["projected_points"], 2)
            if gain <= 0:
                continue
            # Demand proxy, not an empirical success probability.
            demand = round(min(100, max(0, float(incoming.get("owned") or 0)
                                       + 2 * max(0, float(incoming.get("owned_change") or 0)))), 1)
            candidates.append({
                "add": incoming["name"], "add_id": incoming.get("id"),
                "position": incoming["position"],
                "drop": outgoing["name"], "drop_id": outgoing.get("id"),
                "lineup_gain": gain,
                "new_projected_points": optimized["projected_points"],
                "demand_indicator": demand,
                "new_lineup": optimized["starters"],
                "caution": "Evaluate rest-of-season value before submitting; projection-only model.",
            })
    # Prioritize actual lineup gains; tie-break with lower demand.
    candidates.sort(key=lambda c: (-c["lineup_gain"], c["demand_indicator"]))
    return {
        "current_lineup": baseline,
        "claims": candidates[:limit],
        "notes": [
            "Ranked claims are alternatives, not simultaneously executable transactions.",
            "Weekly resetting priority means there is no future priority to preserve.",
            "Demand indicator is a heuristic, not a probability of claim success.",
            "Only healthy bench non-D/ST players are considered automatic drops.",
            "No projections for future weeks or verified opponents' needs are included.",
        ],
    }
