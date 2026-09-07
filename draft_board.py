import argparse
import json
import os
import time
from typing import Any

import requests

SEASON = int(os.getenv("ESPN_SEASON", "2026"))
LEAGUE_ID = os.getenv("ESPN_LEAGUE_ID")
ESPN_S2 = os.getenv("ESPN_S2")
SWID = os.getenv("ESPN_SWID")
TEAM_ID = int(os.getenv("ESPN_TEAM_ID", "13"))

POSITION_NAMES = {
    1: "QB",
    2: "RB",
    3: "WR",
    4: "TE",
    5: "K",
    16: "D/ST",
}

if not LEAGUE_ID:
    raise SystemExit("Missing ESPN_LEAGUE_ID")

LEAGUE_URL = (
    f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/"
    f"seasons/{SEASON}/segments/0/leagues/{LEAGUE_ID}"
)


def auth_cookies() -> dict[str, str] | None:
    if ESPN_S2 and SWID:
        return {"espn_s2": ESPN_S2, "SWID": SWID}
    return None


def get_json(url: str, *, params=None, headers=None) -> Any:
    response = requests.get(
        url,
        params=params,
        headers=headers or {"Accept": "application/json", "User-Agent": "espn-draft-assistant/0.2"},
        cookies=auth_cookies(),
        timeout=20,
    )
    if response.status_code in (401, 403):
        raise SystemExit("ESPN authentication failed. Refresh ESPN_S2 / ESPN_SWID secrets.")
    response.raise_for_status()
    return response.json()


def fetch_league() -> dict[str, Any]:
    return get_json(
        LEAGUE_URL,
        params=[("view", "mDraftDetail"), ("view", "mTeam"), ("view", "mSettings")],
    )


def fetch_player_pool(limit: int = 500) -> list[dict[str, Any]]:
    # kona_player_info is useful because it returns player identity, ownership/ADP,
    # availability status, and ESPN projections/rank metadata in league context.
    fantasy_filter = {
        "players": {
            "filterStatus": {"value": ["FREEAGENT", "WAIVERS", "ONTEAM"]},
            "filterRanksForScoringPeriodIds": {"value": [1]},
            "limit": limit,
            "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
        }
    }
    headers = {
        "Accept": "application/json",
        "User-Agent": "espn-draft-assistant/0.2",
        "X-Fantasy-Filter": json.dumps(fantasy_filter, separators=(",", ":")),
    }
    data = get_json(
        LEAGUE_URL,
        params={"view": "kona_player_info", "scoringPeriodId": 1},
        headers=headers,
    )
    return data.get("players", [])


def team_name(team: dict[str, Any]) -> str:
    if team.get("name"):
        return team["name"]
    return f"{team.get('location', '')} {team.get('nickname', '')}".strip() or f"Team {team.get('id', '?')}"


def normalize_player(entry: dict[str, Any]) -> dict[str, Any]:
    player = entry.get("player") or {}
    pool = entry.get("playerPoolEntry") or {}
    ownership = pool.get("ownership") or player.get("ownership") or {}

    adp = ownership.get("averageDraftPosition")
    if adp in (None, 0):
        adp = 9999.0

    return {
        "id": entry.get("id") or player.get("id"),
        "name": player.get("fullName") or f"Player {entry.get('id', '?')}",
        "position": POSITION_NAMES.get(player.get("defaultPositionId"), str(player.get("defaultPositionId", "?"))),
        "pro_team_id": player.get("proTeamId"),
        "injury": player.get("injuryStatus", "ACTIVE"),
        "on_team_id": entry.get("onTeamId", 0),
        "adp": float(adp),
        "percent_owned": float(ownership.get("percentOwned") or pool.get("percentOwned") or 0),
    }


