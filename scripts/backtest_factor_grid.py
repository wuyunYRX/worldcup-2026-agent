#!/usr/bin/env python3
"""Read-only rolling backtest for WDL calibration and score lambda settings."""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import worldcup_agent as wa  # noqa: E402
import review_completed_matches as rev  # noqa: E402


def merge_candidate_config(base_config: dict, candidate_params: Optional[dict]) -> dict:
    merged = dict(base_config)
    if isinstance(candidate_params, dict):
        merged.update(candidate_params)
    return merged


def load_latest_review_summary() -> Optional[dict]:
    review_dir = ROOT / "docs" / "review"
    files = sorted(review_dir.glob("postmatch-calibration_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in files:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


def review_bucket_to_metrics(summary: Optional[dict], key: str) -> Optional[dict]:
    if not isinstance(summary, dict):
        return None
    bucket_obj = summary.get(key)
    if not isinstance(bucket_obj, dict):
        return None
    bucket = bucket_obj
    probability_obj = bucket.get("probability_metrics")
    probability_metrics = probability_obj if isinstance(probability_obj, dict) else {}
    score_obj = bucket.get("score_metrics")
    score_metrics = score_obj if isinstance(score_obj, dict) else {}
    model_obj = probability_metrics.get("model")
    model_metrics = model_obj if isinstance(model_obj, dict) else {}
    return {
        "wdl": {
            "accuracy": model_metrics.get("accuracy"),
            "brier": model_metrics.get("brier"),
            "logloss": model_metrics.get("logloss"),
        },
        "score": {
            "exact": score_metrics.get("exact_score_accuracy"),
            "top3": score_metrics.get("top3_hit_rate"),
            "top5": score_metrics.get("top5_hit_rate"),
            "total_goal_bias": score_metrics.get("avg_total_goals_error"),
            "total_goal_abs_error": score_metrics.get("avg_abs_total_goals_error"),
        },
    }


def evaluate_snapshot_review(config: dict) -> Optional[dict]:
    probability_model = wa.load_probability_model(ROOT / "config" / "model_probabilities.json")
    strengths = wa.infer_team_strengths(probability_model)
    score_model = wa.load_score_model(ROOT / "models" / "score_model" / "score_model_v1.json")
    training_index = rev.load_training_candidate_index()
    prematch_news_index = rev.load_combined_prematch_news_index()
    predictions = rev.load_snapshot_predictions()
    results = rev.load_results()
    reviewed_rows = []
    exact = top3 = top5 = 0
    losses: List[float] = []
    briers: List[float] = []
    hits = 0
    for actual in results:
        key = (str(actual.get("match_time", "")), str(actual.get("home_team", "")), str(actual.get("away_team", "")))
        snapshot_row = predictions.get(key)
        if not snapshot_row:
            continue
        rebuilt = rev.rebuild_adjustment_fields(snapshot_row, training_index, prematch_news_index, config)
        home = str(rebuilt.get("home", ""))
        away = str(rebuilt.get("away", ""))
        base = probability_model.get((home, away)) or wa.estimate_match_probabilities(home, away, strengths)
        if not base:
            continue
        calibrated = wa.calibrate_wdl_probabilities(base, config)
        normal_odds = rebuilt.get("normal_odds") if isinstance(rebuilt.get("normal_odds"), list) else [0.0, 0.0, 0.0]
        prematch = prematch_news_index.get((wa.normalize_text(str(actual.get("match_time", ""))), wa.normalize_text(home), wa.normalize_text(away)))
        market_preview = wa.market_probabilities_from_odds(normal_odds, calibrated)
        model_probs, ai_adjustment = wa.adjust_probabilities_with_ai_context(calibrated, prematch, config, market_preview)
        actual_home = int(str(actual.get("home_goals", 0) or 0))
        actual_away = int(str(actual.get("away_goals", 0) or 0))
        outcome_idx = rev.actual_outcome(actual_home, actual_away)
        hits += max(range(3), key=lambda idx: model_probs[idx]) == outcome_idx
        losses.append(-math.log(max(model_probs[outcome_idx], 1e-9)))
        briers.append(sum((model_probs[idx] - (1.0 if idx == outcome_idx else 0.0)) ** 2 for idx in range(3)))
        candidate_row = dict(rebuilt)
        candidate_row["probabilities"] = list(model_probs)
        candidate_row["ai_adjusted_probabilities"] = list(model_probs)
        candidate_row["ai_adjustment"] = ai_adjustment
        candidate_row["context_adjusted_probabilities"] = ai_adjustment.get("context_adjusted_probabilities") or list(model_probs)
        candidate_row["weather_adjusted_probabilities"] = ai_adjustment.get("weather_adjusted_probabilities") or candidate_row["context_adjusted_probabilities"]
        candidate_row["tactical_adjusted_probabilities"] = ai_adjustment.get("tactical_adjusted_probabilities")
        candidate_row["value_adjusted_probabilities"] = ai_adjustment.get("value_adjusted_probabilities")
        score_prediction = rev.rebuild_counterfactual_score_prediction(candidate_row, score_model, strengths, prematch_news_index, config)
        score_row = rev.counterfactual_score_row(candidate_row, actual_home, actual_away, model_probs, score_prediction)
        if score_row:
            exact += 1 if score_row.get("exact_hit") else 0
            top3 += 1 if score_row.get("top3_hit") else 0
            top5 += 1 if score_row.get("top5_hit") else 0
            reviewed_rows.append(score_row)
    if not reviewed_rows:
        return None
    score_metrics = rev.score_metric_summary(reviewed_rows, exact, top3, top5)
    return {
        "sample_size": len(reviewed_rows),
        "wdl": {
            "accuracy": hits / len(reviewed_rows),
            "brier": sum(briers) / len(reviewed_rows),
            "logloss": sum(losses) / len(reviewed_rows),
        },
        "score": {
            "exact": score_metrics.get("exact_score_accuracy"),
            "top3": score_metrics.get("top3_hit_rate"),
            "top5": score_metrics.get("top5_hit_rate"),
            "total_goal_bias": score_metrics.get("avg_total_goals_error"),
            "total_goal_abs_error": score_metrics.get("avg_abs_total_goals_error"),
        },
    }


def load_finished_matches(path: Path) -> List[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows: List[dict] = []
    for match in payload.get("matches", []):
        if match.get("status") != "FINISHED":
            continue
        score = match.get("score", {}).get("fullTime", {})
        if score.get("home") is None or score.get("away") is None:
            continue
        kickoff = wa.parse_match_datetime(match.get("utcDate"))
        if kickoff is None:
            continue
        rows.append(
            {
                "kickoff": kickoff,
                "match_time": kickoff.strftime("%Y-%m-%d %H:%M"),
                "home": wa.team_display_name(str(match.get("homeTeam", {}).get("name", ""))),
                "away": wa.team_display_name(str(match.get("awayTeam", {}).get("name", ""))),
                "home_goals": int(score["home"]),
                "away_goals": int(score["away"]),
            }
        )
    return sorted(rows, key=lambda row: row["kickoff"])


def actual_outcome(row: dict) -> int:
    if row["home_goals"] > row["away_goals"]:
        return 0
    if row["home_goals"] == row["away_goals"]:
        return 1
    return 2


def outcome_probabilities(home: str, away: str, model: dict, strengths: Dict[str, float], config: dict) -> Tuple[float, float, float]:
    probabilities = model.get((home, away)) or wa.estimate_match_probabilities(home, away, strengths)
    return wa.calibrate_wdl_probabilities(probabilities, config)


def predicted_outcome_idx(probabilities: Tuple[float, float, float], config: dict) -> int:
    best_idx = max(range(3), key=lambda idx: probabilities[idx])
    if float(config.get("draw_pick_override_enabled", 0.0) or 0.0) > 0:
        home, draw, away = probabilities
        favorite = max(home, away)
        draw_threshold = float(config.get("draw_pick_min_probability", 0.26) or 0.26)
        draw_gap = float(config.get("draw_pick_max_gap_to_favorite", 0.15) or 0.15)
        favorite_max = float(config.get("draw_pick_favorite_max_probability", 0.50) or 0.50)
        if draw >= draw_threshold and draw >= favorite - draw_gap and favorite <= favorite_max:
            return 1
    return best_idx


def recent_summary(prior: List[dict], team: str, kickoff, limit: int) -> Tuple[float, float]:
    rows: List[Tuple[int, int]] = []
    for item in prior:
        if item["kickoff"] >= kickoff:
            continue
        if item["home"] == team:
            rows.append((item["home_goals"], item["away_goals"]))
        elif item["away"] == team:
            rows.append((item["away_goals"], item["home_goals"]))
    rows = rows[-limit:]
    if not rows:
        return 0.0, 0.0
    return sum(x for x, _ in rows) / len(rows), sum(y for _, y in rows) / len(rows)


def rest_days_model(prior: List[dict], team: str, kickoff) -> float:
    previous = [item["kickoff"] for item in prior if item["kickoff"] < kickoff and team in {item["home"], item["away"]}]
    if not previous:
        return 0.0
    days = (kickoff - previous[-1]).total_seconds() / 86400.0
    return max(0.0, min(7.0, days)) / 7.0


def score_lambdas(
    row: dict,
    prior: List[dict],
    probabilities: Tuple[float, float, float],
    score_model: dict,
    strengths: Dict[str, float],
    recent_scale: float,
    rest_scale: float,
    lambda_cap: float,
    lambda_scale: float,
) -> Tuple[float, float]:
    market_home, market_draw, market_away = wa.market_probabilities_from_odds([0.0, 0.0, 0.0], probabilities)
    home_elo = strengths.get(row["home"], 1500.0)
    away_elo = strengths.get(row["away"], 1500.0)
    feature_map = {name: 0.0 for name in score_model["feature_names"]}
    feature_map.update(
        {
            "bias": 1.0,
            "elo_diff_scaled": (home_elo - away_elo) / 100.0,
            "home_elo_scaled": home_elo / 2000.0,
            "away_elo_scaled": away_elo / 2000.0,
            "home_market_prob": market_home,
            "draw_market_prob": market_draw,
            "away_market_prob": market_away,
            "neutral_flag": 1.0,
            "knockout_flag": 0.0,
        }
    )
    home_l5 = recent_summary(prior, row["home"], row["kickoff"], 5)
    away_l5 = recent_summary(prior, row["away"], row["kickoff"], 5)
    home_l10 = recent_summary(prior, row["home"], row["kickoff"], 10)
    away_l10 = recent_summary(prior, row["away"], row["kickoff"], 10)
    feature_map.update(
        {
            "home_last5_goals_for": home_l5[0] * recent_scale,
            "home_last5_goals_against": home_l5[1] * recent_scale,
            "away_last5_goals_for": away_l5[0] * recent_scale,
            "away_last5_goals_against": away_l5[1] * recent_scale,
            "home_last10_goals_for": home_l10[0] * recent_scale,
            "home_last10_goals_against": home_l10[1] * recent_scale,
            "away_last10_goals_for": away_l10[0] * recent_scale,
            "away_last10_goals_against": away_l10[1] * recent_scale,
            "home_rest_days": rest_days_model(prior, row["home"], row["kickoff"]) * rest_scale,
            "away_rest_days": rest_days_model(prior, row["away"], row["kickoff"]) * rest_scale,
        }
    )
    features = [float(feature_map.get(name, 0.0)) for name in score_model["feature_names"]]
    home_lambda = wa.clamp_exp(wa.dot(features, score_model["home_model"]["weights"])) * lambda_scale
    away_lambda = wa.clamp_exp(wa.dot(features, score_model["away_model"]["weights"])) * lambda_scale
    return wa.clamp_goal_lambda(home_lambda, upper=lambda_cap), wa.clamp_goal_lambda(away_lambda, upper=lambda_cap)


def evaluate_wdl(rows: List[dict], model: dict, strengths: Dict[str, float], config: dict) -> dict:
    predictions = [outcome_probabilities(row["home"], row["away"], model, strengths, config) for row in rows]
    total = len(rows)
    accuracy = sum(1 for row, probs in zip(rows, predictions) if predicted_outcome_idx(probs, config) == actual_outcome(row)) / total
    brier = sum(sum((probs[idx] - (1.0 if idx == actual_outcome(row) else 0.0)) ** 2 for idx in range(3)) for row, probs in zip(rows, predictions)) / total
    logloss = -sum(math.log(max(probs[actual_outcome(row)], 1e-9)) for row, probs in zip(rows, predictions)) / total
    return {"accuracy": accuracy, "brier": brier, "logloss": logloss}


def evaluate_score(rows: List[dict], model: dict, score_model: dict, strengths: Dict[str, float], config: dict) -> dict:
    prior: List[dict] = []
    evaluated: List[dict] = []
    for row in rows:
        probs = outcome_probabilities(row["home"], row["away"], model, strengths, config)
        home_lambda, away_lambda = score_lambdas(
            row,
            prior,
            probs,
            score_model,
            strengths,
            float(config.get("score_recent_feature_scale", 0.0) or 0.0),
            float(config.get("score_rest_feature_scale", 0.0) or 0.0),
            float(config.get("score_lambda_cap", 4.5) or 4.5),
            float(config.get("score_lambda_global_scale", 1.0) or 1.0),
        )
        grid = wa.score_grid(home_lambda, away_lambda, max_goals=7, rho=(score_model.get("score_correlation") or {}).get("rho", 0.0))
        grid = wa.rerank_score_grid(
            grid,
            home_lambda,
            away_lambda,
            *wa.market_probabilities_from_odds([0.0, 0.0, 0.0], probs),
            probs,
            btts_promotion_weight=float(config.get("score_btts_promotion_weight", 0.0) or 0.0),
            high_total_promotion_weight=float(config.get("score_high_total_promotion_weight", 0.0) or 0.0),
            btts_total_threshold=float(config.get("score_btts_total_threshold", 2.55) or 2.55),
            common_result_boost=float(config.get("score_common_result_boost", 0.0) or 0.0),
            draw_candidate_boost=float(config.get("score_draw_candidate_boost", 0.0) or 0.0),
            top5_to_top3_btts_boost=float(config.get("score_top5_to_top3_btts_boost", 0.0) or 0.0),
            top5_to_top3_high_total_boost=float(config.get("score_top5_to_top3_high_total_boost", 0.0) or 0.0),
            big_margin_tail_boost=float(config.get("score_big_margin_tail_boost", 0.0) or 0.0),
        )
        top = sorted(grid, key=lambda item: item[2], reverse=True)
        promoted_top = wa.promote_top5_to_top3(
            top,
            home_lambda,
            away_lambda,
            *wa.market_probabilities_from_odds([0.0, 0.0, 0.0], probs),
            draw_candidate_boost=float(config.get("score_draw_candidate_boost", 0.0) or 0.0),
            top5_to_top3_btts_boost=float(config.get("score_top5_to_top3_btts_boost", 0.0) or 0.0),
            top5_to_top3_high_total_boost=float(config.get("score_top5_to_top3_high_total_boost", 0.0) or 0.0),
            big_margin_tail_boost=float(config.get("score_big_margin_tail_boost", 0.0) or 0.0),
            open_game_top5_gap_ratio=float(config.get("score_open_game_top5_gap_ratio", 0.0) or 0.0),
            open_game_top5_direct_boost=float(config.get("score_open_game_top5_direct_boost", 0.0) or 0.0),
        )
        evaluated.append({"row": row, "home_lambda": home_lambda, "away_lambda": away_lambda, "top": promoted_top, "ranked": top, "grid": grid})
        prior.append(row)
    total = len(evaluated)
    top3 = sum(1 for item in evaluated if (item["row"]["home_goals"], item["row"]["away_goals"]) in [(h, a) for h, a, _p in item["top"][:3]]) / total
    top5 = sum(1 for item in evaluated if (item["row"]["home_goals"], item["row"]["away_goals"]) in [(h, a) for h, a, _p in item["ranked"][:5]]) / total
    exact = sum(1 for item in evaluated if (item["row"]["home_goals"], item["row"]["away_goals"]) == (item["top"][0][0], item["top"][0][1])) / total
    bias = sum((item["home_lambda"] + item["away_lambda"]) - (item["row"]["home_goals"] + item["row"]["away_goals"]) for item in evaluated) / total
    abs_error = sum(abs((item["home_lambda"] + item["away_lambda"]) - (item["row"]["home_goals"] + item["row"]["away_goals"])) for item in evaluated) / total
    return {"exact": exact, "top3": top3, "top5": top5, "total_goal_bias": bias, "total_goal_abs_error": abs_error}


def evaluate_score_buckets(rows: List[dict], model: dict, score_model: dict, strengths: Dict[str, float], config: dict) -> Dict[str, dict]:
    prior: List[dict] = []
    buckets: Dict[str, List[dict]] = {
        "draw_risk": [],
        "open_game": [],
        "heavy_favorite": [],
        "balanced_match": [],
    }
    for row in rows:
        probs = outcome_probabilities(row["home"], row["away"], model, strengths, config)
        home_lambda, away_lambda = score_lambdas(
            row,
            prior,
            probs,
            score_model,
            strengths,
            float(config.get("score_recent_feature_scale", 0.0) or 0.0),
            float(config.get("score_rest_feature_scale", 0.0) or 0.0),
            float(config.get("score_lambda_cap", 4.5) or 4.5),
            float(config.get("score_lambda_global_scale", 1.0) or 1.0),
        )
        grid = wa.score_grid(home_lambda, away_lambda, max_goals=7, rho=(score_model.get("score_correlation") or {}).get("rho", 0.0))
        grid = wa.rerank_score_grid(
            grid,
            home_lambda,
            away_lambda,
            *wa.market_probabilities_from_odds([0.0, 0.0, 0.0], probs),
            probs,
            btts_promotion_weight=float(config.get("score_btts_promotion_weight", 0.0) or 0.0),
            high_total_promotion_weight=float(config.get("score_high_total_promotion_weight", 0.0) or 0.0),
            btts_total_threshold=float(config.get("score_btts_total_threshold", 2.55) or 2.55),
            common_result_boost=float(config.get("score_common_result_boost", 0.0) or 0.0),
            draw_candidate_boost=float(config.get("score_draw_candidate_boost", 0.0) or 0.0),
            top5_to_top3_btts_boost=float(config.get("score_top5_to_top3_btts_boost", 0.0) or 0.0),
            top5_to_top3_high_total_boost=float(config.get("score_top5_to_top3_high_total_boost", 0.0) or 0.0),
            big_margin_tail_boost=float(config.get("score_big_margin_tail_boost", 0.0) or 0.0),
        )
        ranked = sorted(grid, key=lambda item: item[2], reverse=True)
        promoted_top = wa.promote_top5_to_top3(
            ranked,
            home_lambda,
            away_lambda,
            *wa.market_probabilities_from_odds([0.0, 0.0, 0.0], probs),
            draw_candidate_boost=float(config.get("score_draw_candidate_boost", 0.0) or 0.0),
            top5_to_top3_btts_boost=float(config.get("score_top5_to_top3_btts_boost", 0.0) or 0.0),
            top5_to_top3_high_total_boost=float(config.get("score_top5_to_top3_high_total_boost", 0.0) or 0.0),
            big_margin_tail_boost=float(config.get("score_big_margin_tail_boost", 0.0) or 0.0),
            open_game_top5_gap_ratio=float(config.get("score_open_game_top5_gap_ratio", 0.0) or 0.0),
            open_game_top5_direct_boost=float(config.get("score_open_game_top5_direct_boost", 0.0) or 0.0),
        )
        detail = {
            "row": row,
            "probs": probs,
            "top": promoted_top,
            "ranked": ranked,
            "actual": actual_outcome(row),
            "home_lambda": home_lambda,
            "away_lambda": away_lambda,
        }
        if probs[1] >= 0.26 and abs(probs[0] - probs[2]) <= 0.18:
            buckets["draw_risk"].append(detail)
        if home_lambda + away_lambda >= 2.9:
            buckets["open_game"].append(detail)
        if abs(home_lambda - away_lambda) >= 0.95:
            buckets["heavy_favorite"].append(detail)
        if abs(probs[0] - probs[2]) <= 0.12:
            buckets["balanced_match"].append(detail)
        prior.append(row)

    def summarize(items: List[dict]) -> dict:
        if not items:
            return {"sample_size": 0, "wdl": None, "top3": None, "top5": None, "exact": None}
        n = len(items)
        wdl = sum(1 for item in items if predicted_outcome_idx(item["probs"], config) == item["actual"]) / n
        top3 = sum(1 for item in items if (item["row"]["home_goals"], item["row"]["away_goals"]) in [(h, a) for h, a, _p in item["top"][:3]]) / n
        top5 = sum(1 for item in items if (item["row"]["home_goals"], item["row"]["away_goals"]) in [(h, a) for h, a, _p in item["ranked"][:5]]) / n
        exact = sum(1 for item in items if (item["row"]["home_goals"], item["row"]["away_goals"]) == (item["top"][0][0], item["top"][0][1])) / n
        return {"sample_size": n, "wdl": wdl, "top3": top3, "top5": top5, "exact": exact}

    return {bucket: summarize(items) for bucket, items in buckets.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only factor grid backtest")
    parser.add_argument("--matches", type=Path, default=ROOT / "data" / "raw" / "wc2026_football_data_matches.json")
    parser.add_argument("--probabilities", type=Path, default=ROOT / "config" / "model_probabilities.json")
    parser.add_argument("--score-model", type=Path, default=ROOT / "models" / "score_model" / "score_model_v1.json")
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "bayesian_calibration.json")
    args = parser.parse_args()

    rows = load_finished_matches(args.matches)
    probability_model = wa.load_probability_model(args.probabilities)
    score_model = wa.load_score_model(args.score_model)
    config = wa.load_risk_config(args.config)
    try:
        raw_config = json.loads(args.config.read_text(encoding="utf-8")) if args.config.exists() else {}
    except Exception:
        raw_config = {}
    calibration = raw_config.get("calibration", {}) if isinstance(raw_config.get("calibration"), dict) else {}
    candidate_probability_params = calibration.get("candidate_probability_params") if isinstance(calibration.get("candidate_probability_params"), dict) else None
    candidate_score_params = calibration.get("candidate_score_params") if isinstance(calibration.get("candidate_score_params"), dict) else None
    candidate_config = merge_candidate_config(merge_candidate_config(config, candidate_probability_params), candidate_score_params)
    latest_review_summary = load_latest_review_summary()
    current_review_combined = review_bucket_to_metrics(latest_review_summary, "combined_metrics")
    current_review_snapshot = review_bucket_to_metrics(latest_review_summary, "snapshot_metrics")
    current_review_proxy = review_bucket_to_metrics(latest_review_summary, "playback_proxy_metrics")
    strengths = wa.infer_team_strengths(probability_model)
    current_backtest_wdl = evaluate_wdl(rows, probability_model, strengths, config)
    current_backtest_score = evaluate_score(rows, probability_model, score_model, strengths, config)
    current_backtest_buckets = evaluate_score_buckets(rows, probability_model, score_model, strengths, config)
    wdl = current_review_combined["wdl"] if current_review_combined else current_backtest_wdl
    score = current_review_combined["score"] if current_review_combined else current_backtest_score
    candidate_wdl = evaluate_wdl(rows, probability_model, strengths, candidate_config)
    candidate_score = evaluate_score(rows, probability_model, score_model, strengths, candidate_config)
    candidate_snapshot = evaluate_snapshot_review(candidate_config)
    wdl_candidates = []
    for draw_floor, balanced_floor, strong_floor, draw_boost, underdog_floor, underdog_boost, temperature in itertools.product(
        [0.18, 0.20, 0.22, 0.24],
        [0.20, 0.24, 0.28],
        [0.20, 0.24, 0.26],
        [0.03, 0.08, 0.10],
        [0.08, 0.12, 0.18],
        [0.0, 0.03, 0.05],
        [0.85, 1.0],
    ):
        candidate_config = dict(config)
        candidate_config.update(
            {
                "draw_probability_floor": draw_floor,
                "balanced_match_draw_floor": balanced_floor,
                "strong_favorite_draw_floor": strong_floor,
                "max_draw_calibration_boost": draw_boost,
                "underdog_probability_floor": underdog_floor,
                "max_underdog_calibration_boost": underdog_boost,
                "probability_temperature": temperature,
            }
        )
        metrics = evaluate_wdl(rows, probability_model, strengths, candidate_config)
        wdl_candidates.append({"params": candidate_config, **metrics})

    score_candidates = []
    for recent_scale, rest_scale, lambda_scale, lambda_cap, btts_weight, high_total_weight, common_boost, draw_boost, btts_top3_boost, high_total_top3_boost, margin_tail_boost in itertools.product(
        [0.0, 0.1],
        [0.0, 0.5],
        [1.35, 1.5],
        [2.8, 3.0],
        [0.0, 0.03],
        [0.0, 0.03],
        [0.0, 0.03],
        [0.0, 0.03],
        [0.0, 0.03],
        [0.0, 0.03],
        [0.0, 0.02],
    ):
        candidate_config = dict(config)
        candidate_config.update(
            {
                "score_recent_feature_scale": recent_scale,
                "score_rest_feature_scale": rest_scale,
                "score_lambda_global_scale": lambda_scale,
                "score_lambda_cap": lambda_cap,
                "score_btts_promotion_weight": btts_weight,
                "score_high_total_promotion_weight": high_total_weight,
                "score_common_result_boost": common_boost,
                "score_draw_candidate_boost": draw_boost,
                "score_top5_to_top3_btts_boost": btts_top3_boost,
                "score_top5_to_top3_high_total_boost": high_total_top3_boost,
                "score_big_margin_tail_boost": margin_tail_boost,
            }
        )
        metrics = evaluate_score(rows, probability_model, score_model, strengths, candidate_config)
        objective = metrics["top3"] * 2.0 + metrics["top5"] - abs(metrics["total_goal_bias"]) * 0.12 - metrics["total_goal_abs_error"] * 0.04
        score_candidates.append({"params": candidate_config, "objective": objective, **metrics})

    joint_candidates = []
    for draw_floor, balanced_floor, strong_floor, draw_boost, underdog_boost, temperature, recent_scale, rest_scale, lambda_scale, lambda_cap, common_boost, draw_score_boost, btts_top3_boost, high_total_top3_boost, margin_tail_boost in itertools.product(
        [0.18, 0.20],
        [0.18, 0.20],
        [0.24, 0.26],
        [0.10, 0.12],
        [0.03, 0.05],
        [0.75, 0.85],
        [0.0, 0.1],
        [0.0, 0.5],
        [1.35, 1.5],
        [2.8, 3.0],
        [0.0, 0.03],
        [0.0, 0.03],
        [0.0, 0.03],
        [0.0, 0.03],
        [0.0, 0.02],
    ):
        candidate_config = dict(config)
        candidate_config.update(
            {
                "draw_probability_floor": draw_floor,
                "balanced_match_draw_floor": balanced_floor,
                "strong_favorite_draw_floor": strong_floor,
                "max_draw_calibration_boost": draw_boost,
                "underdog_probability_floor": 0.08,
                "max_underdog_calibration_boost": underdog_boost,
                "probability_temperature": temperature,
                "draw_pick_override_enabled": 1.0,
                "draw_pick_min_probability": 0.26,
                "draw_pick_max_gap_to_favorite": 0.15,
                "draw_pick_favorite_max_probability": 0.50,
                "score_recent_feature_scale": recent_scale,
                "score_rest_feature_scale": rest_scale,
                "score_lambda_global_scale": lambda_scale,
                "score_lambda_cap": lambda_cap,
                "score_btts_promotion_weight": 0.0,
                "score_high_total_promotion_weight": 0.0,
                "score_common_result_boost": common_boost,
                "score_draw_candidate_boost": draw_score_boost,
                "score_top5_to_top3_btts_boost": btts_top3_boost,
                "score_top5_to_top3_high_total_boost": high_total_top3_boost,
                "score_big_margin_tail_boost": margin_tail_boost,
            }
        )
        wdl_metrics = evaluate_wdl(rows, probability_model, strengths, candidate_config)
        score_metrics = evaluate_score(rows, probability_model, score_model, strengths, candidate_config)
        objective = wdl_metrics["accuracy"] * 2.0 + score_metrics["top3"] * 2.0 + score_metrics["exact"] + score_metrics["top5"]
        joint_candidates.append({"params": candidate_config, "objective": objective, "wdl": wdl_metrics, "score": score_metrics})

    def compact_params(payload: dict, keys: Iterable[str]) -> dict:
        params = payload.get("params", {})
        return {key: params.get(key) for key in keys}

    best_wdl = sorted(wdl_candidates, key=lambda item: (item["accuracy"], -item["brier"], -item["logloss"]), reverse=True)[:5]
    best_score = sorted(score_candidates, key=lambda item: item["objective"], reverse=True)[:5]
    best_joint = sorted(joint_candidates, key=lambda item: item["objective"], reverse=True)[:5]
    apply_candidate = False
    snapshot_guard_passed = None
    combined_guard_passed = None
    if candidate_probability_params or candidate_score_params:
        current_objective = current_backtest_wdl["accuracy"] * 2.0 + current_backtest_score["top3"] * 2.0 + current_backtest_score["exact"] + current_backtest_score["top5"]
        candidate_objective = candidate_wdl["accuracy"] * 2.0 + candidate_score["top3"] * 2.0 + candidate_score["exact"] + candidate_score["top5"]
        if current_review_snapshot and candidate_snapshot:
            snapshot_guard_passed = (
                float(candidate_snapshot["wdl"]["accuracy"] or 0.0) >= float(current_review_snapshot["wdl"]["accuracy"] or 0.0)
                and float(candidate_snapshot["score"]["top3"] or 0.0) >= float(current_review_snapshot["score"]["top3"] or 0.0)
            )
        if current_review_combined:
            combined_guard_passed = (
                float(candidate_wdl["accuracy"] or 0.0) >= float(current_review_combined["wdl"]["accuracy"] or 0.0) - 0.02
                and float(candidate_score["top3"] or 0.0) >= float(current_review_combined["score"]["top3"] or 0.0)
            )
        apply_candidate = (
            candidate_objective >= current_objective - 1e-9
            and candidate_wdl["brier"] <= current_backtest_wdl["brier"] + 1e-9
            and candidate_wdl["logloss"] <= current_backtest_wdl["logloss"] + 1e-9
            and (snapshot_guard_passed is not False)
            and (combined_guard_passed is not False)
        )
    print(
        json.dumps(
            {
                "sample_size": len(rows),
                "current": {"wdl": wdl, "score": score},
                "current_backtest": {"wdl": current_backtest_wdl, "score": current_backtest_score},
                "current_backtest_buckets": current_backtest_buckets,
                "current_review_snapshot": current_review_snapshot,
                "current_review_playback_proxy": current_review_proxy,
                "current_review_combined": current_review_combined,
                "candidate": {
                    "source": "config.calibration.candidate_*",
                    "params": {
                        "probability": candidate_probability_params,
                        "score": candidate_score_params,
                    },
                    "wdl": candidate_wdl,
                    "score": candidate_score,
                    "snapshot_review": candidate_snapshot,
                },
                "best_wdl": [
                    {
                        "params": compact_params(
                            item,
                            [
                                "draw_probability_floor",
                                "balanced_match_draw_floor",
                                "strong_favorite_draw_floor",
                                "max_draw_calibration_boost",
                                "underdog_probability_floor",
                                "max_underdog_calibration_boost",
                                "probability_temperature",
                            ],
                        ),
                        "accuracy": item["accuracy"],
                        "brier": item["brier"],
                        "logloss": item["logloss"],
                    }
                    for item in best_wdl
                ],
                "best_score": [
                    {
                        "params": compact_params(
                            item,
                            [
                                "score_recent_feature_scale",
                                "score_rest_feature_scale",
                                "score_lambda_global_scale",
                                "score_lambda_cap",
                                "score_common_result_boost",
                                "score_draw_candidate_boost",
                                "score_top5_to_top3_btts_boost",
                                "score_top5_to_top3_high_total_boost",
                                "score_open_game_top5_gap_ratio",
                                "score_open_game_top5_direct_boost",
                                "score_big_margin_tail_boost",
                            ],
                        ),
                        "objective": item["objective"],
                        "exact": item["exact"],
                        "top3": item["top3"],
                        "top5": item["top5"],
                        "total_goal_bias": item["total_goal_bias"],
                        "total_goal_abs_error": item["total_goal_abs_error"],
                    }
                    for item in best_score
                ],
                "best_joint": [
                    {
                        "params": compact_params(
                            item,
                            [
                                "draw_probability_floor",
                                "balanced_match_draw_floor",
                                "strong_favorite_draw_floor",
                                "max_draw_calibration_boost",
                                "max_underdog_calibration_boost",
                                "probability_temperature",
                                "draw_pick_min_probability",
                                "draw_pick_max_gap_to_favorite",
                                "draw_pick_favorite_max_probability",
                                "score_recent_feature_scale",
                                "score_rest_feature_scale",
                                "score_lambda_global_scale",
                                "score_lambda_cap",
                                "score_common_result_boost",
                                "score_draw_candidate_boost",
                                "score_top5_to_top3_btts_boost",
                                "score_top5_to_top3_high_total_boost",
                                "score_open_game_top5_gap_ratio",
                                "score_open_game_top5_direct_boost",
                                "score_big_margin_tail_boost",
                            ],
                        ),
                        "objective": item["objective"],
                        "wdl": item["wdl"],
                        "score": item["score"],
                    }
                    for item in best_joint
                ],
                "application_recommendation": {
                    "apply_candidate": apply_candidate,
                    "reason": "candidate_beats_or_matches_current_backtest" if apply_candidate else "keep_current_until_candidate_improves_joint_metrics",
                    "comparison_basis": "review_combined_for_current_display_backtest_for_candidate_validation",
                    "snapshot_guard_passed": snapshot_guard_passed,
                    "combined_guard_passed": combined_guard_passed,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
