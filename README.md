# ESPN Draft Assistant

First step for a fantasy-football draft assistant: read the live ESPN draft state and show the draft order / already-selected players.

## 1. Find your league ID

Open your ESPN fantasy league in a browser. The URL normally contains:

```text
leagueId=123456789
```

Put that number in `ESPN_LEAGUE_ID`.

## 2. Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 3. Run against a public league

```bash
export ESPN_LEAGUE_ID=123456789
export ESPN_SEASON=2026
python draft_state.py
```

## 4. If ESPN says the league is private

Do not paste your ESPN session cookies into chat or commit them to git.

In Firefox:
- Log into ESPN.
- Open Developer Tools.
- Open Storage.
- Open Cookies for ESPN/fantasy.espn.com.
- Copy the values of `espn_s2` and `SWID`.

Then set them locally:

```bash
export ESPN_S2='your-long-cookie-value'
export ESPN_SWID='{YOUR-SWID-WITH-BRACES}'
python draft_state.py
```

`espn_s2` is a session credential. Treat it like a password.

## What success looks like

Before the draft, ESPN may return the generated draft slots even when no player has been selected. During the draft, the same `mDraftDetail` data should populate with `playerId` values as picks are made.

This script currently:
- fetches `mDraftDetail`
- fetches the league teams
- prints the first-round order
- prints every returned pick and whether it is still open

Next step: resolve ESPN player IDs to names and poll this endpoint every few seconds so the recommendation engine automatically removes drafted players.
