import json
import os
import sys
import time
from typing import Any

import requests

from waiver_optimizer import rank_claims

SEASON = int(os.getenv("ESPN_SEASON", "2026"))
LEAGUE_ID = os.getenv("ESPN_LEAGUE_ID")
TEAM_ID = int(os.getenv("ESPN_TEAM_ID", "13"))
ESPN_S2 = os.getenv("ESPN_S2")
SWID = os.getenv("ESPN_SWID")

if not LEAGUE_ID:
    sys.exit("Missing ESPN_LEAGUE_ID")

URL = (
    f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/"
    f"seasons/{SEASON}/segments/0/leagues/{LEAGUE_ID}"
)

POSITION_NAMES = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}
SLOT_NAMES = {0: "QB", 2: "RB", 4: "WR", 6: "TE", 16: "D/ST", 17: "K", 20: "BENCH", 21: "IR", 23: "FLEX"}
STARTER_TEMPLATE = [("QB", 1), ("RB", 2), ("WR", 2), ("TE", 1), ("FLEX", 1), ("D/ST", 1), ("K", 1)]

# Bench churn should be conservative: only recommend replacing a rostered
# player when the incoming player has both a meaningful absolute edge and a
# meaningful relative edge. D/ST is intentionally more aggressive because
# streaming defenses by weekly matchup is normal strategy.
WAIVER_MIN_POINTS = 3.0
WAIVER_MIN_RELATIVE_GAIN = 0.20
DST_STREAM_MIN_POINTS = 1.0

# The stash watch is intentionally not a transaction recommendation. It finds
# available skill players who are becoming interesting before they clear the
# conservative "drop someone for him" threshold.
STASH_MIN_PROJECTION = 7.0
STASH_MIN_OWNED = 3.0
STASH_MAX_OWNED = 65.0
STASH_LIMIT = 6


def cookies() -> dict[str, str] | None:
    if ESPN_S2 and SWID:
        return {"espn_s2": ESPN_S2, "SWID": SWID}
    return None


def get_json(*, params=None, headers=None) -> dict[str, Any]:
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 espn-draft-assistant/0.5",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if headers:
        request_headers.update(headers)
    response = requests.get(
        URL,
        params=params,
        headers=request_headers,
        cookies=cookies(),
        timeout=20,
    )
    if response.status_code in (401, 403):
        sys.exit("ESPN authentication failed. Refresh ESPN_S2 / ESPN_SWID.")
    response.raise_for_status()
    try:
        return response.json()
    except requests.exceptions.JSONDecodeError as exc:
        preview = response.text[:250].replace("\n", " ")
        raise RuntimeError(f"ESPN returned non-JSON: {preview!r}") from exc


def fetch_league() -> dict[str, Any]:
    return get_json(params=[("view", "mTeam"), ("view", "mRoster"), ("view", "mSettings"), ("view", "mStatus")])


def current_week(data: dict[str, Any]) -> int:
    status = data.get("status") or {}
    for key in ("currentMatchupPeriod", "scoringPeriodId", "latestScoringPeriod"):
        value = status.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return 1


def fetch_player_pool(week: int, limit: int = 1000) -> list[dict[str, Any]]:
    fantasy_filter = {
        "players": {
            "filterStatus": {"value": ["FREEAGENT", "WAIVERS", "ONTEAM"]},
            "filterRanksForScoringPeriodIds": {"value": [week]},
            "limit": limit,
            "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
        }
    }
    data = get_json(
        params={"view": "kona_player_info", "scoringPeriodId": week, "_ts": str(int(time.time() * 1000))},
        headers={"X-Fantasy-Filter": json.dumps(fantasy_filter, separators=(",", ":"))},
    )
    return data.get("players", [])


def projection(player: dict[str, Any], week: int) -> float | None:
    matches: list[float] = []
    for stat in player.get("stats") or []:
        if stat.get("scoringPeriodId") != week:
            continue
        if stat.get("statSourceId") != 1:
            continue
        total = stat.get("appliedTotal")
        if isinstance(total, (int, float)):
            matches.append(float(total))
    return max(matches) if matches else None


def normalize_pool_entry(entry: dict[str, Any], week: int) -> dict[str, Any]:
    player = entry.get("player") or (entry.get("playerPoolEntry") or {}).get("player") or {}
    pool = entry.get("playerPoolEntry") or {}
    ownership = pool.get("ownership") or player.get("ownership") or {}
    return {
        "id": entry.get("id") or player.get("id"),
        "name": player.get("fullName") or "Unknown player",
        "position": POSITION_NAMES.get(player.get("defaultPositionId"), str(player.get("defaultPositionId", "?"))),
        "injury": player.get("injuryStatus") or "ACTIVE",
        "on_team_id": entry.get("onTeamId") or pool.get("onTeamId") or 0,
        "projected": projection(player, week),
        "owned": float(ownership.get("percentOwned") or 0),
        "owned_change": float(ownership.get("percentChange") or 0),
        "availability_status": entry.get("status") or pool.get("status") or "UNKNOWN",
    }


def normalize_roster_entry(entry: dict[str, Any], pool_by_id: dict[int, dict[str, Any]]) -> dict[str, Any]:
    pool = entry.get("playerPoolEntry") or {}
    player = pool.get("player") or entry.get("player") or {}
    player_id = player.get("id") or pool.get("id")
    base = pool_by_id.get(player_id, {})
    return {
        "id": player_id,
        "name": player.get("fullName") or base.get("name") or "Unknown player",
        "position": POSITION_NAMES.get(player.get("defaultPositionId"), base.get("position", "?")),
        "injury": player.get("injuryStatus") or base.get("injury") or "ACTIVE",
        "slot": SLOT_NAMES.get(entry.get("lineupSlotId"), str(entry.get("lineupSlotId", "?"))),
        "projected": base.get("projected"),
    }


