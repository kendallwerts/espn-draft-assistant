import json
import os
import sys
from typing import Any

import requests

SEASON = int(os.getenv("ESPN_SEASON", "2026"))
LEAGUE_ID = os.getenv("ESPN_LEAGUE_ID")
ESPN_S2 = os.getenv("ESPN_S2")
SWID = os.getenv("ESPN_SWID")

if not LEAGUE_ID:
    sys.exit("Missing ESPN_LEAGUE_ID. Set it in your environment first.")

BASE_URL = (
    f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/"
    f"seasons/{SEASON}/segments/0/leagues/{LEAGUE_ID}"
)


def cookies():
    if ESPN_S2 and SWID:
        return {"espn_s2": ESPN_S2, "SWID": SWID}
    return None


def fetch_league() -> dict[str, Any]:
    params = [
        ("view", "mDraftDetail"),
        ("view", "mTeam"),
        ("view", "mSettings"),
    ]
    response = requests.get(
        BASE_URL,
        params=params,
        cookies=cookies(),
        headers={
            "Accept": "application/json",
            "User-Agent": "espn-draft-mvp/0.1",
        },
        timeout=15,
    )

    if response.status_code in (401, 403):
        sys.exit(
            "ESPN rejected the request. If this is a private league, set "
            "ESPN_S2 and ESPN_SWID from your logged-in ESPN browser session."
        )

    response.raise_for_status()
    return response.json()


def team_name(team: dict[str, Any]) -> str:
    name = team.get("name")
    if name:
        return name
    location = team.get("location", "")
    nickname = team.get("nickname", "")
    combined = f"{location} {nickname}".strip()
    return combined or f"Team {team.get('id', '?')}"


def summarize(data: dict[str, Any]) -> None:
    teams = {t["id"]: team_name(t) for t in data.get("teams", [])}
    draft = data.get("draftDetail") or {}
    picks = draft.get("picks") or []

    print(f"League: {data.get('settings', {}).get('name', LEAGUE_ID)}")
    print(f"Teams currently returned: {len(teams)}")
    print(f"Draft in progress: {draft.get('inProgress')}")
    print(f"Draft complete: {draft.get('drafted')}")
    print()

    if teams:
        print("Teams:")
        for team_id, name in sorted(teams.items()):
            print(f"  {team_id:>2}: {name}")
        print()

    if not picks:
        print("No draft picks were returned yet.")
        print("That can mean the draft order has not been generated yet.")
        print()
        print("Raw draftDetail:")
        print(json.dumps(draft, indent=2))
        return

    picks = sorted(
        picks,
        key=lambda p: (
            p.get("overallPickNumber", 10**9),
            p.get("roundId", 10**9),
            p.get("roundPickNumber", 10**9),
        ),
    )

    print("Draft slots / picks:")
    for p in picks:
        overall = p.get("overallPickNumber")
        rnd = p.get("roundId")
        rnd_pick = p.get("roundPickNumber")
        team_id = p.get("teamId")
        player_id = p.get("playerId")
        selected = player_id not in (None, -1, 0)
        state = f"playerId={player_id}" if selected else "OPEN"
        print(
            f"  #{str(overall):>3}  round {str(rnd):>2}, pick {str(rnd_pick):>2}  "
            f"team {str(team_id):>2} ({teams.get(team_id, 'unknown')})  {state}"
        )

    print()
    print("First-round draft order:")
    first_round = [p for p in picks if p.get("roundId") == 1]
    for p in first_round:
        team_id = p.get("teamId")
        print(
            f"  {p.get('roundPickNumber', '?'):>2}. "
            f"Team {team_id}: {teams.get(team_id, 'unknown')}"
        )


if __name__ == "__main__":
    summarize(fetch_league())
