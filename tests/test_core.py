import datetime as dt
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from ai_probability_adjustment import adjust_probabilities_with_ai_context  # noqa: E402
from kelly_criterion import fractional_kelly, kelly_fraction, kelly_stake  # noqa: E402
from probability_fusion import fuse_wdl_probabilities, normalize_triplet  # noqa: E402
from review_completed_matches import brier, logloss, market_probs_from_odds, top1_hit, top3_hit  # noqa: E402
from worldcup_agent import (  # noqa: E402
    apply_value_metrics,
    build_group_standings,
    build_team_aliases,
    calibrate_wdl_probabilities,
    filter_nearby_rows,
    merge_candidate_rows,
    prioritize_primary_rows,
    result_pick_text,
    resolve_team_name,
)


class CoreMathTests(unittest.TestCase):
    def test_kelly_helpers(self):
        self.assertAlmostEqual(kelly_fraction(0.55, 2.10), 0.1409090909, places=9)
        self.assertEqual(fractional_kelly(0.50, 2.05, fraction=0.25, min_edge=0.05), 0.0)
        self.assertEqual(kelly_stake(0.60, 2.20, bankroll=100.0, max_stake=5.0), 5.0)

    def test_probability_fusion_and_ev(self):
        self.assertEqual(normalize_triplet((-1.0, 2.0, 2.0)), (0.0, 0.5, 0.5))
        fused = fuse_wdl_probabilities((0.6, 0.2, 0.2), (0.3, 0.3, 0.4), model_weight=0.4)
        self.assertAlmostEqual(fused[0], 0.42)
        _market, fused, ev, _kelly_fractions, _kelly_stakes = apply_value_metrics(
            (0.57, 0.24, 0.19),
            [1.40, 3.98, 6.10],
            {"model_weight": 0.4, "kelly_fraction": 0.2, "min_edge": 0.07, "bankroll": 500.0, "min_stake": 2.0, "max_stake_per_pick": 100.0},
        )
        self.assertAlmostEqual(ev[0], fused[0] * 1.40 - 1.0)

    def test_ai_adjustment_raises_draw_when_home_rotates(self):
        probs, meta = adjust_probabilities_with_ai_context(
            (0.55, 0.24, 0.21),
            {"home_rotation_flag": 1, "away_rotation_flag": 0, "home_lineup_known": 1},
            {"enable_ai_probability_adjustment": 1.0, "ai_probability_max_delta": 0.03, "ai_probability_high_confidence_delta": 0.05},
            (0.52, 0.26, 0.22),
        )
        self.assertIsNotNone(probs)
        self.assertTrue(meta["applied"])
        self.assertLess(probs[0], 0.55)
        self.assertGreater(probs[1], 0.24)

    def test_review_metrics(self):
        probs = market_probs_from_odds([2.0, 4.0, 4.0])
        self.assertIsNotNone(probs)
        self.assertAlmostEqual(sum(probs), 1.0)
        self.assertEqual(brier((1.0, 0.0, 0.0), 0), 0.0)
        self.assertAlmostEqual(logloss((0.5, 0.3, 0.2), 0), -math.log(0.5))
        prediction = {"top_scores": [(1, 0, 0.2), (2, 1, 0.1), (0, 0, 0.08)]}
        self.assertTrue(top1_hit(prediction, 1, 0))
        self.assertTrue(top3_hit(prediction, 2, 1))


class ReportFlowTests(unittest.TestCase):
    def test_calibration_and_one_day_window(self):
        calibrated = calibrate_wdl_probabilities((0.70, 0.20, 0.10))
        self.assertAlmostEqual(calibrated[0], 0.62)
        self.assertAlmostEqual(calibrated[1], 0.25)
        self.assertAlmostEqual(calibrated[2], 0.13)
        rows = [
            {"match_time": "2026-06-23 07:59", "home": "A"},
            {"match_time": "2026-06-23 08:01", "home": "B"},
        ]
        filtered = filter_nearby_rows(rows, dt.datetime(2026, 6, 22, 8, 0))
        self.assertEqual([row["home"] for row in filtered], ["A"])

    def test_result_pick_uses_highest_fused_probability(self):
        row = {"probabilities": (0.40, 0.25, 0.35), "fused_probabilities": (0.38, 0.24, 0.38)}
        self.assertTrue(result_pick_text(row).startswith("主胜"))

    def test_qtx_supplement_dedupes_and_sorts_after_primary_rows(self):
        aliases = build_team_aliases({("西班牙", "沙特阿拉伯"): (0.6, 0.2, 0.2)})
        self.assertEqual(resolve_team_name("沙特", aliases), "沙特阿拉伯")
        primary = [{"num": "周日037", "match_time": "2026-06-22 00:00", "home": "西班牙", "away": "沙特", "qtx_match_token": ""}]
        extra = [{"num": "QTX补充", "match_time": "2026-06-22 00:00", "home": "西班牙", "away": "沙特阿拉伯", "qtx_match_token": "token"}]
        rows = merge_candidate_rows(primary, extra)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["qtx_match_token"], "token")
        ordered = prioritize_primary_rows([extra[0], {"num": "周一041", "match_time": "2026-06-23 01:00", "home": "阿根廷", "away": "奥地利"}])
        self.assertEqual([row["num"] for row in ordered], ["周一041", "QTX补充"])

    def test_group_standings_from_finished_matches(self):
        payload = {
            "matches": [
                {"status": "FINISHED", "stage": "GROUP_STAGE", "group": "GROUP_A", "homeTeam": {"name": "Mexico"}, "awayTeam": {"name": "South Africa"}, "score": {"fullTime": {"home": 2, "away": 0}}},
                {"status": "FINISHED", "stage": "GROUP_STAGE", "group": "GROUP_A", "homeTeam": {"name": "Czechia"}, "awayTeam": {"name": "South Korea"}, "score": {"fullTime": {"home": 1, "away": 2}}},
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "matches.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            standings, best_thirds = build_group_standings(path)
        self.assertEqual(standings[0]["team"], "墨西哥")
        self.assertEqual(best_thirds[0]["team"], "捷克")


if __name__ == "__main__":
    unittest.main()
