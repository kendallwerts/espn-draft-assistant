import os
import sys
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

SLOT_NAMES = {
    0: "QB",
    2: "RB",
    4: "WR",
    6: "TE",
    16: "D/ST",
    17: "K",
    20: "BENCH",
    21: "IR",
    23: "FLEX",
}

POSITION_NAMES = {
    1: "QB",
    2: "RB",
    3: "WR",
    4: "TE",
    5: "K",
    16: "D/ST",
}


def cookies() -> dict[str, str] | None:
    if ESPN_S2 and SWID:
        return {"espn_s2": ESPN_S2, "SWID": SWID}
    return None


def fetch_league() -> dict[str, Any]:
    response = requests.get(
        URL,
        params=[
            ("view", "mTeam"),
            ("view", "mRoster"),
            ("view", "mSettings"),
        ],
        cookies=cookies(),
        headers={
            "Accept": "application/json",
            "User-Agent": "espn-draft-assistant/0.4",
        },
        timeout=20,
    )
    if response.status_code in (401, 403):
        sys.exit("ESPN authentication failed. Refresh ESPN_S2 / ESPN_SWID.")
    response.raise_for_status()
    return response.json()


def team_name(team: dict[str, Any]) -> str:
    if team.get("name"):
        return team["name"]
    return (
        f"{team.get('location', '')} {team.get('nickname', '')}".strip()
        or f"Team {team.get('id', '?')}"
    )


def player_from_entry(entry: dict[str, Any]) -> dict[str, Any]:
    pool = entry.get("playerPoolEntry") or {}
    player = pool.get("player") or entry.get("player") or {}
    return {
        "id": player.get("id") or pool.get("id"),
        "name": player.get("fullName") or player.get("name") or "Unknown player",
        "position": POSITION_NAMES.get(
            player.get("defaultPositionId"),
            str(player.get("defaultPositionId", "?")),
        ),
        "slot": SLOT_NAMES.get(entry.get("lineupSlotId"), str(entry.get("lineupSlotId", "?"))),
        "injury": player.get("injuryStatus") or "ACTIVE",
        "pro_team_id": player.get("proTeamId"),
    }


def main() -> None:
    data = fetch_league()
    teams = data.get("teams", [])
    team = next((t for t in teams if t.get("id") == TEAM_ID), None)
    if not team:
        sys.exit(f"Team {TEAM_ID} not found. Returned team IDs: {[t.get('id') for t in teams]}")

    roster = (team.get("roster") or {}).get("entries") or []
    players = [player_from_entry(entry) for entry in roster]

    print(f"League: {data.get('settings', {}).get('name', LEAGUE_ID)}")
    print(f"Team: {team_name(team)} (team {TEAM_ID})")
    print(f"Rostered players: {len(players)}")
    print()

    starters = [p for p in players if p["slot"] not in ("BENCH", "IR")]
    bench = [p for p in players if p["slot"] == "BENCH"]
    ir = [p for p in players if p["slot"] == "IR"]

    print("STARTERS")
    for p in starters:
        print(f"  {p['slot']:<5} {p['name']:<28} {p['position']:<4} {p['injury']}")

    print("\nBENCH")
    for p in bench:
        print(f"  {p['name']:<34} {p['position']:<4} {p['injury']}")

    if ir:
        print("\nIR")
        for p in ir:
            print(f"  {p['name']:<34} {p['position']:<4} {p['injury']}")

    counts: dict[str, int] = {}
    for p in players:
        counts[p["position"]] = counts.get(p["position"], 0) + 1
    print("\nPOSITION COUNTS")
    for pos in ("QB", "RB", "WR", "TE", "D/ST", "K"):
        print(f"  {pos:<4} {counts.get(pos, 0)}")


if __name__ == "__main__":
    main()
