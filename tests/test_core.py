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
    adjustment_module_metrics,
    apply_weather_adjustment_tuning,
    brier,
    logloss,
    market_probs_from_odds,
    mistake_tag_summary,
    odds_movement_summary,
    odds_outcome_cross_metrics,
    outcome_mistake_tags,
    score_diagnosis,
    score_metric_summary,
    top1_hit,
    top3_hit,
)
from fetch_match_weather import build_weather_entry, load_venue_config  # noqa: E402
from fetch_prematch_team_news import load_match_weather_index, merge_weather  # noqa: E402
from worldcup_agent import (  # noqa: E402
    attach_odds_history,
    asian_handicap_probabilities_from_score_grid,
    asian_handicap_return_units,
    asian_handicap_value_metrics,
    apply_value_metrics,
    build_group_standings,
    safe_odds_triplet,
    build_team_aliases,
    calibrate_wdl_probabilities,
    filter_nearby_rows,
    filter_worldcup_rows,
    generate_report,
    handicap_probabilities_from_score_prediction,
    load_calibration_snapshot,
    load_risk_config,
    merge_candidate_rows,
    monte_carlo_validate_score_prediction,
    prematch_adjustments,
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

    def test_ai_adjustment_raises_draw_for_bad_weather(self):
        probs, meta = adjust_probabilities_with_ai_context(
            (0.54, 0.24, 0.22),
            {"temperature_c": 32, "humidity_pct": 80, "wind_kph": 28, "precipitation_mm": 3, "weather_summary": "高温高湿并伴有降雨和强风"},
            {"enable_ai_probability_adjustment": 1.0, "ai_probability_max_delta": 0.03, "ai_probability_high_confidence_delta": 0.05, "weather_adjustment_max_delta": 0.02},
        )
        self.assertIsNotNone(probs)
        self.assertTrue(meta["applied_weather"])
        self.assertGreater(probs[1], 0.24)
        self.assertAlmostEqual(sum(probs), 1.0)
        self.assertIn("weather_adjusted_probabilities", meta)

    def test_ai_adjustment_weather_weight_changes_delta(self):
        low_weight_probs, _meta = adjust_probabilities_with_ai_context(
            (0.54, 0.24, 0.22),
            {"temperature_c": 32, "humidity_pct": 80, "wind_kph": 28, "precipitation_mm": 3},
            {
                "enable_ai_probability_adjustment": 1.0,
                "ai_probability_max_delta": 0.03,
                "ai_probability_high_confidence_delta": 0.05,
                "weather_adjustment_max_delta": 0.02,
                "weather_adjustment_weight": 0.5,
            },
        )
        high_weight_probs, _meta = adjust_probabilities_with_ai_context(
            (0.54, 0.24, 0.22),
            {"temperature_c": 32, "humidity_pct": 80, "wind_kph": 28, "precipitation_mm": 3},
            {
                "enable_ai_probability_adjustment": 1.0,
                "ai_probability_max_delta": 0.03,
                "ai_probability_high_confidence_delta": 0.05,
                "weather_adjustment_max_delta": 0.02,
                "weather_adjustment_weight": 1.2,
            },
        )
        self.assertIsNotNone(low_weight_probs)
        self.assertIsNotNone(high_weight_probs)
        self.assertGreater(high_weight_probs[1] - 0.24, low_weight_probs[1] - 0.24)

    def test_prematch_adjustments_reduce_goal_expectation_for_bad_weather(self):
        home_mul, away_mul, meta = prematch_adjustments(
            {"temperature_c": 34, "humidity_pct": 82, "wind_kph": 30, "precipitation_mm": 4, "weather_summary": "高温高湿，风雨影响比赛节奏"}
        )
        self.assertLess(home_mul, 1.0)
        self.assertLess(away_mul, 1.0)
        self.assertGreater(meta["weather_severity"], 0.0)
        self.assertEqual(meta["weather_summary"], "高温高湿，风雨影响比赛节奏")

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
    def test_load_venue_config_and_match_weather_merge(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "venues.json"
            config_path.write_text(
                json.dumps(
                    {
                        "matches": [
                            {
                                "match_time": "2026-06-23 01:00",
                                "home_team": "阿根廷",
                                "away_team": "奥地利",
                                "venue_name": "示例球场",
                                "venue_city": "示例城市",
                                "latitude": 31.0,
                                "longitude": -97.0,
                                "timezone": "America/Chicago",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            venue_index = load_venue_config(config_path)
            self.assertIn(("2026-06-23 01:00", "阿根廷", "奥地利"), venue_index)

            weather_path = Path(tmpdir) / "weather.json"
            weather_path.write_text(
                json.dumps(
                    [
                        {
                            "match_time": "2026-06-23 01:00",
                            "home_team": "阿根廷",
                            "away_team": "奥地利",
                            "temperature_c": 29.5,
                            "weather_summary": "示例城市 2026-06-23T01:00 气温 29.5C",
                            "weather_source": "open_meteo_forecast",
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            weather_index = load_match_weather_index(weather_path)
            entry = {"weather_source": "none", "weather_summary": ""}
            merge_weather(entry, weather_index[("2026-06-23 01:00", "阿根廷", "奥地利")])
            self.assertEqual(entry["temperature_c"], 29.5)
            self.assertEqual(entry["weather_source"], "open_meteo_forecast")

    def test_build_weather_entry_uses_hourly_forecast(self):
        from fetch_match_weather import fetch_json as _unused_fetch_json  # noqa: F401

        original = build_weather_entry.__globals__["fetch_json"]
        try:
            build_weather_entry.__globals__["fetch_json"] = lambda _url: {
                "hourly": {
                    "time": ["2026-06-23T01:00", "2026-06-23T02:00"],
                    "temperature_2m": [31.2, 30.4],
                    "relative_humidity_2m": [76, 72],
                    "precipitation": [0.8, 0.0],
                    "wind_speed_10m": [18.0, 12.0],
                }
            }
            item = build_weather_entry(
                {"match_time": "2026-06-23 01:00", "home": "阿根廷", "away": "奥地利"},
                {"venue_name": "示例球场", "venue_city": "示例城市", "latitude": 31.0, "longitude": -97.0, "timezone": "America/Chicago"},
            )
        finally:
            build_weather_entry.__globals__["fetch_json"] = original
        self.assertEqual(item["home_team"], "阿根廷")
        self.assertEqual(item["temperature_c"], 31.2)
        self.assertEqual(item["weather_source"], "open_meteo_forecast")
    def test_load_risk_config_and_calibration_snapshot_include_weather(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "risk.json"
            path.write_text(json.dumps({"weather_adjustment_weight": 1.2, "weather_adjustment_max_delta": 0.018, "calibration": {"weather_model_accuracy": 0.61}}), encoding="utf-8")
            risk = load_risk_config(path)
            calibration = load_calibration_snapshot(path)
        self.assertAlmostEqual(risk["weather_adjustment_weight"], 1.2)
        self.assertAlmostEqual(risk["weather_adjustment_max_delta"], 0.018)
        self.assertEqual(calibration["weather_model_accuracy"], 0.61)

    def test_generate_report_contains_module_review_section(self):
        report = generate_report([], "https://example.com", dt.datetime(2026, 6, 23, 12, 0), risk_config={"bankroll": 100.0, "kelly_fraction": 0.2})
        self.assertIn("最近一次复盘模块效果", report)
        self.assertIn("天气修正", report)
        self.assertIn("战术修正", report)
        self.assertIn("赔率变化", report)

    def test_safe_odds_triplet_and_attach_history(self):
        self.assertEqual(safe_odds_triplet(None), [0.0, 0.0, 0.0])
        self.assertEqual(safe_odds_triplet([1.9, 3.2]), [1.9, 3.2, 0.0])
        rows = [{"match_time": "2099-01-01 12:00", "home": "A", "away": "B", "normal_odds": [1.88, 3.20, 4.10]}]
        attach_odds_history(rows, dt.datetime(2098, 12, 31, 12, 0))
        self.assertIn("normal_odds_change", rows[0])
        self.assertIn("summary", rows[0]["normal_odds_change"])

    def test_odds_movement_summary_counts_shortening(self):
        metrics = odds_movement_summary(
            [
                {"normal_odds_change": {"from_opening": [-0.10, 0.00, 0.12], "from_previous": [-0.05, 0.01, 0.00]}},
                {"normal_odds_change": {"from_opening": [0.05, -0.08, -0.03], "from_previous": [0.02, -0.02, -0.01]}},
            ]
        )
        self.assertEqual(metrics["from_opening"]["sample_size"], 2)
        self.assertEqual(metrics["from_opening"]["home_shorter_count"], 1)
        self.assertEqual(metrics["from_opening"]["draw_shorter_count"], 1)
        self.assertEqual(metrics["from_opening"]["away_shorter_count"], 1)
        self.assertEqual(metrics["from_previous"]["home_shorter_count"], 1)

    def test_odds_outcome_cross_metrics(self):
        metrics = odds_outcome_cross_metrics(
            [
                {
                    "normal_odds_change": {"from_opening": [-0.10, 0.00, 0.12], "from_previous": [-0.05, 0.00, 0.02]},
                    "actual_outcome_idx": 0,
                    "model_top_outcome": "主胜",
                    "model_probabilities": (0.55, 0.25, 0.20),
                },
                {
                    "normal_odds_change": {"from_opening": [0.05, -0.08, -0.03], "from_previous": [0.00, -0.04, -0.01]},
                    "actual_outcome_idx": 1,
                    "model_top_outcome": "平",
                    "model_probabilities": (0.30, 0.38, 0.32),
                },
            ]
        )
        self.assertEqual(metrics["from_opening"]["home"]["sample_size"], 1)
        self.assertAlmostEqual(metrics["from_opening"]["home"]["actual_hit_rate"], 1.0)
        self.assertEqual(metrics["from_opening"]["draw"]["sample_size"], 1)
        self.assertAlmostEqual(metrics["from_opening"]["draw"]["model_pick_rate"], 1.0)
        self.assertEqual(metrics["from_opening"]["away"]["sample_size"], 1)
        self.assertEqual(metrics["from_previous"]["home"]["sample_size"], 1)

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

    def test_filter_worldcup_rows_removes_non_worldcup_matches(self):
        model = {("葡萄牙", "乌兹别克"): (0.7, 0.2, 0.1), ("英格兰", "加纳"): (0.65, 0.22, 0.13)}
        rows = [
            {"home": "葡萄牙", "away": "乌兹别克"},
            {"home": "库普斯", "away": "埃尔维斯"},
            {"home": "英格兰", "away": "加纳"},
        ]
        filtered = filter_worldcup_rows(rows, model)
        self.assertEqual([(row["home"], row["away"]) for row in filtered], [("葡萄牙", "乌兹别克"), ("英格兰", "加纳")])

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

    def test_monte_carlo_validates_top5_to_top3(self):
        prediction = {
            "lambda_home": 1.8,
            "lambda_away": 0.9,
            "score_grid": [
                (2, 0, 0.20),
                (1, 0, 0.18),
                (2, 1, 0.16),
                (1, 1, 0.12),
                (3, 0, 0.10),
                (0, 0, 0.08),
            ],
        }
        result = monte_carlo_validate_score_prediction(
            prediction,
            {
                "monte_carlo_simulations": 500,
                "monte_carlo_seed": 123,
                "monte_carlo_lambda_sigma": 0.05,
                "score_candidate_top_n": 5,
                "score_report_top_n": 3,
            },
            target_probabilities=(0.58, 0.24, 0.18),
        )
        self.assertIsNotNone(result)
        self.assertEqual(len(result["candidate_top_scores"]), 5)
        self.assertEqual(len(result["validated_top_scores"]), 3)
        candidate_keys = {(home, away) for home, away, _prob in result["candidate_top_scores"]}
        validated_keys = {(home, away) for home, away, _prob in result["validated_top_scores"]}
        self.assertTrue(validated_keys.issubset(candidate_keys))
        self.assertEqual(result["simulations"], 500)

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

    def test_outcome_mistake_tags_identify_wrong_confident_pick(self):
        tags = outcome_mistake_tags(
            1,
            (0.58, 0.24, 0.18),
            base_probs=(0.54, 0.27, 0.19),
            market_probs=(0.35, 0.34, 0.31),
            score_top3_hit_value=False,
            ai_adjustment={"applied_weather": True},
        )
        self.assertIn("wdl_miss", tags)
        self.assertIn("overconfident_wrong_pick", tags)
        self.assertIn("draw_missed", tags)
        self.assertIn("ai_adjustment_hurt", tags)
        self.assertIn("weather_adjustment_active", tags)

    def test_mistake_and_module_summaries(self):
        reviewed = [
            {
                "actual_outcome_idx": 1,
                "mistake_tags": ["wdl_miss", "draw_missed", "ai_adjustment_helped"],
                "base_model_probabilities": (0.50, 0.25, 0.25),
                "context_adjusted_probabilities": (0.47, 0.29, 0.24),
                "model_probabilities": (0.47, 0.29, 0.24),
                "ai_adjustment": {"applied": True},
                "applied_weather": True,
            },
            {
                "actual_outcome_idx": 0,
                "mistake_tags": ["wdl_hit"],
                "base_model_probabilities": (0.55, 0.25, 0.20),
                "context_adjusted_probabilities": (0.52, 0.28, 0.20),
                "model_probabilities": (0.52, 0.28, 0.20),
                "ai_adjustment": {"applied": True},
            },
        ]
        tag_metrics = mistake_tag_summary(reviewed)
        self.assertEqual(tag_metrics["counts"]["wdl_miss"], 1)
        self.assertEqual(tag_metrics["counts"]["wdl_hit"], 1)
        module_metrics = adjustment_module_metrics(reviewed)
        self.assertEqual(module_metrics["context"]["sample_size"], 2)
        self.assertEqual(module_metrics["context"]["improved_actual_probability"], 1)
        self.assertEqual(module_metrics["context"]["worsened_actual_probability"], 1)
        self.assertEqual(module_metrics["weather"]["sample_size"], 1)

    def test_apply_weather_adjustment_tuning_expand_and_reduce(self):
        expand_config = {"weather_adjustment_weight": 1.0, "weather_adjustment_max_delta": 0.02}
        expand_summary = {
            "adjustment_module_metrics": {
                "weather": {
                    "sample_size": 4,
                    "improved_actual_probability": 3,
                    "worsened_actual_probability": 1,
                    "fixed_top_outcome": 2,
                    "broke_top_outcome": 0,
                    "avg_actual_probability_delta": 0.006,
                }
            }
        }
        decision = apply_weather_adjustment_tuning(expand_config, expand_summary)
        self.assertEqual(decision, "weather_outperforms_expand")
        self.assertGreater(expand_config["weather_adjustment_weight"], 1.0)
        self.assertGreater(expand_config["weather_adjustment_max_delta"], 0.02)

        reduce_config = {"weather_adjustment_weight": 1.0, "weather_adjustment_max_delta": 0.02}
        reduce_summary = {
            "adjustment_module_metrics": {
                "weather": {
                    "sample_size": 4,
                    "improved_actual_probability": 1,
                    "worsened_actual_probability": 3,
                    "fixed_top_outcome": 0,
                    "broke_top_outcome": 2,
                    "avg_actual_probability_delta": -0.006,
                }
            }
        }
        decision = apply_weather_adjustment_tuning(reduce_config, reduce_summary)
        self.assertEqual(decision, "weather_underperforms_reduce")
        self.assertLess(reduce_config["weather_adjustment_weight"], 1.0)
        self.assertLess(reduce_config["weather_adjustment_max_delta"], 0.02)


if __name__ == "__main__":
    unittest.main()
