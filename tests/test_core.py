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
from review_completed_matches import (  # noqa: E402
    brier,
    logloss,
    market_probs_from_odds,
    score_diagnosis,
    score_metric_summary,
    top1_hit,
    top3_hit,
)
from worldcup_agent import (  # noqa: E402
    asian_handicap_probabilities_from_score_grid,
    asian_handicap_return_units,
    asian_handicap_value_metrics,
    apply_value_metrics,
    build_group_standings,
    build_team_aliases,
    calibrate_wdl_probabilities,
    filter_nearby_rows,
    handicap_probabilities_from_score_prediction,
    merge_candidate_rows,
    parse_asian_handicap_source,
    parse_zgzcw_ypdb_market,
    parse_total_goals_source,
    parse_zgzcw_dxdb_market,
    prioritize_primary_rows,
    result_pick_text,
    resolve_team_name,
    total_goals_probabilities_from_score_grid,
    total_goals_return_units,
    total_goals_value_metrics,
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

    def test_ai_adjustment_lifts_counter_team_against_high_line(self):
        probs, meta = adjust_probabilities_with_ai_context(
            (0.52, 0.24, 0.24),
            {"home_defensive_line": 0.7, "away_coach_style": "counter", "home_lineup_known": 1, "away_lineup_known": 1},
            {"enable_ai_probability_adjustment": 1.0, "ai_probability_max_delta": 0.03, "ai_probability_high_confidence_delta": 0.05},
            (0.58, 0.22, 0.20),
        )
        self.assertIsNotNone(probs)
        self.assertTrue(meta["applied"])
        self.assertGreater(probs[2], 0.24)

    def test_ai_adjustment_uses_value_gap_when_ratio_is_large(self):
        probs, meta = adjust_probabilities_with_ai_context(
            (0.45, 0.27, 0.28),
            {"home_value_ratio": 2.5, "away_value_ratio": 0.4, "home_big5_league_players": 14, "away_big5_league_players": 3, "home_squad_depth_score": 0.8, "away_squad_depth_score": 0.45},
            {"enable_ai_probability_adjustment": 1.0, "ai_probability_max_delta": 0.03, "ai_probability_high_confidence_delta": 0.05, "value_adjustment_weight": 1.0, "value_adjustment_max_delta": 0.025},
        )
        self.assertIsNotNone(probs)
        self.assertTrue(meta["applied_value"])
        self.assertGreater(probs[0], 0.45)

    def test_review_metrics(self):
        probs = market_probs_from_odds([2.0, 4.0, 4.0])
        self.assertIsNotNone(probs)
        self.assertAlmostEqual(sum(probs), 1.0)
        self.assertEqual(brier((1.0, 0.0, 0.0), 0), 0.0)
        self.assertAlmostEqual(logloss((0.5, 0.3, 0.2), 0), -math.log(0.5))
        prediction = {"top_scores": [(1, 0, 0.2), (2, 1, 0.1), (0, 0, 0.08)]}
        self.assertTrue(top1_hit(prediction, 1, 0))
        self.assertTrue(top3_hit(prediction, 2, 1))

    def test_score_diagnosis_flags_total_goals_and_big_win(self):
        prediction = {
            "score_grid": [
                (1, 0, 0.35),
                (2, 0, 0.25),
                (1, 1, 0.20),
                (0, 0, 0.20),
            ],
            "top_scores": [(1, 0, 0.35), (2, 0, 0.25), (1, 1, 0.20)],
        }
        diagnosis = score_diagnosis(prediction, 4, 0, (0.65, 0.22, 0.13))
        self.assertIn("underestimated_total_goals", diagnosis["score_diagnosis"])
        self.assertIn("underestimated_big_win", diagnosis["score_diagnosis"])
        self.assertIn("score_distribution_too_narrow", diagnosis["score_diagnosis"])
        summary = score_metric_summary([{**diagnosis, "top3_hit": False}], exact_hits=0, top3_hits=0)
        self.assertEqual(summary["top3_hit_rate"], 0.0)
        self.assertGreater(summary["underestimated_total_goals_rate"], 0.0)


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

    def test_handicap_probabilities_from_score_prediction(self):
        prediction = {"score_grid": [(2, 0, 0.4), (1, 0, 0.2), (1, 1, 0.2), (0, 1, 0.2)]}
        probs = handicap_probabilities_from_score_prediction(prediction, -1.0)
        self.assertIsNotNone(probs)
        self.assertAlmostEqual(probs[0], 0.4)
        self.assertAlmostEqual(probs[1], 0.2)
        self.assertAlmostEqual(probs[2], 0.4)

    def test_asian_handicap_quarter_line_settlement(self):
        self.assertEqual(asian_handicap_return_units(1, 0, -0.75), 0.5)
        self.assertEqual(asian_handicap_return_units(2, 0, -0.75), 1.0)
        self.assertEqual(asian_handicap_return_units(0, 0, -0.25), -0.5)
        self.assertEqual(asian_handicap_return_units(1, 1, 0.25), 0.5)

    def test_asian_handicap_probabilities_and_value_metrics(self):
        grid = [(2, 0, 0.4), (1, 0, 0.2), (1, 1, 0.2), (0, 1, 0.2)]
        probs = asian_handicap_probabilities_from_score_grid(grid, -0.75)
        self.assertIsNotNone(probs)
        self.assertAlmostEqual(probs["home_full_win"], 0.4)
        self.assertAlmostEqual(probs["home_half_win"], 0.2)
        self.assertAlmostEqual(probs["home_full_loss"], 0.4)
        asian_probs, market_probs, ev, kelly = asian_handicap_value_metrics(
            grid,
            -0.75,
            [1.90, 1.95],
            {"kelly_fraction": 0.25, "min_edge": 0.01},
        )
        self.assertIsNotNone(asian_probs)
        self.assertAlmostEqual(sum(market_probs), 1.0)
        self.assertEqual(len(ev), 2)
        self.assertEqual(len(kelly), 2)

    def test_parse_asian_handicap_json_source(self):
        aliases = build_team_aliases({("西班牙", "沙特阿拉伯"): (0.6, 0.2, 0.2)})
        text = json.dumps(
            {
                "markets": [
                    {
                        "match_time": "2026-06-22 03:00",
                        "home": "西班牙",
                        "away": "沙特",
                        "line": "-1.25",
                        "home_odds": 1.92,
                        "away_odds": 1.98,
                    }
                ]
            },
            ensure_ascii=False,
        )
        markets = parse_asian_handicap_source(text, aliases, "unit-test")
        key = ("2026-06-22 03:00", "西班牙", "沙特阿拉伯")
        self.assertIn(key, markets)
        self.assertEqual(markets[key]["line"], -1.25)
        self.assertEqual(markets[key]["odds"], [1.92, 1.98])

    def test_parse_zgzcw_ypdb_average_market(self):
        html_text = """
        <tr>
          <td>平均*</td>
          <td id="chupan-w-0" data="1.03">1.03</td>
          <td id="chupan-s-0" data='1'>一球</td>
          <td id="chupan-l-0" data="0.79">0.79</td>
          <td cid="0" data="0.99"><a>0.99</a></td>
          <td cid="0" data='1.25'><a>一/球半</a></td>
          <td cid="0" data="0.88"><a>0.88</a></td>
        </tr>
        """
        market = parse_zgzcw_ypdb_market(html_text)
        self.assertIsNotNone(market)
        self.assertEqual(market["line"], -1.25)
        self.assertEqual(market["odds"], [1.99, 1.88])

    def test_total_goals_quarter_line_settlement(self):
        self.assertEqual(total_goals_return_units(1, 1, 2.25), -0.5)
        self.assertEqual(total_goals_return_units(2, 1, 2.75), 0.5)
        self.assertEqual(total_goals_return_units(1, 1, 2.0), 0.0)
        self.assertEqual(total_goals_return_units(3, 0, 2.5), 1.0)

    def test_total_goals_probabilities_and_value_metrics(self):
        grid = [(2, 1, 0.4), (1, 1, 0.2), (1, 0, 0.2), (0, 0, 0.2)]
        probs = total_goals_probabilities_from_score_grid(grid, 2.5)
        self.assertIsNotNone(probs)
        self.assertAlmostEqual(probs["over_full_win"], 0.4)
        self.assertAlmostEqual(probs["over_full_loss"], 0.6)
        total_probs, market_probs, ev, kelly = total_goals_value_metrics(
            grid,
            2.5,
            [1.90, 1.95],
            {"kelly_fraction": 0.25, "min_edge": 0.01},
        )
        self.assertIsNotNone(total_probs)
        self.assertAlmostEqual(sum(market_probs), 1.0)
        self.assertEqual(len(ev), 2)
        self.assertEqual(len(kelly), 2)

    def test_parse_total_goals_json_source(self):
        aliases = build_team_aliases({("阿根廷", "奥地利"): (0.6, 0.2, 0.2)})
        text = json.dumps(
            {
                "markets": [
                    {
                        "match_time": "2026-06-23 01:00",
                        "home": "阿根廷",
                        "away": "奥地利",
                        "line": 2.75,
                        "over_odds": 1.92,
                        "under_odds": 1.88,
                    }
                ]
            },
            ensure_ascii=False,
        )
        markets = parse_total_goals_source(text, aliases, "unit-test")
        key = ("2026-06-23 01:00", "阿根廷", "奥地利")
        self.assertIn(key, markets)
        self.assertEqual(markets[key]["line"], 2.75)
        self.assertEqual(markets[key]["odds"], [1.92, 1.88])

    def test_parse_zgzcw_dxdb_average_market(self):
        html_text = """
        <tr>
          <td>平均*</td>
          <td id="chupan-w-0" data="0.96">0.96</td>
          <td id="chupan-s-0" data='2.5'>2.5球</td>
          <td id="chupan-l-0" data="0.86">0.86</td>
          <td cid="0" data="0.88"><a>0.88</a></td>
          <td cid="0" data='2.50'><a>2.5球</a></td>
          <td cid="0" data="0.94"><a>0.94</a></td>
        </tr>
        """
        market = parse_zgzcw_dxdb_market(html_text)
        self.assertIsNotNone(market)
        self.assertEqual(market["line"], 2.5)
        self.assertEqual(market["odds"], [1.88, 1.94])

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
