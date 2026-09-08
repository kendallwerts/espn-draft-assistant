import json
import os
import sys
import time
from typing import Any

import requests

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

    print("=" * 78)
    print(f"WEEK {week} FANTASY REPORT")
    print(f"Team: {team.get('name') or team.get('nickname') or TEAM_ID}")
    print("=" * 78)

    recommended = recommend_lineup(roster)
    print("\nRECOMMENDED LINEUP (ESPN projected points)")
    print(f"{'SLOT':<6} {'PLAYER':<28} {'POS':<4} {'PROJ':>5}  STATUS")
    print("-" * 62)
    for slot, player in recommended:
        print(f"{slot:<6} {player['name'][:28]:<28} {player['position']:<4} {fmt_points(player['projected'])}  {player['injury']}")

    current_by_id = {p["id"]: p["slot"] for p in roster if p["slot"] not in ("BENCH", "IR")}
    changes = []
    for slot, player in recommended:
        current_slot = current_by_id.get(player["id"])
        if current_slot is None:
            changes.append(f"START {player['name']} at {slot} (currently bench/IR)")
        elif current_slot != slot and not ({current_slot, slot} <= {"RB", "WR", "FLEX"}):
            changes.append(f"Move {player['name']} from {current_slot} to {slot}")

    print("\nLINEUP CHANGES")
    if changes:
        for change in changes:
            print(f"  - {change}")
    else:
        print("  No obvious changes based on ESPN projections.")

    watch = [p for p in roster if p["injury"] not in ("ACTIVE", "NORMAL")]
    print("\nINJURY WATCH")
    if watch:
        for p in sorted(watch, key=lambda x: x["name"]):
            print(f"  - {p['name']} ({p['position']}): {p['injury']}")
    else:
        print("  No roster injury flags.")

    free_agents = [p for p in pool if p.get("on_team_id") in (0, None, -1) and p["projected"] is not None]
    free_agents.sort(key=score_key, reverse=True)
    bench = [p for p in roster if p["slot"] == "BENCH"]

    print("\nWAIVER / FREE-AGENT UPGRADES")
    suggestions = []
    for pos in ("RB", "WR", "TE", "QB"):
        fa = next((p for p in free_agents if p["position"] == pos), None)
        bench_pos = sorted([p for p in bench if p["position"] == pos and p["projected"] is not None], key=score_key)
        if fa and bench_pos:
            weakest = bench_pos[0]
            gain = float(fa["projected"]) - float(weakest["projected"])
            if gain >= 1.0:
                suggestions.append((gain, fa, weakest))
    suggestions.sort(reverse=True, key=lambda item: item[0])
    if suggestions:
        for gain, fa, weakest in suggestions[:5]:
            print(f"  +{gain:4.1f}: {fa['name']} ({fa['position']}, {fa['projected']:.1f}) over {weakest['name']} ({weakest['projected']:.1f})")
    else:
        print("  No >1.0 projected-point bench upgrades found.")

    roster_dst = next((p for p in roster if p["position"] == "D/ST"), None)
    best_dst = next((p for p in free_agents if p["position"] == "D/ST"), None)
    print("\nD/ST STREAMING")
    if roster_dst and best_dst and roster_dst["projected"] is not None:
        gain = float(best_dst["projected"]) - float(roster_dst["projected"])
        if gain >= 1.0:
            print(f"  Consider {best_dst['name']} ({best_dst['projected']:.1f}) over {roster_dst['name']} ({roster_dst['projected']:.1f}); +{gain:.1f} projected.")
        else:
            print(f"  Keep {roster_dst['name']} for now; best available is only {best_dst['name']} ({best_dst['projected']:.1f}).")
    else:
        print("  Not enough ESPN projection data to compare defenses.")

    missing = sum(1 for p in roster if p["projected"] is None)
    if missing:
        print(f"\nNOTE: ESPN did not return a Week {week} projection for {missing} rostered player(s); those players are shown with — and should be reviewed manually.")


if __name__ == "__main__":
    main()