def current_state() -> dict[str, Any]:
    league = fetch_league()
    pool_entries = fetch_player_pool()
    players = [normalize_player(e) for e in pool_entries]
    by_id = {p["id"]: p for p in players if p.get("id") is not None}

    teams = {t["id"]: team_name(t) for t in league.get("teams", [])}
    draft = league.get("draftDetail") or {}
    picks = sorted(draft.get("picks") or [], key=lambda p: p.get("overallPickNumber", 99999))

    drafted_ids = {
        p.get("playerId")
        for p in picks
        if p.get("playerId") not in (None, -1, 0)
    }

    # ESPN's onTeamId can lag/behave differently around the live draft, so the
    # draft pick list is our source of truth for whether a player is available.
    available = [p for p in players if p["id"] not in drafted_ids]
    available.sort(key=lambda p: (p["adp"], -p["percent_owned"], p["name"]))

    next_open = next((p for p in picks if p.get("playerId") in (None, -1, 0)), None)
    my_open_picks = [
        p for p in picks
        if p.get("teamId") == TEAM_ID and p.get("playerId") in (None, -1, 0)
    ]

    return {
        "league": league,
        "teams": teams,
        "draft": draft,
        "picks": picks,
        "by_id": by_id,
        "drafted_ids": drafted_ids,
        "available": available,
        "next_open": next_open,
        "my_open_picks": my_open_picks,
    }


def print_board(state: dict[str, Any], top_n: int = 40) -> None:
    draft = state["draft"]
    teams = state["teams"]
    picks = state["picks"]
    by_id = state["by_id"]

    print("=" * 88)
    print(f"Dockware draft board | season {SEASON} | league {LEAGUE_ID}")
    print(f"Draft in progress: {draft.get('inProgress')} | complete: {draft.get('drafted')}")
    print(f"Players loaded: {len(state['available']) + len(state['drafted_ids'])} | drafted: {len(state['drafted_ids'])}")

    next_open = state["next_open"]
    if next_open:
        tid = next_open.get("teamId")
        print(
            f"On the clock / next pick: #{next_open.get('overallPickNumber')} "
            f"— {teams.get(tid, f'Team {tid}')}"
        )

    if state["my_open_picks"]:
        mine = state["my_open_picks"][:4]
        print("Kendall's next picks: " + ", ".join(f"#{p.get('overallPickNumber')}" for p in mine))

    selected = [p for p in picks if p.get("playerId") not in (None, -1, 0)]
    if selected:
        print("\nMost recent picks:")
        for pick in selected[-10:]:
            player = by_id.get(pick.get("playerId"), {})
            tid = pick.get("teamId")
            print(
                f"  #{pick.get('overallPickNumber'):>3}  "
                f"{player.get('name', f\"playerId={pick.get('playerId')}\")} "
                f"{player.get('position', ''):<4}  → {teams.get(tid, f'Team {tid}')}"
            )

    print(f"\nTop {top_n} available by ESPN ADP:")
    print(f"{'ADP':>6}  {'POS':<4} {'PLAYER':<30} {'INJURY':<13} {'OWN%':>6}")
    print("-" * 70)
    for player in state["available"][:top_n]:
        adp = "—" if player["adp"] >= 9999 else f"{player['adp']:.1f}"
        print(
            f"{adp:>6}  {player['position']:<4} {player['name'][:30]:<30} "
            f"{player['injury'][:13]:<13} {player['percent_owned']:>5.1f}%"
        )


def run_once(top_n: int) -> None:
    print_board(current_state(), top_n=top_n)


def watch(interval: int, top_n: int) -> None:
    previous_drafted: set[int] = set()
    while True:
        try:
            state = current_state()
            drafted = set(state["drafted_ids"])
            new = drafted - previous_drafted
            if new or not previous_drafted:
                # ANSI clear screen; harmless if redirected to a file.
                print("\033[2J\033[H", end="")
                print_board(state, top_n=top_n)
            previous_drafted = drafted
            if state["draft"].get("drafted"):
                print("\nDraft is complete.")
                return
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nStopped.")
            return
        except requests.RequestException as exc:
            print(f"ESPN request failed: {exc}; retrying in {interval}s")
            time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Live ESPN fantasy draft board")
    parser.add_argument("--watch", action="store_true", help="Poll ESPN continuously")
    parser.add_argument("--interval", type=int, default=5, help="Polling interval in seconds")
    parser.add_argument("--top", type=int, default=40, help="Number of available players to show")
    args = parser.parse_args()

    if args.watch:
        watch(max(args.interval, 2), args.top)
    else:
        run_once(args.top)


if __name__ == "__main__":
    main()
