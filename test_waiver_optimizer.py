import unittest
from waiver_optimizer import best_lineup, rank_claims


def player(id, name, pos, pts, slot="BENCH", injury="ACTIVE", on_team_id=0):
    return dict(id=id, name=name, position=pos, projected=pts,
                slot=slot, injury=injury, on_team_id=on_team_id,
                owned=20, owned_change=0)


class WaiverOptimizerTests(unittest.TestCase):
    def setUp(self):
        self.roster = [
            player(1, "QB", "QB", 15, "QB"),
            player(2, "RB1", "RB", 15, "RB"),
            player(3, "RB2", "RB", 10, "RB"),
            player(4, "WR1", "WR", 14, "WR"),
            player(5, "WR2", "WR", 12, "WR"),
            player(6, "TE", "TE", 8, "TE"),
            player(7, "Flex", "WR", 9, "FLEX"),
            player(8, "Steelers", "D/ST", 5, "D/ST"),
            player(9, "Giants", "D/ST", 8, "BENCH"),
            player(10, "K", "K", 7, "K"),
            player(11, "Drop WR", "WR", 2),
            player(12, "Injured Star", "RB", 30, "IR", "OUT"),
        ]

    def test_uses_bench_defense_and_ignores_ir(self):
        lineup = best_lineup(self.roster)
        names = [p["name"] for p in lineup["starters"]]
        self.assertIn("Giants", names)
        self.assertNotIn("Injured Star", names)
        self.assertEqual(next(p["slot"] for p in lineup["starters"] if p["name"] == "Giants"), "D/ST")

    def test_gain_measures_lineup_not_raw_projection(self):
        pool = [player(13, "New RB", "RB", 13)]
        result = rank_claims(self.roster, pool)
        claim = next(c for c in result["claims"] if c["add"] == "New RB" and c["drop"] == "Drop WR")
        self.assertEqual(claim["lineup_gain"], 3)

    def test_no_automatic_defense_or_injured_drops(self):
        pool = [player(14, "Great RB", "RB", 25)]
        result = rank_claims(self.roster, pool)
        self.assertFalse(any(c["drop"] in ("Steelers", "Giants", "Injured Star") for c in result["claims"]))

    def test_distinct_incoming_players_and_safe_drops(self):
        pool = [
            player(13, "QB A", "QB", 18),
            player(14, "QB B", "QB", 16),
        ]
        result = rank_claims(self.roster, pool)
        self.assertEqual([c["add"] for c in result["claims"]], ["QB A", "QB B"])
        self.assertTrue(all(c["drop"] == "Drop WR" for c in result["claims"]))
        self.assertFalse(any(c["drop"] in ("Breece Hall", "Jordan Addison", "Rico Dowdle")
                             for c in result["claims"]))

    def test_no_safe_drop_means_no_claim(self):
        roster = [p for p in self.roster if p["name"] != "Drop WR"]
        result = rank_claims(roster, [player(13, "QB A", "QB", 18)])
        self.assertEqual(result["claims"], [])


if __name__ == "__main__":
    unittest.main()