def score_key(player: dict[str, Any]) -> tuple[float, str]:
    value = player.get("projected")
    return (float(value) if isinstance(value, (int, float)) else -9999.0, player["name"])


def recommend_lineup(roster: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    unused = list(roster)
    result: list[tuple[str, dict[str, Any]]] = []

    def take(position: str, count: int) -> None:
        nonlocal unused
        candidates = [p for p in unused if p["position"] == position]
        candidates.sort(key=score_key, reverse=True)
        for player in candidates[:count]:
            result.append((position, player))
            unused.remove(player)

    take("QB", 1)
    take("RB", 2)
    take("WR", 2)
    take("TE", 1)
    flex = [p for p in unused if p["position"] in ("RB", "WR", "TE")]
    flex.sort(key=score_key, reverse=True)
    if flex:
        result.append(("FLEX", flex[0]))
        unused.remove(flex[0])
    take("D/ST", 1)
    take("K", 1)
    return result


def fmt_points(value: float | None) -> str:
    return "   —" if value is None else f"{value:5.1f}"


def meaningful_bench_upgrade(new_points: float, old_points: float) -> bool:
    gain = new_points - old_points
    baseline = max(abs(old_points), 1.0)
    relative_gain = gain / baseline
    return gain >= WAIVER_MIN_POINTS and relative_gain >= WAIVER_MIN_RELATIVE_GAIN


def safe_drop_candidate(player: dict[str, Any]) -> bool:
    """Only compare healthy bench players as potential waiver drops.

    ESPN often sets the current-week projection to zero for injured players.
    Treating that zero as talent/value would create misleading suggestions
    such as dropping a high-value DOUBTFUL player for a short-term fill-in.
    Injured players are surfaced separately in INJURY WATCH.
    """
    return player.get("injury") in ("ACTIVE", "NORMAL")


def main() -> None:
    league = fetch_league()
    week = current_week(league)
    pool = [normalize_pool_entry(entry, week) for entry in fetch_player_pool(week)]
    pool_by_id = {p["id"]: p for p in pool if p.get("id") is not None}

    team = next((t for t in league.get("teams", []) if t.get("id") == TEAM_ID), None)
    if not team:
        sys.exit(f"Team {TEAM_ID} not found")
    roster_entries = (team.get("roster") or {}).get("entries") or []
    roster = [normalize_roster_entry(entry, pool_by_id) for entry in roster_entries]

    optimization = rank_claims(roster, pool)
    snapshot = {
        "season": SEASON, "week": week, "team_id": TEAM_ID,
        "roster": {
            "starters": [p for p in roster if p["slot"] not in ("BENCH", "IR")],
            "bench": [p for p in roster if p["slot"] == "BENCH"],
            "injured_reserve": [p for p in roster if p["slot"] == "IR"],
        },
        "available_players": [p for p in pool if p.get("on_team_id") in (0, -1, None)],
        "optimizer": optimization,
    }
    with open("fantasy_snapshot.json", "w", encoding="utf-8") as output:
        json.dump(snapshot, output, indent=2)
    print("\\nFULL CURRENT ROSTER")
    for label, players in snapshot["roster"].items():
        print(f"  {label.upper()} ({len(players)}):")
        for p in players:
            print(f"    {p['slot']:<6} {p['name']:<28} {p['position']:<4} {fmt_points(p['projected'])} {p['injury']}")
    print("\\nOPTIMIZED CURRENT LINEUP")
    for p in optimization["current_lineup"]["starters"]:
        print(f"  {p['slot']:<6} {p['name']:<28} {fmt_points(p['projected'])}")
    print(f"  TOTAL: {optimization['current_lineup']['projected_points']:.1f}")
    if optimization["current_lineup"]["unfilled_slots"]:
        print(f"  UNFILLED: {optimization['current_lineup']['unfilled_slots']}")
    print("\\nRANKED WAIVER CLAIM ALTERNATIVES (not submitted)")
    for i, claim in enumerate(optimization["claims"], 1):
        print(f"  {i}. ADD {claim['add']} / DROP {claim['drop']}: "
              f"+{claim['lineup_gain']:.1f} lineup pts; "
              f"demand indicator {claim['demand_indicator']:.0f}/100")
    print("  CAUTION: Claims are alternatives; demand is not claim probability.")
    print("  Full roster and free-agent snapshot: fantasy_snapshot.json")

    print("\\nCURRENT VS OPTIMIZED LINEUP CHANGES")
    current_starters = {p["id"]: p for p in roster if p["slot"] not in ("BENCH", "IR")}
    for p in optimization["current_lineup"]["starters"]:
        previous = current_starters.get(p["id"])
        if previous is None:
            print(f"  START {p['name']} at {p['slot']} (currently bench)")
    print("\\nWAIVER CLAIM FALLBACK DETAILS")
    for claim in optimization["claims"]:
        backups = ", ".join(p["name"] for p in claim["backup_drops"]) or "none"
        print(f"  {claim['add']}: drop {claim['drop']}; alternate drops: {backups}")
    for note in optimization["notes"]:
        print(f"  NOTE: {note}")
    if any(p["position"] == "QB" and float(p.get("projected") or 0) == 0
           for p in optimization["current_lineup"]["starters"]):
        print("  WARNING: Starting QB has a zero projection; verify bye/injury and ESPN status.")


if __name__ == "__main__":
    main()
