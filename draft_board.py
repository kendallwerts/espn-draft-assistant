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

POSITION_NAMES = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}

if not LEAGUE_ID:
    raise SystemExit("Missing ESPN_LEAGUE_ID")

LEAGUE_URL = (
    f"https://fantasy.espn.com/apis/v3/games/ffl/"
    f"seasons/{SEASON}/segments/0/leagues/{LEAGUE_ID}"
)


def cookies() -> dict[str, str] | None:
    if ESPN_S2 and SWID:
        return {"espn_s2": ESPN_S2, "SWID": SWID}
    return None


def get_json(url: str, *, params=None, headers=None) -> Any:
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "espn-draft-assistant/0.3",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if headers:
        request_headers.update(headers)

    if params is None:
        params = {}
    elif isinstance(params, list):
        params = list(params)
    else:
        params = dict(params)

    cache_buster = ("_ts", str(int(time.time() * 1000)))
    if isinstance(params, list):
        params.append(cache_buster)
    else:
        params[cache_buster[0]] = cache_buster[1]

    response = requests.get(
        url,
        params=params,
        headers=request_headers,
        cookies=cookies(),
        timeout=20,
    )
    if response.status_code in (401, 403):
        raise SystemExit("ESPN authentication failed. Refresh ESPN_S2 / ESPN_SWID.")
    response.raise_for_status()
    return response.json()


def fetch_league() -> dict[str, Any]:
    return get_json(
        LEAGUE_URL,
        params=[
            ("view", "mDraftDetail"),
            ("view", "mTeam"),
            ("view", "mSettings"),
        ],
    )


def fetch_player_pool(limit: int = 500) -> list[dict[str, Any]]:
    fantasy_filter = {
        "players": {
            "filterStatus": {"value": ["FREEAGENT", "WAIVERS", "ONTEAM"]},
            "filterRanksForScoringPeriodIds": {"value": [1]},
            "limit": limit,
            "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
        }
    }
    data = get_json(
        LEAGUE_URL,
        params={"view": "kona_player_info", "scoringPeriodId": 1},
        headers={
            "X-Fantasy-Filter": json.dumps(fantasy_filter, separators=(",", ":"))
        },
    )
    return data.get("players", [])


def team_name(team: dict[str, Any]) -> str:
    if team.get("name"):
        return team["name"]
    location = team.get("location", "")
    nickname = team.get("nickname", "")
    return f"{location} {nickname}".strip() or f"Team {team.get('id', '?')}"


def normalize_player(entry: dict[str, Any]) -> dict[str, Any]:
    player = entry.get("player") or {}
    pool = entry.get("playerPoolEntry") or {}
    ownership = pool.get("ownership") or player.get("ownership") or {}
    adp = ownership.get("averageDraftPosition") or 9999.0

    return {
        "id": entry.get("id") or player.get("id"),
        "name": player.get("fullName") or f"Player {entry.get('id', '?')}",
        "position": POSITION_NAMES.get(
            player.get("defaultPositionId"),
            str(player.get("defaultPositionId", "?")),
        ),
        "injury": player.get("injuryStatus", "ACTIVE"),
        "on_team_id": entry.get("onTeamId") or pool.get("onTeamId") or 0,
        "adp": float(adp),
        "percent_owned": float(
            ownership.get("percentOwned") or pool.get("percentOwned") or 0
        ),
    }


def current_state() -> dict[str, Any]:
    league = fetch_league()
    players = [normalize_player(entry) for entry in fetch_player_pool()]
    by_id = {player["id"]: player for player in players if player.get("id")}
    teams = {team["id"]: team_name(team) for team in league.get("teams", [])}
    draft = league.get("draftDetail") or {}
    picks = sorted(
        draft.get("picks") or [],
        key=lambda pick: pick.get("overallPickNumber", 99999),
    )

    drafted_ids = {
        pick.get("playerId")
        for pick in picks
        if pick.get("playerId") not in (None, -1, 0)
    }
    rostered_ids = {
        player["id"]
        for player in players
        if player.get("id") and player.get("on_team_id") not in (None, 0, -1)
    }
    unavailable_ids = drafted_ids | rostered_ids

    available = [player for player in players if player["id"] not in unavailable_ids]
    available.sort(
        key=lambda player: (
            player["adp"],
            -player["percent_owned"],
            player["name"],
        )
    )

    next_open = next(
        (pick for pick in picks if pick.get("playerId") in (None, -1, 0)),
        None,
    )
    my_open_picks = [
        pick
        for pick in picks
        if pick.get("teamId") == TEAM_ID
        and pick.get("playerId") in (None, -1, 0)
    ]

    return {
        "teams": teams,
        "draft": draft,
        "picks": picks,
        "by_id": by_id,
        "drafted_ids": drafted_ids,
        "rostered_ids": rostered_ids,
        "unavailable_ids": unavailable_ids,
        "available": available,
        "next_open": next_open,
        "my_open_picks": my_open_picks,
    }


def print_board(state: dict[str, Any], top_n: int) -> None:
    draft = state["draft"]
    teams = state["teams"]
    print("=" * 88)
    print(f"Dockware draft board | season {SEASON}")
    print(
        f"Draft in progress: {draft.get('inProgress')} | "
        f"complete: {draft.get('drafted')} | "
        f"draft picks seen: {len(state['drafted_ids'])} | "
        f"rostered players seen: {len(state['rostered_ids'])}"
    )

    next_open = state["next_open"]
    if next_open:
        team_id = next_open.get("teamId")
        team = teams.get(team_id, f"Team {team_id}")
        print(f"Next pick: #{next_open.get('overallPickNumber')} — {team}")

    if state["my_open_picks"]:
        picks = state["my_open_picks"][:4]
        print(
            "Kendall's next picks: "
            + ", ".join(f"#{pick.get('overallPickNumber')}" for pick in picks)
        )

    selected = [
        pick
        for pick in state["picks"]
        if pick.get("playerId") not in (None, -1, 0)
    ]
    if selected:
        print("\nMost recent picks:")
        for pick in selected[-10:]:
            player = state["by_id"].get(pick.get("playerId"), {})
            player_name = player.get("name") or f"playerId={pick.get('playerId')}"
            position = player.get("position", "")
            team_id = pick.get("teamId")
            team = teams.get(team_id, f"Team {team_id}")
            print(
                f"  #{pick.get('overallPickNumber'):>3}  "
                f"{player_name:<30} {position:<4} → {team}"
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


def watch(interval: int, top_n: int) -> None:
    previous_signature: tuple[frozenset[int], frozenset[int]] | None = None
    while True:
        try:
            state = current_state()
            signature = (
                frozenset(state["drafted_ids"]),
                frozenset(state["rostered_ids"]),
            )
            if signature != previous_signature:
                print("\033[2J\033[H", end="")
                print_board(state, top_n)
                previous_signature = signature
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
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=5)
    parser.add_argument("--top", type=int, default=40)
    args = parser.parse_args()

    if args.watch:
        watch(max(args.interval, 2), args.top)
    else:
        print_board(current_state(), args.top)


if __name__ == "__main__":
    main()
