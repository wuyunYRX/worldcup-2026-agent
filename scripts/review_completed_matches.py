#!/usr/bin/env python3
"""Review completed matches and calibrate prediction probabilities."""

from __future__ import annotations

import datetime as dt
import json
import math
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
RUN_DOCS_DIR = ROOT / "docs" / "run"
REVIEW_DOCS_DIR = ROOT / "docs" / "review"
STATS_RESULTS_PATH = ROOT / "data" / "raw" / "statsbomb_matches_real.json"
WC2026_RESULTS_PATH = ROOT / "data" / "raw" / "wc2026_football_data_matches.json"
CALIBRATION_PATH = ROOT / "config" / "bayesian_calibration.json"
SCORE_MODEL_PATH = ROOT / "models" / "score_model" / "score_model_v1.json"
PREMATCH_HISTORY_PATH = ROOT / "data" / "raw" / "prematch_team_news_history.json"
OUTCOME_NAMES = ["主胜", "平", "客胜"]
DIAGNOSIS_LABELS = {
    "underestimated_total_goals": "低估总进球",
    "overestimated_total_goals": "高估总进球",
    "underestimated_draw": "低估平局",
    "underestimated_underdog_goal": "低估弱队进球",
    "underestimated_big_win": "低估大胜",
    "wrong_goal_diff_direction": "净胜球方向错误",
    "score_distribution_too_narrow": "比分分布过窄",
}
MISTAKE_TAG_LABELS = {
    "wdl_hit": "赛果方向命中",
    "wdl_miss": "赛果方向未命中",
    "low_actual_outcome_probability": "真实方向概率偏低",
    "very_low_actual_outcome_probability": "真实方向概率严重偏低",
    "overconfident_wrong_pick": "高置信错误方向",
    "favorite_overestimated": "高估热门方",
    "draw_missed": "平局漏判",
    "away_or_underdog_missed": "弱势/客队方向漏判",
    "market_model_disagreement": "模型与市场主方向分歧",
    "score_top3_miss": "比分 Top3 未命中",
    "ai_adjustment_helped": "AI 修正提高真实方向概率",
    "ai_adjustment_hurt": "AI 修正降低真实方向概率",
    "weather_adjustment_active": "天气修正已触发",
    "tactical_adjustment_active": "战术修正已触发",
    "value_adjustment_active": "身价/阵容价值修正已触发",
}

from worldcup_agent import (  # noqa: E402
    apply_monte_carlo_validation,
    asian_handicap_probabilities_from_score_grid,
    asian_handicap_return_units,
    calibrate_wdl_probabilities,
    estimate_match_probabilities,
    infer_team_strengths,
    load_risk_config,
    load_prematch_team_news,
    load_probability_model,
    load_score_model,
    market_probabilities_from_odds,
    monte_carlo_validate_score_prediction,
    normalize_text,
    score_prediction_from_trained_model,
    score_prediction_from_wdl,
    total_goals_probabilities_from_score_grid,
    total_goals_return_units,
)
from ai_probability_adjustment import adjust_probabilities_with_ai_context  # noqa: E402

EN_CN = {
    "Mexico": "墨西哥",
    "South Korea": "韩国",
    "Korea Republic": "韩国",
    "Czechia": "捷克",
    "Czech Republic": "捷克",
    "South Africa": "南非",
    "Canada": "加拿大",
    "Switzerland": "瑞士",
    "Bosnia-Herzegovina": "波黑",
    "Bosnia-H.": "波黑",
    "Qatar": "卡塔尔",
    "Brazil": "巴西",
    "Morocco": "摩洛哥",
    "Scotland": "苏格兰",
    "Haiti": "海地",
    "United States": "美国",
    "Australia": "澳大利亚",
    "Paraguay": "巴拉圭",
    "Turkey": "土耳其",
    "Turkiye": "土耳其",
    "Netherlands": "荷兰",
    "Sweden": "瑞典",
    "Germany": "德国",
    "Ivory Coast": "科特迪瓦",
    "Cote d'Ivoire": "科特迪瓦",
    "Ecuador": "厄瓜多尔",
    "Curacao": "库拉索",
    "Curaçao": "库拉索",
    "Japan": "日本",
    "New Zealand": "新西兰",
    "Iran": "伊朗",
    "Belgium": "比利时",
    "Egypt": "埃及",
    "Uruguay": "乌拉圭",
    "Saudi Arabia": "沙特阿拉伯",
    "Spain": "西班牙",
    "Cabo Verde": "佛得角",
    "Cape Verde": "佛得角",
    "Cape Verde Islands": "佛得角",
    "Norway": "挪威",
    "France": "法国",
    "Senegal": "塞内加尔",
    "Iraq": "伊拉克",
    "Argentina": "阿根廷",
    "Austria": "奥地利",
    "Jordan": "约旦",
    "Algeria": "阿尔及利亚",
    "Colombia": "哥伦比亚",
    "Portugal": "葡萄牙",
    "Uzbekistan": "乌兹别克",
    "DR Congo": "民主刚果",
    "D.R. Congo": "民主刚果",
    "Congo DR": "民主刚果",
    "England": "英格兰",
    "Ghana": "加纳",
    "Panama": "巴拿马",
    "Croatia": "克罗地亚",
    "Tunisia": "突尼斯",
}


def to_cn(name: str) -> str:
    return EN_CN.get(name, name)


def parse_iso(value: str) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_local(value: str) -> Optional[dt.datetime]:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.datetime.strptime(value.split(".")[0], fmt)
        except ValueError:
            continue
    return None


def utc_to_local_text(value: str) -> str:
    parsed = parse_iso(value)
    if parsed is None:
        return value
    return (parsed.astimezone(dt.timezone(dt.timedelta(hours=8))).replace(tzinfo=None)).strftime("%Y-%m-%d %H:%M")


def latest_snapshot() -> Optional[Path]:
    files = sorted(RUN_DOCS_DIR.glob("worldcup-2026-agent-predictions_*.json"))
    return files[-1] if files else None


def load_wc2026_results() -> List[Dict[str, object]]:
    if not WC2026_RESULTS_PATH.exists():
        return []
    raw = json.loads(WC2026_RESULTS_PATH.read_text(encoding="utf-8"))
    rows: List[Dict[str, object]] = []
    for match in raw.get("matches", []):
        if match.get("status") != "FINISHED":
            continue
        score = match.get("score", {}).get("fullTime", {})
        home_goals = score.get("home")
        away_goals = score.get("away")
        if home_goals is None or away_goals is None:
            continue
        rows.append(
            {
                "competition": "FIFA World Cup",
                "season": "2026",
                "match_time": utc_to_local_text(str(match.get("utcDate", ""))),
                "utc_match_time": match.get("utcDate", ""),
                "home_team": to_cn(str(match.get("homeTeam", {}).get("name", ""))),
                "away_team": to_cn(str(match.get("awayTeam", {}).get("name", ""))),
                "home_goals": int(home_goals),
                "away_goals": int(away_goals),
                "stage": "小组赛" if match.get("stage") == "GROUP_STAGE" else match.get("stage", ""),
                "group_name": str(match.get("group", "")).replace("GROUP_", ""),
                "source": "football_data_org_api",
            }
        )
    return rows


def load_stats_results() -> List[Dict[str, object]]:
    if not STATS_RESULTS_PATH.exists():
        return []
    raw = json.loads(STATS_RESULTS_PATH.read_text(encoding="utf-8"))
    rows = []
    for row in raw:
        match_time = str(row.get("match_time", ""))
        local_time = utc_to_local_text(match_time) if match_time.endswith("Z") else match_time
        rows.append({**row, "match_time": local_time})
    return rows


def load_results() -> List[Dict[str, object]]:
    merged: Dict[Tuple[str, str, str], Dict[str, object]] = {}
    for row in load_stats_results() + load_wc2026_results():
        key = (str(row.get("match_time", "")), str(row.get("home_team", "")), str(row.get("away_team", "")))
        if all(key):
            merged[key] = row
    return sorted(merged.values(), key=lambda row: str(row.get("match_time", "")))


def load_probability_config() -> Dict[str, float]:
    if CALIBRATION_PATH.exists():
        try:
            raw = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return {key: float(value) for key, value in raw.items() if isinstance(value, (int, float))}
        except Exception:
            return {}
    return {}


def load_combined_prematch_news_index() -> Dict[Tuple[str, str, str], Dict[str, object]]:
    history_index = load_prematch_team_news(PREMATCH_HISTORY_PATH)
    current_index = load_prematch_team_news(ROOT / "data" / "raw" / "prematch_team_news.json")
    merged = dict(history_index)
    merged.update(current_index)
    return merged


def rebuild_adjustment_fields(
    prediction_row: Dict[str, object],
    training_candidate_index: Dict[Tuple[str, str, str], Dict[str, object]],
    prematch_news_index: Dict[Tuple[str, str, str], Dict[str, object]],
    calibration_config: Dict[str, float],
) -> Dict[str, object]:
    base_probs = normalize_probs(prediction_row.get("base_probabilities"))
    model_probs = normalize_probs(prediction_row.get("probabilities"))
    if base_probs is None:
        return prediction_row
    key = (
        normalize_text(str(prediction_row.get("match_time", ""))),
        normalize_text(str(prediction_row.get("home", ""))),
        normalize_text(str(prediction_row.get("away", ""))),
    )
    current_context = normalize_probs(prediction_row.get("context_adjusted_probabilities"))
    current_tactical = normalize_probs(prediction_row.get("tactical_adjusted_probabilities"))
    current_value = normalize_probs(prediction_row.get("value_adjusted_probabilities"))
    current_weather = normalize_probs(prediction_row.get("weather_adjusted_probabilities"))
    if current_context and current_tactical and current_value and current_weather:
        return prediction_row
    candidate_row = training_candidate_index.get((str(prediction_row.get("match_time", "")), str(prediction_row.get("home", "")), str(prediction_row.get("away", ""))))
    if isinstance(candidate_row, dict):
        candidate_context = normalize_probs(candidate_row.get("context_adjusted_probabilities"))
        candidate_weather = normalize_probs(candidate_row.get("weather_adjusted_probabilities")) or candidate_context
        candidate_tactical = normalize_probs(candidate_row.get("tactical_adjusted_probabilities"))
        candidate_value = normalize_probs(candidate_row.get("value_adjusted_probabilities"))
        candidate_ai_adjustment = candidate_row.get("ai_adjustment")
        if any((candidate_context, candidate_weather, candidate_tactical, candidate_value)):
            merged_adjustment = dict(prediction_row.get("ai_adjustment") or {})
            if isinstance(candidate_ai_adjustment, dict):
                merged_adjustment.update(candidate_ai_adjustment)
            rebuilt = dict(prediction_row)
            rebuilt["ai_adjustment"] = merged_adjustment
            rebuilt["context_adjusted_probabilities"] = current_context or candidate_context or model_probs
            rebuilt["weather_adjusted_probabilities"] = current_weather or candidate_weather or rebuilt["context_adjusted_probabilities"]
            rebuilt["tactical_adjusted_probabilities"] = current_tactical or candidate_tactical
            rebuilt["value_adjusted_probabilities"] = current_value or candidate_value
            return rebuilt
    prematch = prematch_news_index.get(key)
    if not prematch:
        return prediction_row
    normal_odds_obj = prediction_row.get("normal_odds")
    normal_odds = [float(value) for value in normal_odds_obj[:3]] if isinstance(normal_odds_obj, list) and len(normal_odds_obj) >= 3 else [0.0, 0.0, 0.0]
    market_probs = market_probabilities_from_odds(normal_odds, base_probs)
    _recalculated_probs, ai_adjustment = adjust_probabilities_with_ai_context(base_probs, prematch, calibration_config, market_probs)
    merged_adjustment = dict(prediction_row.get("ai_adjustment") or {})
    merged_adjustment.update(ai_adjustment)
    rebuilt = dict(prediction_row)
    rebuilt["ai_adjustment"] = merged_adjustment
    rebuilt["context_adjusted_probabilities"] = merged_adjustment.get("context_adjusted_probabilities") or current_context or model_probs
    rebuilt["weather_adjusted_probabilities"] = merged_adjustment.get("weather_adjusted_probabilities") or rebuilt["context_adjusted_probabilities"]
    rebuilt["tactical_adjusted_probabilities"] = merged_adjustment.get("tactical_adjusted_probabilities") or current_tactical
    rebuilt["value_adjusted_probabilities"] = merged_adjustment.get("value_adjusted_probabilities") or current_value or model_probs
    return rebuilt


def build_full_playback_samples(results: List[Dict[str, object]]) -> List[Dict[str, object]]:
    model_path = ROOT / "config" / "model_probabilities.json"
    if not model_path.exists():
        return []
    model = load_probability_model(model_path)
    strengths = infer_team_strengths(model)
    calibration_config = load_probability_config()
    risk_config = load_risk_config(CALIBRATION_PATH)
    score_model = load_score_model(SCORE_MODEL_PATH)
    prematch_news_index = load_combined_prematch_news_index()
    samples: List[Dict[str, object]] = []
    for actual in results:
        home = str(actual.get("home_team", ""))
        away = str(actual.get("away_team", ""))
        if not home or not away:
            continue
        probs = model.get((home, away))
        source = "model_probability_table"
        if probs is None:
            probs = estimate_match_probabilities(home, away, strengths)
            source = "strength_estimate_fallback"
        probs = calibrate_wdl_probabilities(probs, calibration_config)
        actual_home = int(actual.get("home_goals", 0))
        actual_away = int(actual.get("away_goals", 0))
        outcome_idx = actual_outcome(actual_home, actual_away)
        match_time = str(actual.get("match_time", ""))
        trained_prediction = score_prediction_from_trained_model(
            score_model,
            home,
            away,
            str(actual.get("stage", "")),
            str(actual.get("group_name", "")),
            match_time,
            [0.0, 0.0, 0.0],
            fallback_probabilities=probs,
            strengths=strengths,
            prematch_news_index=prematch_news_index,
            score_goal_diff_shrink=float(risk_config.get("score_goal_diff_shrink", 0.0) or 0.0),
            btts_promotion_weight=float(risk_config.get("score_btts_promotion_weight", 0.0) or 0.0),
            high_total_promotion_weight=float(risk_config.get("score_high_total_promotion_weight", 0.0) or 0.0),
            btts_total_threshold=float(risk_config.get("score_btts_total_threshold", 2.55) or 2.55),
            score_lambda_cap=float(risk_config.get("score_lambda_cap", 4.5) or 4.5),
            score_lambda_global_scale=float(risk_config.get("score_lambda_global_scale", 1.0) or 1.0),
            score_recent_feature_scale=float(risk_config.get("score_recent_feature_scale", 0.0) or 0.0),
            score_rest_feature_scale=float(risk_config.get("score_rest_feature_scale", 0.0) or 0.0),
            score_historical_lambda_mix=float(risk_config.get("score_historical_lambda_mix", 1.0) or 1.0),
            score_common_result_boost=float(risk_config.get("score_common_result_boost", 0.0) or 0.0),
            score_draw_candidate_boost=float(risk_config.get("score_draw_candidate_boost", 0.0) or 0.0),
            score_top5_to_top3_btts_boost=float(risk_config.get("score_top5_to_top3_btts_boost", 0.0) or 0.0),
            score_top5_to_top3_high_total_boost=float(risk_config.get("score_top5_to_top3_high_total_boost", 0.0) or 0.0),
            score_big_margin_tail_boost=float(risk_config.get("score_big_margin_tail_boost", 0.0) or 0.0),
            score_open_game_top5_gap_ratio=float(risk_config.get("score_open_game_top5_gap_ratio", 0.0) or 0.0),
            score_open_game_top5_direct_boost=float(risk_config.get("score_open_game_top5_direct_boost", 0.0) or 0.0),
        )
        score_prediction = trained_prediction or score_prediction_from_wdl(
            probs,
            score_goal_diff_shrink=float(risk_config.get("score_goal_diff_shrink", 0.0) or 0.0),
            btts_promotion_weight=float(risk_config.get("score_btts_promotion_weight", 0.0) or 0.0),
            high_total_promotion_weight=float(risk_config.get("score_high_total_promotion_weight", 0.0) or 0.0),
            btts_total_threshold=float(risk_config.get("score_btts_total_threshold", 2.55) or 2.55),
            common_result_boost=float(risk_config.get("score_common_result_boost", 0.0) or 0.0),
            draw_candidate_boost=float(risk_config.get("score_draw_candidate_boost", 0.0) or 0.0),
            top5_to_top3_btts_boost=float(risk_config.get("score_top5_to_top3_btts_boost", 0.0) or 0.0),
            top5_to_top3_high_total_boost=float(risk_config.get("score_top5_to_top3_high_total_boost", 0.0) or 0.0),
            big_margin_tail_boost=float(risk_config.get("score_big_margin_tail_boost", 0.0) or 0.0),
            open_game_top5_gap_ratio=float(risk_config.get("score_open_game_top5_gap_ratio", 0.0) or 0.0),
            open_game_top5_direct_boost=float(risk_config.get("score_open_game_top5_direct_boost", 0.0) or 0.0),
        )
        diagnosis = score_diagnosis(score_prediction, actual_home, actual_away, probs)
        funnel_diagnosis = top5_to_top3_diagnosis(score_prediction, actual_home, actual_away)
        samples.append(
            {
                "match_time": match_time,
                "home": home,
                "away": away,
                "actual_score": f"{actual_home}-{actual_away}",
                "actual_outcome": OUTCOME_NAMES[outcome_idx],
                "actual_outcome_idx": outcome_idx,
                "model_probabilities": probs,
                "model_top_outcome": OUTCOME_NAMES[max(range(3), key=lambda idx: probs[idx])],
                "predicted_top3": score_prediction.get("top_scores") or [],
                "exact_hit": top1_hit(score_prediction, actual_home, actual_away),
                "top3_hit": top3_hit(score_prediction, actual_home, actual_away),
                "top5_hit": top5_hit(score_prediction, actual_home, actual_away),
                **funnel_diagnosis,
                **diagnosis,
                "sample_source": source,
            }
        )
    return samples


def playback_review_row(actual: Dict[str, object], playback_row: Dict[str, object]) -> Dict[str, object]:
    actual_home = int(actual.get("home_goals", 0))
    actual_away = int(actual.get("away_goals", 0))
    outcome_idx = actual_outcome(actual_home, actual_away)
    model_probs = normalize_probs(playback_row.get("model_probabilities"))
    predicted_top3 = playback_row.get("predicted_top3") or []
    exact = bool(playback_row.get("exact_hit"))
    top3 = bool(playback_row.get("top3_hit"))
    top5 = bool(playback_row.get("top5_hit"))
    diagnosis = score_diagnosis({"top_scores": predicted_top3}, actual_home, actual_away, model_probs)
    funnel_diagnosis = {
        "candidate_rank": None,
        "top5_to_top3_diagnosis": list(playback_row.get("top5_to_top3_diagnosis") or []),
    }
    mistake_tags = outcome_mistake_tags(
        outcome_idx,
        model_probs,
        base_probs=model_probs,
        market_probs=None,
        score_top3_hit_value=top3,
        ai_adjustment=None,
    )
    return {
        "match_time": actual.get("match_time", ""),
        "home": actual.get("home_team", ""),
        "away": actual.get("away_team", ""),
        "actual_score": f"{actual_home}-{actual_away}",
        "actual_outcome": OUTCOME_NAMES[outcome_idx],
        "actual_outcome_idx": outcome_idx,
        "snapshot": "",
        "generated_at": "",
        "base_model_probabilities": model_probs,
        "context_adjusted_probabilities": model_probs,
        "weather_adjusted_probabilities": model_probs,
        "tactical_adjusted_probabilities": None,
        "value_adjusted_probabilities": None,
        "model_probabilities": model_probs,
        "market_probabilities": None,
        "fused_probabilities": model_probs,
        "normal_odds_change": None,
        "ai_adjustment": {},
        "applied_tactical": False,
        "applied_value": False,
        "applied_weather": False,
        "mistake_tags": mistake_tags,
        "base_model_top_outcome": OUTCOME_NAMES[max(range(3), key=lambda idx: model_probs[idx])] if model_probs else None,
        "context_model_top_outcome": OUTCOME_NAMES[max(range(3), key=lambda idx: model_probs[idx])] if model_probs else None,
        "tactical_model_top_outcome": None,
        "value_model_top_outcome": None,
        "model_top_outcome": OUTCOME_NAMES[max(range(3), key=lambda idx: model_probs[idx])] if model_probs else None,
        "fused_top_outcome": OUTCOME_NAMES[max(range(3), key=lambda idx: model_probs[idx])] if model_probs else None,
        "predicted_top3": predicted_top3,
        "exact_hit": exact,
        "top3_hit": top3,
        "top5_hit": top5,
        "review_source": "full_playback_proxy",
        **funnel_diagnosis,
        **diagnosis,
    }


def snapshot_files() -> List[Path]:
    return sorted(RUN_DOCS_DIR.glob("worldcup-2026-agent-predictions_*.json"))


def training_candidate_files() -> List[Path]:
    return sorted(RUN_DOCS_DIR.glob("worldcup-2026-agent-training-candidates_*.json"))


def candidate_completeness_score(row: Dict[str, object]) -> int:
    score = 0
    for field in (
        "context_adjusted_probabilities",
        "weather_adjusted_probabilities",
        "tactical_adjusted_probabilities",
        "value_adjusted_probabilities",
    ):
        if normalize_probs(row.get(field)):
            score += 1
    adjustment = row.get("ai_adjustment")
    if isinstance(adjustment, dict):
        if adjustment.get("applied_tactical"):
            score += 2
        if adjustment.get("applied_value"):
            score += 2
        if adjustment.get("applied_weather"):
            score += 1
    return score


def candidate_rank_tuple(row: Dict[str, object], generated_at: str) -> Tuple[int, int, str]:
    match_dt = parse_local(str(row.get("match_time", "")))
    generated_dt = parse_local(generated_at)
    is_prematch = 1
    if match_dt and generated_dt and generated_dt > match_dt:
        is_prematch = 0
    return (candidate_completeness_score(row), is_prematch, generated_at)


def load_training_candidate_index() -> Dict[Tuple[str, str, str], Dict[str, object]]:
    best: Dict[Tuple[str, str, str], Dict[str, object]] = {}
    for path in training_candidate_files():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        generated_text = str(payload.get("generated_at", ""))
        rows = payload.get("rows")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            match_time = str(row.get("match_time", ""))
            home = str(row.get("home_team", ""))
            away = str(row.get("away_team", ""))
            key = (match_time, home, away)
            if not all(key):
                continue
            current = best.get(key)
            current_rank = candidate_rank_tuple(current, str(current.get("generated_at", ""))) if isinstance(current, dict) else (-1, -1, "")
            row_rank = candidate_rank_tuple(row, generated_text)
            if current is None or row_rank > current_rank:
                best[key] = {**row, "snapshot": path.name, "generated_at": generated_text}
    return best


def load_snapshot_predictions() -> Dict[Tuple[str, str, str], Dict[str, object]]:
    best: Dict[Tuple[str, str, str], Dict[str, object]] = {}
    for path in snapshot_files():
        payload = json.loads(path.read_text(encoding="utf-8"))
        generated = parse_local(str(payload.get("generated_at", "")))
        for match in payload.get("matches", []):
            match_time = str(match.get("match_time", ""))
            match_dt = parse_local(match_time)
            key = (match_time, str(match.get("home", "")), str(match.get("away", "")))
            if not all(key):
                continue
            if generated and match_dt and generated > match_dt:
                continue
            current = best.get(key)
            if current is None or str(payload.get("generated_at", "")) > str(current.get("generated_at", "")):
                best[key] = {**match, "snapshot": path.name, "generated_at": payload.get("generated_at", "")}
    return best


def actual_outcome(home_goals: int, away_goals: int) -> int:
    if home_goals > away_goals:
        return 0
    if home_goals == away_goals:
        return 1
    return 2


def normalize_probs(values: object) -> Optional[Tuple[float, float, float]]:
    if not isinstance(values, (list, tuple)) or len(values) < 3:
        return None
    try:
        probs = tuple(max(float(values[idx]), 0.0) for idx in range(3))
    except (TypeError, ValueError):
        return None
    total = sum(probs)
    if total <= 0:
        return None
    return probs[0] / total, probs[1] / total, probs[2] / total


def market_probs_from_odds(values: object) -> Optional[Tuple[float, float, float]]:
    if not isinstance(values, (list, tuple)) or len(values) < 3:
        return None
    try:
        odds = [float(values[idx]) for idx in range(3)]
    except (TypeError, ValueError):
        return None
    if not all(value > 0 for value in odds):
        return None
    implied = [1.0 / value for value in odds]
    total = sum(implied)
    return implied[0] / total, implied[1] / total, implied[2] / total


def normalize_prob_pair(values: object) -> Optional[Tuple[float, float]]:
    if not isinstance(values, (list, tuple)) or len(values) < 2:
        return None
    try:
        first = max(float(values[0]), 0.0)
        second = max(float(values[1]), 0.0)
    except (TypeError, ValueError):
        return None
    total = first + second
    if total <= 0:
        return None
    return first / total, second / total


def safe_float_list(values: object, limit: int) -> List[float]:
    if not isinstance(values, (list, tuple)):
        return [0.0] * limit
    result: List[float] = []
    for idx in range(limit):
        try:
            result.append(float(values[idx]))
        except (IndexError, TypeError, ValueError):
            result.append(0.0)
    return result


def brier(probabilities: Tuple[float, float, float], outcome_idx: int) -> float:
    return sum((probabilities[idx] - (1.0 if idx == outcome_idx else 0.0)) ** 2 for idx in range(3))


def logloss(probabilities: Tuple[float, float, float], outcome_idx: int) -> float:
    return -math.log(max(probabilities[outcome_idx], 1e-12))


def top_outcome_idx(probabilities: Optional[Tuple[float, float, float]]) -> Optional[int]:
    if not probabilities:
        return None
    return max(range(3), key=lambda idx: probabilities[idx])


def actual_probability(probabilities: Optional[Tuple[float, float, float]], outcome_idx: int) -> Optional[float]:
    if not probabilities:
        return None
    return probabilities[outcome_idx]


def top1_hit(prediction: Dict[str, object], actual_home: int, actual_away: int) -> bool:
    top_scores = prediction.get("top_scores") or []
    if not top_scores:
        return False
    home, away, _prob = top_scores[0]
    return int(home) == actual_home and int(away) == actual_away


def top3_hit(prediction: Dict[str, object], actual_home: int, actual_away: int) -> bool:
    return top_n_hit(prediction, actual_home, actual_away, 3)


def top5_hit(prediction: Dict[str, object], actual_home: int, actual_away: int) -> bool:
    return top_n_hit(prediction, actual_home, actual_away, 5)


def top_n_hit(prediction: Dict[str, object], actual_home: int, actual_away: int, limit: int) -> bool:
    for home, away, _prob in score_candidates(prediction, limit):
        if int(home) == actual_home and int(away) == actual_away:
            return True
    return False


def score_candidates(prediction: Dict[str, object], limit: int) -> List[Tuple[int, int, float]]:
    raw_top = prediction.get("top_scores") or []
    top_scores: List[Tuple[int, int, float]] = []
    for item in raw_top:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            top_scores.append((int(item[0]), int(item[1]), float(item[2]) if len(item) > 2 else 0.0))
    if limit > len(top_scores):
        ranked = ranked_scores(prediction, limit)
        seen = {(h, a) for h, a, _ in top_scores}
        for home, away, prob in ranked:
            if (home, away) not in seen:
                top_scores.append((home, away, prob))
                if len(top_scores) >= limit:
                    break
    return top_scores[:limit]


def score_candidate_rank(prediction: Dict[str, object], actual_home: int, actual_away: int, limit: int = 10) -> Optional[int]:
    for idx, (home, away, _prob) in enumerate(score_candidates(prediction, limit), start=1):
        if int(home) == actual_home and int(away) == actual_away:
            return idx
    return None


def top5_to_top3_diagnosis(prediction: Dict[str, object], actual_home: int, actual_away: int) -> Dict[str, object]:
    rank = score_candidate_rank(prediction, actual_home, actual_away, limit=10)
    labels: List[str] = []
    if rank is not None and 4 <= rank <= 5:
        labels.append("top5_only_hit")
        top3 = score_candidates(prediction, 3)
        if top3:
            actual_total = actual_home + actual_away
            actual_margin = abs(actual_home - actual_away)
            avg_top3_total = sum(home + away for home, away, _prob in top3) / len(top3)
            avg_top3_margin = sum(abs(home - away) for home, away, _prob in top3) / len(top3)
            if actual_total >= avg_top3_total + 1.0:
                labels.append("actual_total_higher_than_top3")
            elif actual_total <= avg_top3_total - 1.0:
                labels.append("actual_total_lower_than_top3")
            if actual_margin >= avg_top3_margin + 1.0:
                labels.append("actual_margin_bigger_than_top3")
            if actual_home == actual_away and not any(home == away for home, away, _prob in top3):
                labels.append("draw_outside_top3")
            if min(actual_home, actual_away) >= 1 and not any(min(home, away) >= 1 for home, away, _prob in top3):
                labels.append("both_teams_score_outside_top3")
    return {"candidate_rank": rank, "top5_to_top3_diagnosis": labels}


def ranked_scores(prediction: Dict[str, object], limit: int = 3) -> List[Tuple[int, int, float]]:
    scores = score_distribution(prediction)
    if not scores:
        return []
    return sorted(scores, key=lambda item: item[2], reverse=True)[:limit]


def score_distribution(prediction: Dict[str, object]) -> List[Tuple[int, int, float]]:
    raw_scores = prediction.get("score_grid") or prediction.get("top_scores") or []
    scores: List[Tuple[int, int, float]] = []
    for item in raw_scores:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        try:
            scores.append((int(item[0]), int(item[1]), max(float(item[2]), 0.0)))
        except (TypeError, ValueError):
            continue
    total = sum(prob for _home, _away, prob in scores)
    if total <= 0:
        return []
    return [(home, away, prob / total) for home, away, prob in scores]


def score_expectations(prediction: Dict[str, object]) -> Dict[str, Optional[float]]:
    dist = score_distribution(prediction)
    if not dist:
        return {
            "expected_home_goals": None,
            "expected_away_goals": None,
            "expected_total_goals": None,
            "expected_goal_diff": None,
        }
    home_goals = sum(home * prob for home, _away, prob in dist)
    away_goals = sum(away * prob for _home, away, prob in dist)
    return {
        "expected_home_goals": home_goals,
        "expected_away_goals": away_goals,
        "expected_total_goals": home_goals + away_goals,
        "expected_goal_diff": home_goals - away_goals,
    }


def has_big_win_in_top_scores(prediction: Dict[str, object], n: int = 3, margin: int = 3) -> bool:
    for home, away, _prob in ranked_scores(prediction, n):
        if abs(int(home) - int(away)) >= margin:
            return True
    return False


def score_diagnosis(
    prediction: Dict[str, object],
    actual_home: int,
    actual_away: int,
    model_probs: Optional[Tuple[float, float, float]] = None,
) -> Dict[str, object]:
    expected = score_expectations(prediction)
    expected_home = expected["expected_home_goals"]
    expected_away = expected["expected_away_goals"]
    expected_total = expected["expected_total_goals"]
    expected_diff = expected["expected_goal_diff"]
    actual_total = actual_home + actual_away
    actual_diff = actual_home - actual_away
    labels: List[str] = []

    total_error = None if expected_total is None else expected_total - actual_total
    diff_error = None if expected_diff is None else expected_diff - actual_diff
    home_error = None if expected_home is None else expected_home - actual_home
    away_error = None if expected_away is None else expected_away - actual_away

    if total_error is not None:
        if total_error <= -1.25:
            labels.append("underestimated_total_goals")
        elif total_error >= 1.25:
            labels.append("overestimated_total_goals")
    if diff_error is not None and expected_diff is not None and actual_diff != 0 and expected_diff * actual_diff < 0:
        labels.append("wrong_goal_diff_direction")
    if actual_home == actual_away and model_probs and max(range(3), key=lambda idx: model_probs[idx]) != 1:
        labels.append("underestimated_draw")
    if expected_home is not None and expected_away is not None:
        actual_outcome_idx = actual_outcome(actual_home, actual_away)
        if actual_outcome_idx == 0 and actual_away >= 1 and expected_away <= actual_away - 0.75:
            labels.append("underestimated_underdog_goal")
        elif actual_outcome_idx == 2 and actual_home >= 1 and expected_home <= actual_home - 0.75:
            labels.append("underestimated_underdog_goal")
    if abs(actual_diff) >= 3 and not has_big_win_in_top_scores(prediction, n=3, margin=3):
        labels.append("underestimated_big_win")
    if actual_total >= 4 and not any((home + away) >= 4 for home, away, _prob in ranked_scores(prediction, 3)):
        labels.append("score_distribution_too_narrow")

    return {
        **expected,
        "actual_total_goals": actual_total,
        "actual_goal_diff": actual_diff,
        "total_goals_error": total_error,
        "goal_diff_error": diff_error,
        "home_goals_error": home_error,
        "away_goals_error": away_error,
        "score_diagnosis": labels,
    }


def metric_summary(values: List[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def safe_float(value: object) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def settled_direction(units: float, positive_label: str, negative_label: str) -> str:
    if units > 0:
        return positive_label
    if units < 0:
        return negative_label
    return "push"


def score_market_direction_diagnosis(prediction_row: Dict[str, object], actual_home: int, actual_away: int) -> Dict[str, object]:
    result: Dict[str, object] = {}

    total_line = safe_float(prediction_row.get("total_goals_line"))
    total_probs = prediction_row.get("total_goals_probabilities")
    if total_line is not None and isinstance(total_probs, dict):
        over_prob = float(total_probs.get("over_full_win", 0.0) or 0.0) + float(total_probs.get("over_half_win", 0.0) or 0.0)
        under_prob = float(total_probs.get("over_full_loss", 0.0) or 0.0) + float(total_probs.get("over_half_loss", 0.0) or 0.0)
        model_direction = "over" if over_prob >= under_prob else "under"
        actual_units = total_goals_return_units(actual_home, actual_away, total_line)
        actual_direction = settled_direction(actual_units, "over", "under")
        result.update(
            {
                "total_goals_line": total_line,
                "total_goals_model_direction": model_direction,
                "total_goals_actual_direction": actual_direction,
                "total_goals_direction_hit": None if actual_direction == "push" else model_direction == actual_direction,
                "total_goals_model_over_probability": over_prob,
                "total_goals_model_under_probability": under_prob,
            }
        )

    asian_line = safe_float(prediction_row.get("asian_handicap_line"))
    asian_probs = prediction_row.get("asian_handicap_probabilities")
    if asian_line is not None and isinstance(asian_probs, dict):
        home_cover_prob = float(asian_probs.get("home_full_win", 0.0) or 0.0) + float(asian_probs.get("home_half_win", 0.0) or 0.0)
        away_cover_prob = float(asian_probs.get("home_full_loss", 0.0) or 0.0) + float(asian_probs.get("home_half_loss", 0.0) or 0.0)
        model_direction = "home_cover" if home_cover_prob >= away_cover_prob else "away_cover"
        actual_units = asian_handicap_return_units(actual_home, actual_away, asian_line)
        actual_direction = settled_direction(actual_units, "home_cover", "away_cover")
        result.update(
            {
                "asian_handicap_line": asian_line,
                "asian_handicap_model_direction": model_direction,
                "asian_handicap_actual_direction": actual_direction,
                "asian_handicap_direction_hit": None if actual_direction == "push" else model_direction == actual_direction,
                "asian_handicap_model_home_cover_probability": home_cover_prob,
                "asian_handicap_model_away_cover_probability": away_cover_prob,
            }
        )

    return result


def rebuild_counterfactual_score_prediction(
    prediction_row: Dict[str, object],
    score_model: Optional[Dict[str, object]],
    strengths: Dict[str, float],
    prematch_news_index: Optional[Dict[Tuple[str, str, str], Dict[str, object]]],
    risk_config: Dict[str, float],
    score_param_overrides: Optional[Dict[str, float]] = None,
) -> Optional[Dict[str, object]]:
    score_param_overrides = score_param_overrides or {}

    def param(name: str, default: float) -> float:
        value = score_param_overrides.get(name, risk_config.get(name, default))
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    probs = normalize_probs(prediction_row.get("probabilities")) or normalize_probs(prediction_row.get("fused_probabilities"))
    if not probs:
        return None
    home = str(prediction_row.get("home", ""))
    away = str(prediction_row.get("away", ""))
    match_time = str(prediction_row.get("match_time", ""))
    normal_odds = safe_float_list(prediction_row.get("normal_odds"), 3)
    handicap_line = safe_float(prediction_row.get("handicap"))
    handicap_market = normalize_probs(prediction_row.get("handicap_market_probabilities"))
    asian_line = safe_float(prediction_row.get("asian_handicap_line"))
    asian_market = normalize_prob_pair(prediction_row.get("asian_handicap_market_probabilities"))
    total_line = safe_float(prediction_row.get("total_goals_line"))
    total_market = normalize_prob_pair(prediction_row.get("total_goals_market_probabilities"))

    score_prediction = score_prediction_from_trained_model(
        score_model=score_model,
        home=home,
        away=away,
        stage=str(prediction_row.get("stage", "小组赛")),
        round_text=str(prediction_row.get("round", "")),
        match_time=match_time,
        normal_odds=normal_odds,
        fallback_probabilities=probs,
        strengths=strengths,
        prematch_news_index=prematch_news_index,
        handicap_line=handicap_line,
        handicap_market_probabilities=handicap_market,
        asian_handicap_line=asian_line,
        asian_market_probabilities=asian_market,
        total_goals_line=total_line,
        total_goals_market_probabilities=total_market,
        score_goal_diff_shrink=param("score_goal_diff_shrink", 0.0),
        btts_promotion_weight=param("score_btts_promotion_weight", 0.0),
        high_total_promotion_weight=param("score_high_total_promotion_weight", 0.0),
        btts_total_threshold=param("score_btts_total_threshold", 2.55),
        score_common_result_boost=param("score_common_result_boost", 0.0),
        score_draw_candidate_boost=param("score_draw_candidate_boost", 0.0),
        score_top5_to_top3_btts_boost=param("score_top5_to_top3_btts_boost", 0.0),
        score_top5_to_top3_high_total_boost=param("score_top5_to_top3_high_total_boost", 0.0),
        score_big_margin_tail_boost=param("score_big_margin_tail_boost", 0.0),
        score_open_game_top5_gap_ratio=param("score_open_game_top5_gap_ratio", 0.0),
        score_open_game_top5_direct_boost=param("score_open_game_top5_direct_boost", 0.0),
    ) or score_prediction_from_wdl(
        probs,
        handicap_line,
        handicap_market,
        asian_line,
        asian_market,
        total_line,
        total_market,
        param("score_goal_diff_shrink", 0.0),
        param("score_btts_promotion_weight", 0.0),
        param("score_high_total_promotion_weight", 0.0),
        param("score_btts_total_threshold", 2.55),
        param("score_common_result_boost", 0.0),
        param("score_draw_candidate_boost", 0.0),
        param("score_top5_to_top3_btts_boost", 0.0),
        param("score_top5_to_top3_high_total_boost", 0.0),
        param("score_big_margin_tail_boost", 0.0),
        param("score_open_game_top5_gap_ratio", 0.0),
        param("score_open_game_top5_direct_boost", 0.0),
    )
    if score_prediction and risk_config.get("enable_monte_carlo", 1.0) > 0:
        market_probs = market_probs_from_odds(normal_odds) or normalize_probs(prediction_row.get("market_probabilities"))
        monte_carlo = monte_carlo_validate_score_prediction(
            score_prediction,
            risk_config,
            target_probabilities=probs,
            market_probabilities=market_probs,
            handicap_line=handicap_line,
            handicap_market_probabilities=handicap_market,
            asian_handicap_line=asian_line,
            asian_market_probabilities=asian_market,
            total_goals_line=total_line,
            total_goals_market_probabilities=total_market,
        )
        score_prediction = apply_monte_carlo_validation(score_prediction, monte_carlo)
    return score_prediction


def counterfactual_score_row(
    prediction_row: Dict[str, object],
    actual_home: int,
    actual_away: int,
    model_probs: Optional[Tuple[float, float, float]],
    score_prediction: Optional[Dict[str, object]],
) -> Optional[Dict[str, object]]:
    if not score_prediction:
        return None
    exact = top1_hit(score_prediction, actual_home, actual_away)
    top3 = top3_hit(score_prediction, actual_home, actual_away)
    top5 = top5_hit(score_prediction, actual_home, actual_away)
    diagnosis = score_diagnosis(score_prediction, actual_home, actual_away, model_probs)
    funnel_diagnosis = top5_to_top3_diagnosis(score_prediction, actual_home, actual_away)
    market_row = dict(prediction_row)
    score_grid = score_prediction.get("score_grid")
    if isinstance(score_grid, list):
        asian_line = safe_float(prediction_row.get("asian_handicap_line"))
        total_line = safe_float(prediction_row.get("total_goals_line"))
        if asian_line is not None:
            market_row["asian_handicap_probabilities"] = asian_handicap_probabilities_from_score_grid(score_grid, asian_line)
        if total_line is not None:
            market_row["total_goals_probabilities"] = total_goals_probabilities_from_score_grid(score_grid, total_line)
    market_diagnosis = score_market_direction_diagnosis(market_row, actual_home, actual_away)
    return {
        "match_time": prediction_row.get("match_time", ""),
        "home": prediction_row.get("home", ""),
        "away": prediction_row.get("away", ""),
        "actual_score": f"{actual_home}-{actual_away}",
        "snapshot": prediction_row.get("snapshot", ""),
        "generated_at": prediction_row.get("generated_at", ""),
        "predicted_top3": score_prediction.get("top_scores") or [],
        "exact_hit": exact,
        "top3_hit": top3,
        "top5_hit": top5,
        "market_lambda_adjustments": score_prediction.get("market_lambda_adjustments"),
        **market_diagnosis,
        **funnel_diagnosis,
        **diagnosis,
    }


def direction_hit_summary(reviewed: List[Dict[str, object]], field: str) -> Dict[str, object]:
    rows = [row for row in reviewed if isinstance(row.get(field), bool)]
    if not rows:
        return {"sample_size": 0, "hit_rate": None, "hit_count": 0, "miss_count": 0}
    hits = sum(1 for row in rows if bool(row.get(field)))
    return {
        "sample_size": len(rows),
        "hit_rate": hits / len(rows),
        "hit_count": hits,
        "miss_count": len(rows) - hits,
    }


def top5_to_top3_summary(reviewed: List[Dict[str, object]]) -> Dict[str, object]:
    top5_only = [row for row in reviewed if row.get("top5_hit") and not row.get("top3_hit")]
    labels = [label for row in reviewed for label in row.get("top5_to_top3_diagnosis", [])]
    btts_count = 0
    higher_total_count = 0
    bigger_margin_count = 0
    draw_count = 0
    for row in top5_only:
        try:
            home_text, away_text = str(row.get("actual_score", "0-0")).split("-", 1)
            actual_home = int(home_text)
            actual_away = int(away_text)
        except (TypeError, ValueError):
            continue
        if actual_home > 0 and actual_away > 0:
            btts_count += 1
        if actual_home == actual_away:
            draw_count += 1
        if actual_home + actual_away >= 3:
            higher_total_count += 1
        if abs(actual_home - actual_away) >= 2:
            bigger_margin_count += 1
    return {
        "top5_only_count": len(top5_only),
        "top5_only_rate": len(top5_only) / len(reviewed) if reviewed else 0.0,
        "top5_only_btts_count": btts_count,
        "top5_only_higher_total_count": higher_total_count,
        "top5_only_bigger_margin_count": bigger_margin_count,
        "top5_only_draw_count": draw_count,
        "diagnosis_counts": {label: labels.count(label) for label in sorted(set(labels))},
    }


def score_metric_summary(reviewed: List[Dict[str, object]], exact_hits: int, top3_hits: int, top5_hits: Optional[int] = None) -> Dict[str, object]:
    total_errors = [float(row["total_goals_error"]) for row in reviewed if row.get("total_goals_error") is not None]
    diff_errors = [float(row["goal_diff_error"]) for row in reviewed if row.get("goal_diff_error") is not None]
    labels = [label for row in reviewed for label in row.get("score_diagnosis", [])]
    top5_hit_count = top5_hits if top5_hits is not None else sum(1 for row in reviewed if row.get("top5_hit"))

    def label_rate(label: str) -> float:
        return labels.count(label) / len(reviewed) if reviewed else 0.0

    return {
        "exact_score_accuracy": (exact_hits / len(reviewed)) if reviewed else 0.0,
        "top3_hit_rate": (top3_hits / len(reviewed)) if reviewed else 0.0,
        "top5_hit_rate": (top5_hit_count / len(reviewed)) if reviewed else 0.0,
        "avg_total_goals_error": metric_summary(total_errors),
        "avg_abs_total_goals_error": metric_summary([abs(value) for value in total_errors]),
        "avg_goal_diff_error": metric_summary(diff_errors),
        "avg_abs_goal_diff_error": metric_summary([abs(value) for value in diff_errors]),
        "total_goals_direction": direction_hit_summary(reviewed, "total_goals_direction_hit"),
        "asian_handicap_direction": direction_hit_summary(reviewed, "asian_handicap_direction_hit"),
        "top5_to_top3": top5_to_top3_summary(reviewed),
        "underestimated_total_goals_rate": label_rate("underestimated_total_goals"),
        "overestimated_total_goals_rate": label_rate("overestimated_total_goals"),
        "underestimated_draw_rate": label_rate("underestimated_draw"),
        "underestimated_underdog_goal_rate": label_rate("underestimated_underdog_goal"),
        "underestimated_big_win_rate": label_rate("underestimated_big_win"),
        "wrong_goal_diff_direction_rate": label_rate("wrong_goal_diff_direction"),
        "score_distribution_too_narrow_rate": label_rate("score_distribution_too_narrow"),
    }


def score_metric_delta(before: Dict[str, object], after: Dict[str, object]) -> Dict[str, object]:
    keys = (
        "exact_score_accuracy",
        "top3_hit_rate",
        "top5_hit_rate",
        "avg_abs_total_goals_error",
        "avg_abs_goal_diff_error",
        "underestimated_total_goals_rate",
        "score_distribution_too_narrow_rate",
    )
    delta: Dict[str, object] = {}
    for key in keys:
        before_value = before.get(key)
        after_value = after.get(key)
        if isinstance(before_value, (int, float)) and isinstance(after_value, (int, float)):
            delta[key] = after_value - before_value
    return delta


def counterfactual_score_value(metrics: Dict[str, object]) -> float:
    exact = float(metrics.get("exact_score_accuracy", 0.0) or 0.0)
    top3 = float(metrics.get("top3_hit_rate", 0.0) or 0.0)
    top5 = float(metrics.get("top5_hit_rate", 0.0) or 0.0)
    total_mae = float(metrics.get("avg_abs_total_goals_error", 0.0) or 0.0)
    diff_mae = float(metrics.get("avg_abs_goal_diff_error", 0.0) or 0.0)
    return exact * 2.0 + top3 * 1.5 + top5 * 0.5 - total_mae * 0.15 - diff_mae * 0.15


def counterfactual_score_parameter_grid() -> List[Dict[str, float]]:
    rows: List[Dict[str, float]] = []
    for shrink in (0.0, 0.03, 0.06, 0.09):
        for btts in (0.0, 0.03, 0.05, 0.08):
            for high_total in (0.0, 0.02, 0.04):
                for threshold in (2.40, 2.55, 2.70):
                    for open_gap in (0.0, 0.92):
                        for open_direct in (0.0, 0.08):
                            rows.append(
                                {
                                    "score_goal_diff_shrink": shrink,
                                    "score_btts_promotion_weight": btts,
                                    "score_high_total_promotion_weight": high_total,
                                    "score_btts_total_threshold": threshold,
                                    "score_open_game_top5_gap_ratio": open_gap,
                                    "score_open_game_top5_direct_boost": open_direct,
                                }
                            )
    return rows


def counterfactual_grid_search(
    replay_inputs: List[Dict[str, object]],
    score_model: Optional[Dict[str, object]],
    strengths: Dict[str, float],
    prematch_news_index: Optional[Dict[Tuple[str, str, str], Dict[str, object]]],
    risk_config: Dict[str, float],
) -> Dict[str, object]:
    if not replay_inputs:
        return {"sample_size": 0, "best": None, "candidates": []}
    search_risk_config = dict(risk_config)
    search_risk_config["enable_monte_carlo"] = 0.0
    candidates: List[Dict[str, object]] = []
    for params in counterfactual_score_parameter_grid():
        rows: List[Dict[str, object]] = []
        exact_hits = top3_hits = top5_hits = 0
        for item in replay_inputs:
            prediction_row = item.get("prediction_row")
            if not isinstance(prediction_row, dict):
                continue
            actual_home = int(item.get("actual_home", 0))
            actual_away = int(item.get("actual_away", 0))
            model_probs = item.get("model_probs")
            if not isinstance(model_probs, tuple):
                model_probs = None
            prediction = rebuild_counterfactual_score_prediction(
                prediction_row,
                score_model,
                strengths,
                prematch_news_index,
                search_risk_config,
                score_param_overrides=params,
            )
            row = counterfactual_score_row(prediction_row, actual_home, actual_away, model_probs, prediction)
            if not row:
                continue
            exact_hits += 1 if row.get("exact_hit") else 0
            top3_hits += 1 if row.get("top3_hit") else 0
            top5_hits += 1 if row.get("top5_hit") else 0
            rows.append(row)
        metrics = score_metric_summary(rows, exact_hits, top3_hits, top5_hits)
        value = counterfactual_score_value(metrics)
        candidates.append({"params": params, "score_value": value, "score_metrics": metrics, "sample_size": len(rows)})
    candidates.sort(key=lambda row: float(row.get("score_value", -9999.0)), reverse=True)
    return {"sample_size": len(replay_inputs), "best": candidates[0] if candidates else None, "candidates": candidates[:10]}


def diagnosis_label_text(labels: object) -> str:
    if not isinstance(labels, list) or not labels:
        return "无明显比分偏差标签"
    return "、".join(DIAGNOSIS_LABELS.get(str(label), str(label)) for label in labels)


def mistake_tag_text(labels: object) -> str:
    if not isinstance(labels, list) or not labels:
        return "无明显赛果错因标签"
    return "、".join(MISTAKE_TAG_LABELS.get(str(label), str(label)) for label in labels)


def outcome_mistake_tags(
    outcome_idx: int,
    model_probs: Optional[Tuple[float, float, float]],
    base_probs: Optional[Tuple[float, float, float]] = None,
    market_probs: Optional[Tuple[float, float, float]] = None,
    score_top3_hit_value: bool = False,
    ai_adjustment: Optional[Dict[str, object]] = None,
) -> List[str]:
    tags: List[str] = []
    model_top = top_outcome_idx(model_probs)
    if model_top == outcome_idx:
        tags.append("wdl_hit")
    else:
        tags.append("wdl_miss")
    actual_prob = actual_probability(model_probs, outcome_idx)
    if actual_prob is not None:
        if actual_prob < 0.20:
            tags.append("very_low_actual_outcome_probability")
        elif actual_prob < 0.30:
            tags.append("low_actual_outcome_probability")
    if model_probs and model_top is not None and model_top != outcome_idx and model_probs[model_top] >= 0.50:
        tags.append("overconfident_wrong_pick")
    if model_probs and model_top == 0 and outcome_idx != 0 and model_probs[0] >= 0.45:
        tags.append("favorite_overestimated")
    if outcome_idx == 1 and model_top != 1:
        tags.append("draw_missed")
    if outcome_idx == 2 and model_top != 2:
        tags.append("away_or_underdog_missed")
    market_top = top_outcome_idx(market_probs)
    if model_top is not None and market_top is not None and model_top != market_top:
        tags.append("market_model_disagreement")
    if not score_top3_hit_value:
        tags.append("score_top3_miss")
    if base_probs and model_probs:
        delta = model_probs[outcome_idx] - base_probs[outcome_idx]
        if delta >= 0.005:
            tags.append("ai_adjustment_helped")
        elif delta <= -0.005:
            tags.append("ai_adjustment_hurt")
    adjustment = ai_adjustment or {}
    if adjustment.get("applied_weather"):
        tags.append("weather_adjustment_active")
    if adjustment.get("applied_tactical"):
        tags.append("tactical_adjustment_active")
    if adjustment.get("applied_value"):
        tags.append("value_adjustment_active")
    return tags


def mistake_tag_summary(reviewed: List[Dict[str, object]]) -> Dict[str, object]:
    labels = [label for row in reviewed for label in row.get("mistake_tags", [])]
    counts = {label: labels.count(label) for label in sorted(set(labels))}
    return {
        "sample_size": len(reviewed),
        "counts": counts,
        "rates": {label: (count / len(reviewed) if reviewed else 0.0) for label, count in counts.items()},
        "labels_cn": {label: MISTAKE_TAG_LABELS.get(label, label) for label in counts},
    }


def adjustment_module_metrics(reviewed: List[Dict[str, object]]) -> Dict[str, object]:
    modules = {
        "context": ("base_model_probabilities", "context_adjusted_probabilities", "context_or_ai_adjustment"),
        "tactical": ("context_adjusted_probabilities", "tactical_adjusted_probabilities", "applied_tactical"),
        "value": ("tactical_adjusted_probabilities", "value_adjusted_probabilities", "applied_value"),
        "weather": ("base_model_probabilities", "model_probabilities", "applied_weather"),
    }
    result: Dict[str, object] = {}
    for name, (before_field, after_field, applied_field) in modules.items():
        rows = []
        for row in reviewed:
            before = row.get(before_field)
            after = row.get(after_field)
            if not before or not after:
                continue
            if applied_field == "context_or_ai_adjustment":
                adjustment = row.get("ai_adjustment") or {}
                applied = bool(adjustment.get("applied")) if isinstance(adjustment, dict) else False
            else:
                applied = bool(row.get(applied_field))
            if not applied:
                continue
            rows.append(row)
        improved = 0
        worsened = 0
        unchanged = 0
        fixed = 0
        broke = 0
        changed_top = 0
        deltas: List[float] = []
        for row in rows:
            outcome_idx = int(row["actual_outcome_idx"])
            before = row[before_field]
            after = row[after_field]
            before_actual = before[outcome_idx]
            after_actual = after[outcome_idx]
            delta = after_actual - before_actual
            deltas.append(delta)
            if delta > 1e-9:
                improved += 1
            elif delta < -1e-9:
                worsened += 1
            else:
                unchanged += 1
            before_top = top_outcome_idx(before)
            after_top = top_outcome_idx(after)
            if before_top != after_top:
                changed_top += 1
            if before_top != outcome_idx and after_top == outcome_idx:
                fixed += 1
            if before_top == outcome_idx and after_top != outcome_idx:
                broke += 1
        result[name] = {
            "sample_size": len(rows),
            "improved_actual_probability": improved,
            "worsened_actual_probability": worsened,
            "unchanged_actual_probability": unchanged,
            "fixed_top_outcome": fixed,
            "broke_top_outcome": broke,
            "changed_top_outcome": changed_top,
            "avg_actual_probability_delta": metric_summary(deltas),
            "avg_abs_actual_probability_delta": metric_summary([abs(delta) for delta in deltas]),
        }
    return result


def odds_movement_summary_for_source(reviewed: List[Dict[str, object]], source_key: str) -> Dict[str, object]:
    rows = [row for row in reviewed if isinstance(row.get("normal_odds_change"), dict)]
    changes_list = [row["normal_odds_change"].get(source_key, [0.0, 0.0, 0.0]) for row in rows]
    return {
        "sample_size": len(rows),
        "home_shorter_count": sum(1 for change in changes_list if len(change) >= 1 and float(change[0] or 0.0) < 0),
        "draw_shorter_count": sum(1 for change in changes_list if len(change) >= 2 and float(change[1] or 0.0) < 0),
        "away_shorter_count": sum(1 for change in changes_list if len(change) >= 3 and float(change[2] or 0.0) < 0),
        "avg_home_delta": metric_summary([float(change[0] or 0.0) for change in changes_list if len(change) >= 1]),
        "avg_draw_delta": metric_summary([float(change[1] or 0.0) for change in changes_list if len(change) >= 2]),
        "avg_away_delta": metric_summary([float(change[2] or 0.0) for change in changes_list if len(change) >= 3]),
    }


def odds_movement_summary(reviewed: List[Dict[str, object]]) -> Dict[str, object]:
    return {
        "from_opening": odds_movement_summary_for_source(reviewed, "from_opening"),
        "from_previous": odds_movement_summary_for_source(reviewed, "from_previous"),
    }


def odds_outcome_cross_metrics_for_source(reviewed: List[Dict[str, object]], source_key: str) -> Dict[str, object]:
    result: Dict[str, object] = {}
    for idx, key in enumerate(("home", "draw", "away")):
        rows = []
        for row in reviewed:
            change_info = row.get("normal_odds_change")
            model_probs = row.get("model_probabilities")
            if not isinstance(change_info, dict) or not model_probs:
                continue
            changes = change_info.get(source_key, [0.0, 0.0, 0.0])
            if not isinstance(changes, list) or len(changes) <= idx:
                continue
            if float(changes[idx] or 0.0) < 0:
                rows.append(row)
        if not rows:
            result[key] = {"sample_size": 0, "actual_hit_rate": None, "model_pick_rate": None, "avg_actual_probability": None}
            continue
        actual_hits = sum(1 for row in rows if int(row.get("actual_outcome_idx", -1)) == idx)
        model_hits = sum(1 for row in rows if row.get("model_top_outcome") == OUTCOME_NAMES[idx])
        actual_probs = [float((row.get("model_probabilities") or [0.0, 0.0, 0.0])[idx]) for row in rows]
        result[key] = {
            "sample_size": len(rows),
            "actual_hit_rate": actual_hits / len(rows),
            "model_pick_rate": model_hits / len(rows),
            "avg_actual_probability": metric_summary(actual_probs),
        }
    return result


def odds_outcome_cross_metrics(reviewed: List[Dict[str, object]]) -> Dict[str, object]:
    return {
        "from_opening": odds_outcome_cross_metrics_for_source(reviewed, "from_opening"),
        "from_previous": odds_outcome_cross_metrics_for_source(reviewed, "from_previous"),
    }


def apply_weather_adjustment_tuning(config: Dict[str, object], summary: Dict[str, object]) -> str:
    config.setdefault("weather_adjustment_weight", 1.0)
    config.setdefault("weather_adjustment_max_delta", 0.02)
    module_metrics = summary.get("adjustment_module_metrics", {}) if isinstance(summary.get("adjustment_module_metrics"), dict) else {}
    weather_metrics = module_metrics.get("weather", {}) if isinstance(module_metrics.get("weather"), dict) else {}
    sample_size = int(weather_metrics.get("sample_size", 0) or 0)
    if sample_size < 3:
        return "missing_weather_review_sample_keep"

    improved = int(weather_metrics.get("improved_actual_probability", 0) or 0)
    worsened = int(weather_metrics.get("worsened_actual_probability", 0) or 0)
    fixed = int(weather_metrics.get("fixed_top_outcome", 0) or 0)
    broke = int(weather_metrics.get("broke_top_outcome", 0) or 0)
    avg_delta = float(weather_metrics.get("avg_actual_probability_delta", 0.0) or 0.0)
    current_weight = float(config.get("weather_adjustment_weight", 1.0) or 1.0)
    current_delta = float(config.get("weather_adjustment_max_delta", 0.02) or 0.02)

    if improved > worsened and fixed >= broke and avg_delta >= 0.003:
        config["weather_adjustment_weight"] = min(1.3, current_weight + 0.1)
        config["weather_adjustment_max_delta"] = min(0.03, current_delta + 0.003)
        return "weather_outperforms_expand"
    if worsened > improved and (broke > fixed or avg_delta <= -0.003):
        config["weather_adjustment_weight"] = max(0.7, current_weight - 0.1)
        config["weather_adjustment_max_delta"] = max(0.01, current_delta - 0.003)
        return "weather_underperforms_reduce"
    return "weather_matches_keep"


def probability_stats(reviewed: List[Dict[str, object]], field: str) -> Dict[str, object]:
    rows = [row for row in reviewed if row.get(field)]
    if not rows:
        return {"sample_size": 0, "accuracy": None, "brier": None, "logloss": None, "avg_actual_prob": None}
    hits = 0
    briers = []
    losses = []
    actual_probs = []
    for row in rows:
        probs = row[field]
        outcome_idx = int(row["actual_outcome_idx"])
        hits += 1 if max(range(3), key=lambda idx: probs[idx]) == outcome_idx else 0
        briers.append(brier(probs, outcome_idx))
        losses.append(logloss(probs, outcome_idx))
        actual_probs.append(probs[outcome_idx])
    return {
        "sample_size": len(rows),
        "accuracy": hits / len(rows),
        "brier": metric_summary(briers),
        "logloss": metric_summary(losses),
        "avg_actual_prob": metric_summary(actual_probs),
    }


def review_bucket(rows: List[Dict[str, object]]) -> Dict[str, object]:
    exact_hits = sum(1 for row in rows if row.get("exact_hit"))
    top3_hits = sum(1 for row in rows if row.get("top3_hit"))
    top5_hits = sum(1 for row in rows if row.get("top5_hit"))
    return {
        "sample_size": len(rows),
        "score_metrics": score_metric_summary(rows, exact_hits, top3_hits, top5_hits),
        "probability_metrics": {
            "base": probability_stats(rows, "base_model_probabilities"),
            "context": probability_stats(rows, "context_adjusted_probabilities"),
            "weather": probability_stats(rows, "weather_adjusted_probabilities"),
            "tactical": probability_stats(rows, "tactical_adjusted_probabilities"),
            "value": probability_stats(rows, "value_adjusted_probabilities"),
            "model": probability_stats(rows, "model_probabilities"),
            "market": probability_stats(rows, "market_probabilities"),
            "fused": probability_stats(rows, "fused_probabilities"),
        },
        "mistake_tag_metrics": mistake_tag_summary(rows),
        "adjustment_module_metrics": adjustment_module_metrics(rows),
        "odds_movement_metrics": odds_movement_summary(rows),
        "odds_outcome_cross_metrics": odds_outcome_cross_metrics(rows),
    }


def build_advice(summary: Dict[str, object], reviewed: List[Dict[str, object]]) -> List[str]:
    advice = []
    base_stats = summary["probability_metrics"].get("base", {})
    model_stats = summary["probability_metrics"]["model"]
    market_stats = summary["probability_metrics"].get("market", {})
    fused_stats = summary["probability_metrics"]["fused"]
    score_top3 = summary["score_metrics"]["top3_hit_rate"]

    if model_stats["sample_size"] and model_stats["avg_actual_prob"] is not None and model_stats["avg_actual_prob"] < 0.45:
        advice.append("真实赛果在模型分配的平均概率偏低，说明当前 WDL 概率校准偏乐观或方向偏差较大，应降低 Kelly 信号强度。")
    if base_stats.get("sample_size") and base_stats.get("brier") is not None and model_stats["brier"] is not None:
        if model_stats["brier"] < base_stats["brier"] - 1e-9:
            advice.append("AI 赛前情报修正后的 Brier 优于修正前模型，可继续保留默认开启。")
        elif model_stats["brier"] > base_stats["brier"] + 1e-9:
            advice.append("AI 赛前情报修正后的 Brier 暂未优于修正前模型，需控制修正幅度并继续观察。")
    if fused_stats["sample_size"] and fused_stats["brier"] is not None and model_stats["brier"] is not None:
        if fused_stats["brier"] > model_stats["brier"] + 1e-9:
            advice.append("融合概率 Brier 暂未优于模型概率，样本不足时不建议继续提高市场权重。")
        elif fused_stats["brier"] < model_stats["brier"] - 1e-9:
            advice.append("融合概率 Brier 优于模型概率，可继续保留当前 shrinkage 融合权重。")
        elif market_stats.get("sample_size"):
            advice.append("本轮部分场次有市场赔率，但所匹配赛前快照未记录融合概率，暂不能判断融合权重优劣。")
        else:
            advice.append("融合概率与模型概率表现一致，本轮没有市场赔率参与，暂不能判断融合权重优劣。")
    if score_top3 is not None and score_top3 < 0.35:
        advice.append("比分 Top3 命中率偏低，需复查 lambda 过强/过弱以及低比分重排规则。")
    score_metrics = summary.get("score_metrics", {}) if isinstance(summary.get("score_metrics"), dict) else {}
    total_direction = score_metrics.get("total_goals_direction", {}) if isinstance(score_metrics.get("total_goals_direction"), dict) else {}
    asian_direction = score_metrics.get("asian_handicap_direction", {}) if isinstance(score_metrics.get("asian_handicap_direction"), dict) else {}
    if total_direction.get("sample_size") and total_direction.get("hit_rate") is not None and float(total_direction.get("hit_rate", 0.0)) < 0.45:
        advice.append("大小球方向命中率偏低，应优先检查总进球 lambda 与大小球盘口融合规则。")
    if asian_direction.get("sample_size") and asian_direction.get("hit_rate") is not None and float(asian_direction.get("hit_rate", 0.0)) < 0.45:
        advice.append("亚盘方向命中率偏低，应优先检查净胜球差 lambda 与让球盘口融合规则。")
    if score_metrics.get("underestimated_total_goals_rate", 0.0) >= 0.25:
        advice.append("低估总进球的场次占比较高，应检查总进球 lambda 和大比分尾部权重。")
    if score_metrics.get("underestimated_draw_rate", 0.0) >= 0.25:
        advice.append("实际平局但模型主方向非平局的场次偏多，应继续观察并可能提高平局保护。")
    if score_metrics.get("underestimated_big_win_rate", 0.0) >= 0.20:
        advice.append("强队大胜或客队大胜尾部覆盖不足，应让深盘/强弱差更明显地影响比分尾部分布。")
    mistake_metrics = summary.get("mistake_tag_metrics", {}) if isinstance(summary.get("mistake_tag_metrics"), dict) else {}
    mistake_rates = mistake_metrics.get("rates", {}) if isinstance(mistake_metrics.get("rates"), dict) else {}
    module_metrics = summary.get("adjustment_module_metrics", {}) if isinstance(summary.get("adjustment_module_metrics"), dict) else {}
    odds_metrics_all = summary.get("odds_movement_metrics", {}) if isinstance(summary.get("odds_movement_metrics"), dict) else {}
    odds_cross_all = summary.get("odds_outcome_cross_metrics", {}) if isinstance(summary.get("odds_outcome_cross_metrics"), dict) else {}
    odds_metrics = odds_metrics_all.get("from_opening", {}) if isinstance(odds_metrics_all.get("from_opening"), dict) else {}
    odds_cross = odds_cross_all.get("from_opening", {}) if isinstance(odds_cross_all.get("from_opening"), dict) else {}
    if mistake_rates.get("overconfident_wrong_pick", 0.0) >= 0.20:
        advice.append("高置信错误方向占比较高，建议降低强队方向集中度并提高冷门/平局尾部保护。")
    if mistake_rates.get("ai_adjustment_hurt", 0.0) > mistake_rates.get("ai_adjustment_helped", 0.0):
        advice.append("AI 修正降低真实方向概率的场次多于提高场次，应收紧赛前情报修正上限并复查触发原因。")
    if odds_metrics.get("sample_size") and odds_metrics.get("home_shorter_count", 0) >= max(2, len(reviewed) // 3):
        advice.append("主胜赔率临近比赛前被持续压低的场次较多，后续应重点观察强队方向是否被市场提前定价。")
    home_cross = odds_cross.get("home", {}) if isinstance(odds_cross.get("home"), dict) else {}
    if home_cross.get("sample_size") and home_cross.get("actual_hit_rate") is not None and home_cross.get("actual_hit_rate", 0.0) >= 0.6:
        advice.append("主胜赔率走低的场次里主胜命中率偏高，可继续观察市场压低主胜时是否值得提高强队方向关注度。")
    for module_name, label in (("weather", "天气"), ("tactical", "战术"), ("value", "身价/阵容价值")):
        module = module_metrics.get(module_name, {}) if isinstance(module_metrics.get(module_name), dict) else {}
        if module.get("sample_size") and module.get("worsened_actual_probability", 0) > module.get("improved_actual_probability", 0):
            advice.append(f"{label}修正降低真实方向概率的样本更多，后续应降低该模块权重或提高触发阈值。")
    upsets = [row for row in reviewed if row.get("model_probabilities") and row["model_probabilities"][int(row["actual_outcome_idx"])] < 0.25]
    if len(upsets) >= max(2, len(reviewed) // 3):
        advice.append("冷门或模型低估方向占比较高，建议在下一轮预测中降低强队方向集中度，并提高平局/客胜尾部概率。")
    if not advice:
        advice.append("样本量仍偏小，先记录校准指标，不做激进参数调整。")
    return advice


def update_calibration_config(summary: Dict[str, object]) -> None:
    if CALIBRATION_PATH.exists():
        config = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
    else:
        config = {
            "model_weight": 0.4,
            "kelly_fraction": 0.25,
            "min_edge": 0.05,
            "bankroll": 10.0,
            "min_stake": 2.0,
            "max_stake_per_pick": 5.0,
            "max_total_stake": 15.0,
        }

    snapshot_metrics = summary.get("snapshot_metrics", {}) if isinstance(summary.get("snapshot_metrics"), dict) else {}
    combined_metrics = summary.get("combined_metrics", {}) if isinstance(summary.get("combined_metrics"), dict) else {}
    probability_metrics = snapshot_metrics.get("probability_metrics") if isinstance(snapshot_metrics.get("probability_metrics"), dict) else summary["probability_metrics"]
    model_stats = probability_metrics["model"]
    base_stats = probability_metrics.get("base", {})
    context_stats = probability_metrics.get("context", {})
    weather_stats = probability_metrics.get("weather", {})
    tactical_stats = probability_metrics.get("tactical", {})
    value_stats = probability_metrics.get("value", {})
    market_stats = probability_metrics["market"]
    fused_stats = probability_metrics["fused"]
    full_playback = summary.get("full_playback", {}) if isinstance(summary.get("full_playback"), dict) else {}
    full_model_stats = full_playback.get("probability_metrics", {}).get("model", {}) if isinstance(full_playback.get("probability_metrics"), dict) else {}
    full_score_metrics = full_playback.get("score_metrics", {}) if isinstance(full_playback.get("score_metrics"), dict) else {}
    sample_size = int(summary.get("matched_snapshot_matches", 0) or 0)
    combined_score_metrics = combined_metrics.get("score_metrics", summary["score_metrics"]) if isinstance(combined_metrics, dict) else summary["score_metrics"]
    config.setdefault("draw_probability_floor", 0.22)
    config.setdefault("balanced_match_draw_floor", 0.26)
    config.setdefault("strong_favorite_draw_floor", 0.25)
    config.setdefault("max_draw_calibration_boost", 0.08)
    config.setdefault("underdog_probability_floor", 0.18)
    config.setdefault("max_underdog_calibration_boost", 0.03)
    config.setdefault("enable_ai_probability_adjustment", 1.0)
    config.setdefault("ai_probability_max_delta", 0.03)
    config.setdefault("ai_probability_high_confidence_delta", 0.05)
    config.setdefault("weather_adjustment_weight", 1.0)
    config.setdefault("weather_adjustment_max_delta", 0.02)
    config.setdefault("auto_apply_review_probability_params", 0.0)
    config.setdefault("auto_apply_review_score_params", 0.0)
    config.setdefault("auto_apply_requires_joint_backtest", 1.0)

    config["calibration"] = {
        "sample_size": sample_size,
        "combined_sample_size": int(summary["reviewed_matches"]),
        "base_model_accuracy": base_stats.get("accuracy"),
        "context_model_accuracy": context_stats.get("accuracy"),
        "weather_model_accuracy": weather_stats.get("accuracy"),
        "tactical_model_accuracy": tactical_stats.get("accuracy"),
        "value_model_accuracy": value_stats.get("accuracy"),
        "model_accuracy": model_stats["accuracy"],
        "market_accuracy": market_stats["accuracy"],
        "fused_accuracy": fused_stats["accuracy"],
        "model_brier": model_stats["brier"],
        "base_model_brier": base_stats.get("brier"),
        "context_model_brier": context_stats.get("brier"),
        "weather_model_brier": weather_stats.get("brier"),
        "tactical_model_brier": tactical_stats.get("brier"),
        "value_model_brier": value_stats.get("brier"),
        "market_brier": market_stats["brier"],
        "fused_brier": fused_stats["brier"],
        "base_model_logloss": base_stats.get("logloss"),
        "context_model_logloss": context_stats.get("logloss"),
        "weather_model_logloss": weather_stats.get("logloss"),
        "tactical_model_logloss": tactical_stats.get("logloss"),
        "value_model_logloss": value_stats.get("logloss"),
        "model_logloss": model_stats["logloss"],
        "market_logloss": market_stats["logloss"],
        "fused_logloss": fused_stats["logloss"],
        "score_exact_accuracy": combined_score_metrics["exact_score_accuracy"],
        "score_top3_hit_rate": combined_score_metrics["top3_hit_rate"],
        "score_top5_hit_rate": combined_score_metrics.get("top5_hit_rate"),
        "avg_total_goals_error": combined_score_metrics.get("avg_total_goals_error"),
        "avg_abs_total_goals_error": combined_score_metrics.get("avg_abs_total_goals_error"),
        "avg_goal_diff_error": combined_score_metrics.get("avg_goal_diff_error"),
        "avg_abs_goal_diff_error": combined_score_metrics.get("avg_abs_goal_diff_error"),
        "total_goals_direction_metrics": combined_score_metrics.get("total_goals_direction"),
        "asian_handicap_direction_metrics": combined_score_metrics.get("asian_handicap_direction"),
        "underestimated_total_goals_rate": combined_score_metrics.get("underestimated_total_goals_rate"),
        "underestimated_draw_rate": combined_score_metrics.get("underestimated_draw_rate"),
        "underestimated_underdog_goal_rate": combined_score_metrics.get("underestimated_underdog_goal_rate"),
        "underestimated_big_win_rate": combined_score_metrics.get("underestimated_big_win_rate"),
        "mistake_tag_metrics": snapshot_metrics.get("mistake_tag_metrics") if isinstance(snapshot_metrics, dict) else summary.get("mistake_tag_metrics"),
        "adjustment_module_metrics": snapshot_metrics.get("adjustment_module_metrics") if isinstance(snapshot_metrics, dict) else summary.get("adjustment_module_metrics"),
        "last_review_file": summary["review_file"],
    }
    if full_playback:
        config["calibration"]["full_playback_sample_size"] = full_playback.get("sample_size")
        config["calibration"]["full_playback_model_accuracy"] = full_model_stats.get("accuracy")
        config["calibration"]["full_playback_model_brier"] = full_model_stats.get("brier")
        config["calibration"]["full_playback_model_logloss"] = full_model_stats.get("logloss")
        config["calibration"]["full_playback_score_top3_hit_rate"] = full_score_metrics.get("top3_hit_rate")
        config["calibration"]["full_playback_score_top5_hit_rate"] = full_score_metrics.get("top5_hit_rate")

    if sample_size < 100:
        config["model_weight"] = config.get("model_weight", 0.4)
        config["calibration"]["model_weight_decision"] = "sample_size_below_100_keep_current_weight"
    elif model_stats["brier"] is not None and market_stats["brier"] is not None:
        current = float(config.get("model_weight", 0.4))
        if model_stats["brier"] < market_stats["brier"]:
            config["model_weight"] = min(0.6, current + 0.05)
        else:
            config["model_weight"] = max(0.2, current - 0.05)
        config["calibration"]["model_weight_decision"] = "adjusted_by_brier_score"

    if base_stats.get("sample_size") and base_stats.get("brier") is not None and model_stats.get("brier") is not None:
        ai_delta = float(config.get("ai_probability_max_delta", 0.03))
        ai_high_delta = float(config.get("ai_probability_high_confidence_delta", 0.05))
        if model_stats["brier"] < base_stats["brier"] - 0.005:
            config["ai_probability_max_delta"] = min(0.05, ai_delta + 0.005)
            config["ai_probability_high_confidence_delta"] = min(0.07, ai_high_delta + 0.005)
            config["calibration"]["ai_adjustment_decision"] = "ai_outperforms_base_expand_delta"
        elif model_stats["brier"] > base_stats["brier"] + 0.005:
            config["ai_probability_max_delta"] = max(0.015, ai_delta - 0.005)
            config["ai_probability_high_confidence_delta"] = max(0.025, ai_high_delta - 0.005)
            config["calibration"]["ai_adjustment_decision"] = "ai_underperforms_base_reduce_delta"
        else:
            config["calibration"]["ai_adjustment_decision"] = "ai_matches_base_keep_delta"
    else:
        config["calibration"]["ai_adjustment_decision"] = "missing_ai_review_sample_keep_delta"

    if tactical_stats.get("sample_size") and context_stats.get("brier") is not None and tactical_stats.get("brier") is not None:
        tactical_delta = float(config.get("tactical_adjustment_max_delta", 0.02))
        config.setdefault("tactical_adjustment_weight", 1.0)
        config.setdefault("tactical_adjustment_max_delta", 0.02)
        if tactical_stats["brier"] < context_stats["brier"] - 0.005:
            config["tactical_adjustment_weight"] = min(1.3, float(config.get("tactical_adjustment_weight", 1.0)) + 0.1)
            config["tactical_adjustment_max_delta"] = min(0.03, tactical_delta + 0.003)
            config["calibration"]["tactical_adjustment_decision"] = "tactical_outperforms_context_expand"
        elif tactical_stats["brier"] > context_stats["brier"] + 0.005:
            config["tactical_adjustment_weight"] = max(0.7, float(config.get("tactical_adjustment_weight", 1.0)) - 0.1)
            config["tactical_adjustment_max_delta"] = max(0.01, tactical_delta - 0.003)
            config["calibration"]["tactical_adjustment_decision"] = "tactical_underperforms_context_reduce"
        else:
            config["calibration"]["tactical_adjustment_decision"] = "tactical_matches_context_keep"
    else:
        config.setdefault("tactical_adjustment_weight", 1.0)
        config.setdefault("tactical_adjustment_max_delta", 0.02)
        config["calibration"]["tactical_adjustment_decision"] = "missing_tactical_review_sample_keep"

    config.setdefault("value_adjustment_weight", 1.0)
    config.setdefault("value_adjustment_max_delta", 0.025)
    if value_stats.get("sample_size") and tactical_stats.get("brier") is not None and value_stats.get("brier") is not None:
        value_delta = float(config.get("value_adjustment_max_delta", 0.025))
        if value_stats["brier"] < tactical_stats["brier"] - 0.005:
            config["value_adjustment_weight"] = min(1.3, float(config.get("value_adjustment_weight", 1.0)) + 0.1)
            config["value_adjustment_max_delta"] = min(0.035, value_delta + 0.003)
            config["calibration"]["value_adjustment_decision"] = "value_outperforms_tactical_expand"
        elif value_stats["brier"] > tactical_stats["brier"] + 0.005:
            config["value_adjustment_weight"] = max(0.7, float(config.get("value_adjustment_weight", 1.0)) - 0.1)
            config["value_adjustment_max_delta"] = max(0.01, value_delta - 0.003)
            config["calibration"]["value_adjustment_decision"] = "value_underperforms_tactical_reduce"
        else:
            config["calibration"]["value_adjustment_decision"] = "value_matches_tactical_keep"
    else:
        config["calibration"]["value_adjustment_decision"] = "missing_value_review_sample_keep"

    config["calibration"]["weather_adjustment_decision"] = apply_weather_adjustment_tuning(config, summary)

    avg_actual_prob = model_stats.get("avg_actual_prob")
    top3_rate = summary["score_metrics"].get("top3_hit_rate")
    if sample_size > 0 and ((avg_actual_prob is not None and avg_actual_prob < 0.45) or (top3_rate is not None and top3_rate < 0.35)):
        config["kelly_fraction"] = min(float(config.get("kelly_fraction", 0.25)), 0.2)
        config["min_edge"] = max(float(config.get("min_edge", 0.05)), 0.07)
        config["calibration"]["risk_adjustment_decision"] = "poor_short_sample_accuracy_reduce_kelly_raise_edge_keep_manual_total_stake"
    else:
        config["calibration"]["risk_adjustment_decision"] = "keep_current_risk_settings"

    full_sample_size = int(full_playback.get("sample_size", 0) or 0) if full_playback else 0
    candidate_probability_params: Optional[Dict[str, float]] = None
    if full_sample_size >= 10:
        draw_rows = [row for row in summary.get("full_playback_matches", []) if int(row.get("actual_outcome_idx", -1)) == 1 and row.get("model_probabilities")]
        low_draw_hits = [row for row in draw_rows if row["model_probabilities"][1] < 0.25]
        if draw_rows and len(low_draw_hits) / len(draw_rows) >= 0.4:
            candidate_probability_params = {
                "draw_probability_floor": min(0.26, max(float(config.get("draw_probability_floor", 0.22)), 0.23)),
                "balanced_match_draw_floor": min(0.29, max(float(config.get("balanced_match_draw_floor", 0.26)), 0.27)),
                "strong_favorite_draw_floor": min(0.28, max(float(config.get("strong_favorite_draw_floor", 0.25)), 0.26)),
                "max_draw_calibration_boost": min(0.10, max(float(config.get("max_draw_calibration_boost", 0.08)), 0.09)),
                "underdog_probability_floor": float(config.get("underdog_probability_floor", 0.18) or 0.18),
                "max_underdog_calibration_boost": float(config.get("max_underdog_calibration_boost", 0.03) or 0.03),
                "probability_temperature": float(config.get("probability_temperature", 1.0) or 1.0),
                "draw_pick_override_enabled": float(config.get("draw_pick_override_enabled", 1.0) or 1.0),
                "draw_pick_min_probability": float(config.get("draw_pick_min_probability", 0.26) or 0.26),
                "draw_pick_max_gap_to_favorite": float(config.get("draw_pick_max_gap_to_favorite", 0.15) or 0.15),
                "draw_pick_favorite_max_probability": float(config.get("draw_pick_favorite_max_probability", 0.50) or 0.50),
            }
            config["calibration"]["probability_parameter_decision"] = "full_playback_generates_probability_candidate"
        else:
            config["calibration"]["probability_parameter_decision"] = "full_playback_keep_probability_parameters"
    elif full_playback:
        config["calibration"]["probability_parameter_decision"] = "full_playback_sample_below_10_keep_parameters"

    candidate_score_params: Optional[Dict[str, float]] = None
    best_grid = summary.get("counterfactual_grid_search", {}).get("best") if isinstance(summary.get("counterfactual_grid_search"), dict) else None
    if isinstance(best_grid, dict) and isinstance(best_grid.get("params"), dict):
        params = best_grid["params"]
        candidate_score_params = {
            "score_goal_diff_shrink": float(params.get("score_goal_diff_shrink", config.get("score_goal_diff_shrink", 0.0)) or 0.0),
            "score_btts_promotion_weight": float(params.get("score_btts_promotion_weight", config.get("score_btts_promotion_weight", 0.0)) or 0.0),
            "score_high_total_promotion_weight": float(params.get("score_high_total_promotion_weight", config.get("score_high_total_promotion_weight", 0.0)) or 0.0),
            "score_btts_total_threshold": float(params.get("score_btts_total_threshold", config.get("score_btts_total_threshold", 2.55)) or 2.55),
            "score_common_result_boost": float(config.get("score_common_result_boost", 0.0) or 0.0),
            "score_draw_candidate_boost": float(config.get("score_draw_candidate_boost", 0.0) or 0.0),
            "score_top5_to_top3_btts_boost": float(config.get("score_top5_to_top3_btts_boost", 0.0) or 0.0),
            "score_top5_to_top3_high_total_boost": float(config.get("score_top5_to_top3_high_total_boost", 0.0) or 0.0),
            "score_open_game_top5_gap_ratio": float(params.get("score_open_game_top5_gap_ratio", config.get("score_open_game_top5_gap_ratio", 0.0)) or 0.0),
            "score_open_game_top5_direct_boost": float(params.get("score_open_game_top5_direct_boost", config.get("score_open_game_top5_direct_boost", 0.0)) or 0.0),
            "score_big_margin_tail_boost": float(config.get("score_big_margin_tail_boost", 0.0) or 0.0),
        }

    config["calibration"]["candidate_probability_params"] = candidate_probability_params
    config["calibration"]["candidate_score_params"] = candidate_score_params
    config["calibration"]["auto_apply_decision"] = "hold_requires_backtest_confirmation"
    if float(config.get("auto_apply_review_probability_params", 0.0) or 0.0) > 0 and float(config.get("auto_apply_requires_joint_backtest", 1.0) or 1.0) <= 0 and candidate_probability_params:
        config.update(candidate_probability_params)
        config["calibration"]["auto_apply_decision"] = "applied_review_probability_candidate"
    if float(config.get("auto_apply_review_score_params", 0.0) or 0.0) > 0 and float(config.get("auto_apply_requires_joint_backtest", 1.0) or 1.0) <= 0 and candidate_score_params:
        config.update(candidate_score_params)
        config["calibration"]["auto_apply_decision"] = "applied_review_score_candidate"

    CALIBRATION_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["risk_config"] = {
        "kelly_fraction": config.get("kelly_fraction"),
        "min_edge": config.get("min_edge"),
        "bankroll": config.get("bankroll"),
        "max_stake_per_pick": config.get("max_stake_per_pick"),
        "max_total_stake": config.get("max_total_stake"),
        "ai_probability_max_delta": config.get("ai_probability_max_delta"),
        "ai_probability_high_confidence_delta": config.get("ai_probability_high_confidence_delta"),
        "tactical_adjustment_weight": config.get("tactical_adjustment_weight"),
        "tactical_adjustment_max_delta": config.get("tactical_adjustment_max_delta"),
        "value_adjustment_weight": config.get("value_adjustment_weight"),
        "value_adjustment_max_delta": config.get("value_adjustment_max_delta"),
        "weather_adjustment_weight": config.get("weather_adjustment_weight"),
        "weather_adjustment_max_delta": config.get("weather_adjustment_max_delta"),
    }
    summary["calibration_ai_decision"] = config["calibration"].get("ai_adjustment_decision")
    summary["calibration_tactical_decision"] = config["calibration"].get("tactical_adjustment_decision")
    summary["calibration_value_decision"] = config["calibration"].get("value_adjustment_decision")
    summary["calibration_weather_decision"] = config["calibration"].get("weather_adjustment_decision")


def write_reflection_report(summary: Dict[str, object]) -> Path:
    path = REVIEW_DOCS_DIR / summary["review_file"].replace("postmatch-calibration_", "postmatch-reflection_").replace(".json", ".md")
    model_stats = summary["probability_metrics"]["model"]
    fused_stats = summary["probability_metrics"]["fused"]
    weather_stats = summary["probability_metrics"].get("weather", {})
    tactical_stats = summary["probability_metrics"].get("tactical", {})
    value_stats = summary["probability_metrics"].get("value", {})
    snapshot_metrics = summary.get("snapshot_metrics", {}) if isinstance(summary.get("snapshot_metrics"), dict) else {}
    playback_proxy_metrics = summary.get("playback_proxy_metrics", {}) if isinstance(summary.get("playback_proxy_metrics"), dict) else {}
    combined_metrics = summary.get("combined_metrics", {}) if isinstance(summary.get("combined_metrics"), dict) else {}
    snapshot_model = snapshot_metrics.get("probability_metrics", {}).get("model", {}) if isinstance(snapshot_metrics.get("probability_metrics"), dict) else {}
    snapshot_score = snapshot_metrics.get("score_metrics", {}) if isinstance(snapshot_metrics.get("score_metrics"), dict) else {}
    proxy_model = playback_proxy_metrics.get("probability_metrics", {}).get("model", {}) if isinstance(playback_proxy_metrics.get("probability_metrics"), dict) else {}
    proxy_score = playback_proxy_metrics.get("score_metrics", {}) if isinstance(playback_proxy_metrics.get("score_metrics"), dict) else {}
    combined_model = combined_metrics.get("probability_metrics", {}).get("model", {}) if isinstance(combined_metrics.get("probability_metrics"), dict) else {}
    combined_score = combined_metrics.get("score_metrics", {}) if isinstance(combined_metrics.get("score_metrics"), dict) else {}
    lines = [
        "# 赛后准确率校准与反思",
        "",
        f"生成时间：{summary['generated_at']}",
        "",
        "## 1. 本轮校准结果",
        "",
        f"- 扫描预测快照：{summary['snapshots_scanned']} 个",
        f"- 加载赛果：{summary['results_loaded']} 场",
        f"- 赛前快照直接匹配：{summary.get('matched_snapshot_matches', summary['reviewed_matches'])} 场",
        f"- 全量回放代理补齐：{summary.get('playback_proxy_matches', 0)} 场",
        f"- 最终复盘覆盖：{summary['reviewed_matches']} 场",
        f"- 比分 Exact：{summary['score_metrics']['exact_score_accuracy']:.1%}",
        f"- 比分 Top3：{summary['score_metrics']['top3_hit_rate']:.1%}",
        f"- 比分 Top5：{summary['score_metrics']['top5_hit_rate']:.1%}",
        f"- 反事实重算 Top3：{summary.get('counterfactual_replay', {}).get('score_metrics', {}).get('top3_hit_rate'):.1%}" if summary.get('counterfactual_replay', {}).get('score_metrics', {}).get('top3_hit_rate') is not None else "- 反事实重算 Top3：暂无",
        f"- 反事实重算 Top5：{summary.get('counterfactual_replay', {}).get('score_metrics', {}).get('top5_hit_rate'):.1%}" if summary.get('counterfactual_replay', {}).get('score_metrics', {}).get('top5_hit_rate') is not None else "- 反事实重算 Top5：暂无",
        f"- WDL 模型方向准确率：{model_stats['accuracy']:.1%}" if model_stats["accuracy"] is not None else "- WDL 模型方向准确率：暂无",
        f"- AI 修正前 WDL 准确率：{summary['probability_metrics']['base']['accuracy']:.1%}" if summary['probability_metrics']['base']['accuracy'] is not None else "- AI 修正前 WDL 准确率：暂无",
        f"- 模型 Brier：{model_stats['brier']:.4f}" if model_stats["brier"] is not None else "- 模型 Brier：暂无",
        f"- AI 修正前 Brier：{summary['probability_metrics']['base']['brier']:.4f}" if summary['probability_metrics']['base']['brier'] is not None else "- AI 修正前 Brier：暂无",
        f"- 融合 Brier：{fused_stats['brier']:.4f}" if fused_stats["brier"] is not None else "- 融合 Brier：暂无",
        "",
        "## 1.A 三套口径摘要",
        "",
        f"- 真实快照：样本 {snapshot_metrics.get('sample_size', 0)} 场，WDL {float(snapshot_model.get('accuracy', 0.0) or 0.0):.1%}，Top3 {float(snapshot_score.get('top3_hit_rate', 0.0) or 0.0):.1%}，Top5 {float(snapshot_score.get('top5_hit_rate', 0.0) or 0.0):.1%}。",
        f"- 代理回放：样本 {playback_proxy_metrics.get('sample_size', 0)} 场，WDL {float(proxy_model.get('accuracy', 0.0) or 0.0):.1%}，Top3 {float(proxy_score.get('top3_hit_rate', 0.0) or 0.0):.1%}，Top5 {float(proxy_score.get('top5_hit_rate', 0.0) or 0.0):.1%}。",
        f"- 合并口径：样本 {combined_metrics.get('sample_size', 0)} 场，WDL {float(combined_model.get('accuracy', 0.0) or 0.0):.1%}，Top3 {float(combined_score.get('top3_hit_rate', 0.0) or 0.0):.1%}，Top5 {float(combined_score.get('top5_hit_rate', 0.0) or 0.0):.1%}。",
        "",
        "## 1.0 模块独立概率指标",
        "",
        f"- 天气修正：准确率 {weather_stats.get('accuracy'):.1%}，Brier {weather_stats.get('brier'):.4f}，logloss {weather_stats.get('logloss'):.4f}" if weather_stats.get("accuracy") is not None and weather_stats.get("brier") is not None and weather_stats.get("logloss") is not None else "- 天气修正：暂无独立样本。",
        f"- 战术修正：准确率 {tactical_stats.get('accuracy'):.1%}，Brier {tactical_stats.get('brier'):.4f}，logloss {tactical_stats.get('logloss'):.4f}" if tactical_stats.get("accuracy") is not None and tactical_stats.get("brier") is not None and tactical_stats.get("logloss") is not None else "- 战术修正：暂无独立样本。",
        f"- 身价/阵容价值修正：准确率 {value_stats.get('accuracy'):.1%}，Brier {value_stats.get('brier'):.4f}，logloss {value_stats.get('logloss'):.4f}" if value_stats.get("accuracy") is not None and value_stats.get("brier") is not None and value_stats.get("logloss") is not None else "- 身价/阵容价值修正：暂无独立样本。",
        "",
        "## 1.1 全量已完赛回放校准",
        "",
        f"- 回放样本：{summary.get('full_playback', {}).get('sample_size', 0)} 场（使用当前模型回放，非真实赛前快照）。",
        f"- 回放 WDL 准确率：{summary.get('full_playback', {}).get('probability_metrics', {}).get('model', {}).get('accuracy'):.1%}" if summary.get('full_playback', {}).get('probability_metrics', {}).get('model', {}).get('accuracy') is not None else "- 回放 WDL 准确率：暂无",
        f"- 回放比分 Top3：{summary.get('full_playback', {}).get('score_metrics', {}).get('top3_hit_rate'):.1%}" if summary.get('full_playback', {}).get('score_metrics', {}).get('top3_hit_rate') is not None else "- 回放比分 Top3：暂无",
        f"- 回放比分 Top5：{summary.get('full_playback', {}).get('score_metrics', {}).get('top5_hit_rate'):.1%}" if summary.get('full_playback', {}).get('score_metrics', {}).get('top5_hit_rate') is not None else "- 回放比分 Top5：暂无",
        "",
        "## 1.2 比分诊断",
        "",
        f"- 平均总进球误差：{summary['score_metrics'].get('avg_total_goals_error'):.2f}" if summary['score_metrics'].get('avg_total_goals_error') is not None else "- 平均总进球误差：暂无",
        f"- 平均总进球绝对误差：{summary['score_metrics'].get('avg_abs_total_goals_error'):.2f}" if summary['score_metrics'].get('avg_abs_total_goals_error') is not None else "- 平均总进球绝对误差：暂无",
        f"- 平均净胜球误差：{summary['score_metrics'].get('avg_goal_diff_error'):.2f}" if summary['score_metrics'].get('avg_goal_diff_error') is not None else "- 平均净胜球误差：暂无",
        f"- 平均净胜球绝对误差：{summary['score_metrics'].get('avg_abs_goal_diff_error'):.2f}" if summary['score_metrics'].get('avg_abs_goal_diff_error') is not None else "- 平均净胜球绝对误差：暂无",
        f"- 大小球方向命中：{summary['score_metrics'].get('total_goals_direction', {}).get('hit_rate'):.1%}（样本 {summary['score_metrics'].get('total_goals_direction', {}).get('sample_size')}）" if summary['score_metrics'].get('total_goals_direction', {}).get('hit_rate') is not None else "- 大小球方向命中：暂无样本",
        f"- 亚盘方向命中：{summary['score_metrics'].get('asian_handicap_direction', {}).get('hit_rate'):.1%}（样本 {summary['score_metrics'].get('asian_handicap_direction', {}).get('sample_size')}）" if summary['score_metrics'].get('asian_handicap_direction', {}).get('hit_rate') is not None else "- 亚盘方向命中：暂无样本",
        f"- 反事实 Top3 变化：{summary.get('counterfactual_replay', {}).get('score_metric_delta_vs_snapshot', {}).get('top3_hit_rate'):+.1%}" if summary.get('counterfactual_replay', {}).get('score_metric_delta_vs_snapshot', {}).get('top3_hit_rate') is not None else "- 反事实 Top3 变化：暂无",
        f"- 反事实总进球 MAE 变化：{summary.get('counterfactual_replay', {}).get('score_metric_delta_vs_snapshot', {}).get('avg_abs_total_goals_error'):+.2f}" if summary.get('counterfactual_replay', {}).get('score_metric_delta_vs_snapshot', {}).get('avg_abs_total_goals_error') is not None else "- 反事实总进球 MAE 变化：暂无",
        f"- 反事实参数搜索最佳 Top3：{summary.get('counterfactual_grid_search', {}).get('best', {}).get('score_metrics', {}).get('top3_hit_rate'):.1%}" if summary.get('counterfactual_grid_search', {}).get('best', {}).get('score_metrics', {}).get('top3_hit_rate') is not None else "- 反事实参数搜索最佳 Top3：暂无",
        f"- 低估总进球：{summary['score_metrics'].get('underestimated_total_goals_rate', 0.0):.1%}",
        f"- 高估总进球：{summary['score_metrics'].get('overestimated_total_goals_rate', 0.0):.1%}",
        f"- 低估平局：{summary['score_metrics'].get('underestimated_draw_rate', 0.0):.1%}",
        f"- 低估弱队进球：{summary['score_metrics'].get('underestimated_underdog_goal_rate', 0.0):.1%}",
        f"- 低估大胜：{summary['score_metrics'].get('underestimated_big_win_rate', 0.0):.1%}",
        "",
        "## 1.3 赛果错因标签",
        "",
    ]
    mistake_metrics = summary.get("mistake_tag_metrics", {}) if isinstance(summary.get("mistake_tag_metrics"), dict) else {}
    mistake_counts = mistake_metrics.get("counts", {}) if isinstance(mistake_metrics.get("counts"), dict) else {}
    mistake_rates = mistake_metrics.get("rates", {}) if isinstance(mistake_metrics.get("rates"), dict) else {}
    if mistake_counts:
        for label, count in sorted(mistake_counts.items(), key=lambda item: (-int(item[1]), str(item[0])))[:8]:
            lines.append(f"- {MISTAKE_TAG_LABELS.get(str(label), str(label))}：{count} 场，占比 {float(mistake_rates.get(label, 0.0)):.1%}")
    else:
        lines.append("- 暂无赛果错因标签。")
    lines.extend(
        [
            "",
            "## 1.4 修正模块效果",
            "",
        ]
    )
    module_metrics = summary.get("adjustment_module_metrics", {}) if isinstance(summary.get("adjustment_module_metrics"), dict) else {}
    module_labels = {"context": "赛前情报", "tactical": "战术", "value": "身价/阵容价值", "weather": "天气"}
    for module_name in ("context", "tactical", "value", "weather"):
        module = module_metrics.get(module_name, {}) if isinstance(module_metrics.get(module_name), dict) else {}
        if not module or not module.get("sample_size"):
            lines.append(f"- {module_labels[module_name]}修正：暂无触发样本。")
            continue
        avg_delta = module.get("avg_actual_probability_delta")
        delta_text = f"，真实方向平均概率变化 {avg_delta:+.2%}" if avg_delta is not None else ""
        lines.append(
            f"- {module_labels[module_name]}修正：样本 {module.get('sample_size')} 场，"
            f"提高 {module.get('improved_actual_probability')} 场，降低 {module.get('worsened_actual_probability')} 场，"
            f"修正主方向 {module.get('fixed_top_outcome')} 场，破坏主方向 {module.get('broke_top_outcome')} 场{delta_text}。"
        )
    odds_metrics_all = summary.get("odds_movement_metrics", {}) if isinstance(summary.get("odds_movement_metrics"), dict) else {}
    odds_cross_all = summary.get("odds_outcome_cross_metrics", {}) if isinstance(summary.get("odds_outcome_cross_metrics"), dict) else {}
    lines.extend(
        [
            "",
            "## 1.5 赔率变化摘要",
            "",
            f"- 初盘→当前盘：样本 {(odds_metrics_all.get('from_opening') or {}).get('sample_size', 0)} 场；主胜走低 {(odds_metrics_all.get('from_opening') or {}).get('home_shorter_count', 0)} 场，平局走低 {(odds_metrics_all.get('from_opening') or {}).get('draw_shorter_count', 0)} 场，客胜走低 {(odds_metrics_all.get('from_opening') or {}).get('away_shorter_count', 0)} 场。",
            f"- 上一版→当前盘：样本 {(odds_metrics_all.get('from_previous') or {}).get('sample_size', 0)} 场；主胜走低 {(odds_metrics_all.get('from_previous') or {}).get('home_shorter_count', 0)} 场，平局走低 {(odds_metrics_all.get('from_previous') or {}).get('draw_shorter_count', 0)} 场，客胜走低 {(odds_metrics_all.get('from_previous') or {}).get('away_shorter_count', 0)} 场。",
        ]
    )
    outcome_labels = {"home": "主胜赔率走低", "draw": "平局赔率走低", "away": "客胜赔率走低"}
    lines.extend(["", "## 1.6 赔率变化 × 命中率交叉统计", ""])
    source_labels = {"from_opening": "初盘→当前盘", "from_previous": "上一版→当前盘"}
    for source_key in ("from_opening", "from_previous"):
        lines.append(f"- {source_labels[source_key]}：")
        source_cross = odds_cross_all.get(source_key, {}) if isinstance(odds_cross_all.get(source_key), dict) else {}
        for key in ("home", "draw", "away"):
            cross = source_cross.get(key, {}) if isinstance(source_cross.get(key), dict) else {}
            if not cross or not cross.get("sample_size"):
                lines.append(f"  {outcome_labels[key]}：暂无样本。")
                continue
            lines.append(
                f"  {outcome_labels[key]}：样本 {cross.get('sample_size')} 场，"
                f"对应真实方向命中率 {float(cross.get('actual_hit_rate', 0.0)):.1%}，"
                f"模型主方向命中率 {float(cross.get('model_pick_rate', 0.0)):.1%}，"
                f"真实方向平均概率 {float(cross.get('avg_actual_probability', 0.0)):.1%}。"
            )
    lines.extend(
        [
            "",
        "## 2. 单场复盘",
        "",
        ]
    )
    for row in summary["matches"]:
        model_probs = row.get("model_probabilities") or [0.0, 0.0, 0.0]
        predicted = row.get("model_top_outcome") or "未知"
        diagnosis_text = diagnosis_label_text(row.get("score_diagnosis"))
        mistake_text = mistake_tag_text(row.get("mistake_tags"))
        source_label = "[代理回放] " if row.get("review_source") == "full_playback_proxy" else "[真实快照] "
        lines.append(
            f"- {source_label}{row['match_time']} {row['home']} vs {row['away']}：实际 {row['actual_score']}（{row['actual_outcome']}），"
            f"模型主方向 {predicted}，AI前主方向 {row.get('base_model_top_outcome') or '未知'}，真实方向概率 {model_probs[int(row['actual_outcome_idx'])]:.1%}，"
            f"比分 Top3 命中：{'是' if row['top3_hit'] else '否'}；比分诊断：{diagnosis_text}；赛果错因：{mistake_text}。"
        )
    lines.extend(
        [
            "",
            "## 3. 反思与调整",
            "",
            *[f"- {item}" for item in summary["advice"]],
            f"- AI 修正幅度调优：{summary.get('calibration_ai_decision', 'keep_current_ai_delta')}；当前普通修正上限 {summary['risk_config'].get('ai_probability_max_delta', 0.03):.3f}，高置信度上限 {summary['risk_config'].get('ai_probability_high_confidence_delta', 0.05):.3f}。",
            f"- 战术修正调优：{summary.get('calibration_tactical_decision', 'keep_current_tactical_delta')}；当前战术权重 {summary['risk_config'].get('tactical_adjustment_weight', 1.0):.2f}，战术修正上限 {summary['risk_config'].get('tactical_adjustment_max_delta', 0.02):.3f}。",
            f"- 价值修正调优：{summary.get('calibration_value_decision', 'keep_current_value_delta')}；当前价值权重 {summary['risk_config'].get('value_adjustment_weight', 1.0):.2f}，价值修正上限 {summary['risk_config'].get('value_adjustment_max_delta', 0.025):.3f}。",
            f"- 天气修正调优：{summary.get('calibration_weather_decision', 'keep_current_weather_delta')}；当前天气权重 {summary['risk_config'].get('weather_adjustment_weight', 1.0):.2f}，天气修正上限 {summary['risk_config'].get('weather_adjustment_max_delta', 0.02):.3f}。",
            f"- 本轮直接匹配快照只有 {summary.get('matched_snapshot_matches', summary['reviewed_matches'])} 场；若含全量回放代理则覆盖 {summary['reviewed_matches']} 场，不调整模型/市场融合权重；只做短期风控收紧。",
            f"- 已将 Kelly 从 1/4 降到不高于 1/5，并将最小 EV 门槛提高到 7%；总投入上限保留为 {summary['risk_config']['max_total_stake']:.0f} 元。",
            "",
            "## 4. 下一步",
            "",
            "- 等下一批比赛结束后继续累计样本，至少满 20 场后再评估是否调整概率模型参数。",
            "- 重点观察平局和一球小胜是否持续低估；若持续出现，优先调整 WDL 平局底线和比分重排低比分权重。",
            "- 若后续有普通胜平负赔率，优先比较 market/fused/model 的 Brier 和 logloss，再决定是否调整 `model_weight`。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    predictions = load_snapshot_predictions()
    results = load_results()
    full_playback = build_full_playback_samples(results)
    full_playback_index = {
        (str(row.get("match_time", "")), str(row.get("home", "")), str(row.get("away", ""))): row
        for row in full_playback
        if row.get("match_time") and row.get("home") and row.get("away")
    }
    training_candidate_index = load_training_candidate_index()
    prematch_news_index = load_combined_prematch_news_index()
    calibration_config = load_probability_config()
    probability_model = load_probability_model(ROOT / "config" / "model_probabilities.json")
    strengths = infer_team_strengths(probability_model)
    score_model = load_score_model(SCORE_MODEL_PATH)
    risk_config = load_risk_config(CALIBRATION_PATH)
    reviewed = []
    matched_snapshot_count = 0
    playback_proxy_count = 0
    counterfactual_replay = []
    counterfactual_inputs: List[Dict[str, object]] = []
    exact_hits = 0
    top3_hits = 0
    top5_hits = 0
    counterfactual_exact_hits = 0
    counterfactual_top3_hits = 0
    counterfactual_top5_hits = 0

    for actual in results:
        key = (str(actual.get("match_time", "")), str(actual.get("home_team", "")), str(actual.get("away_team", "")))
        prediction_row = predictions.get(key)
        if not prediction_row:
            playback_row = full_playback_index.get(key)
            if playback_row:
                reviewed.append(playback_review_row(actual, playback_row))
                playback_proxy_count += 1
            continue
        matched_snapshot_count += 1
        prediction_row = rebuild_adjustment_fields(prediction_row, training_candidate_index, prematch_news_index, calibration_config)
        actual_home = int(actual.get("home_goals", 0))
        actual_away = int(actual.get("away_goals", 0))
        outcome_idx = actual_outcome(actual_home, actual_away)
        prediction = prediction_row.get("score_prediction") or {}
        exact = top1_hit(prediction, actual_home, actual_away)
        top3 = top3_hit(prediction, actual_home, actual_away)
        top5 = top5_hit(prediction, actual_home, actual_away)
        exact_hits += 1 if exact else 0
        top3_hits += 1 if top3 else 0
        top5_hits += 1 if top5 else 0
        model_probs = normalize_probs(prediction_row.get("probabilities"))
        base_probs = normalize_probs(prediction_row.get("base_probabilities")) or model_probs
        context_probs = normalize_probs(prediction_row.get("context_adjusted_probabilities")) or model_probs
        weather_probs = normalize_probs(prediction_row.get("weather_adjusted_probabilities")) or context_probs or model_probs
        tactical_probs = normalize_probs(prediction_row.get("tactical_adjusted_probabilities"))
        value_probs = normalize_probs(prediction_row.get("value_adjusted_probabilities"))
        market_probs = normalize_probs(prediction_row.get("market_probabilities")) or market_probs_from_odds(prediction_row.get("normal_odds"))
        fused_probs = normalize_probs(prediction_row.get("fused_probabilities")) or model_probs
        counterfactual_inputs.append(
            {
                "prediction_row": prediction_row,
                "actual_home": actual_home,
                "actual_away": actual_away,
                "model_probs": model_probs,
            }
        )
        diagnosis = score_diagnosis(prediction, actual_home, actual_away, model_probs)
        funnel_diagnosis = top5_to_top3_diagnosis(prediction, actual_home, actual_away)
        market_score_diagnosis = score_market_direction_diagnosis(prediction_row, actual_home, actual_away)
        counterfactual_prediction = rebuild_counterfactual_score_prediction(prediction_row, score_model, strengths, prematch_news_index, risk_config)
        counterfactual_row = counterfactual_score_row(prediction_row, actual_home, actual_away, model_probs, counterfactual_prediction)
        if counterfactual_row:
            counterfactual_exact_hits += 1 if counterfactual_row.get("exact_hit") else 0
            counterfactual_top3_hits += 1 if counterfactual_row.get("top3_hit") else 0
            counterfactual_top5_hits += 1 if counterfactual_row.get("top5_hit") else 0
            counterfactual_replay.append(counterfactual_row)
        ai_adjustment = prediction_row.get("ai_adjustment") or {}
        mistake_tags = outcome_mistake_tags(
            outcome_idx,
            model_probs,
            base_probs=base_probs,
            market_probs=market_probs,
            score_top3_hit_value=top3,
            ai_adjustment=ai_adjustment,
        )
        reviewed.append(
            {
                "match_time": actual.get("match_time", ""),
                "home": actual.get("home_team", ""),
                "away": actual.get("away_team", ""),
                "actual_score": f"{actual_home}-{actual_away}",
                "actual_outcome": OUTCOME_NAMES[outcome_idx],
                "actual_outcome_idx": outcome_idx,
                "snapshot": prediction_row.get("snapshot", ""),
                "generated_at": prediction_row.get("generated_at", ""),
                "base_model_probabilities": base_probs,
                "context_adjusted_probabilities": context_probs,
                "weather_adjusted_probabilities": weather_probs,
                "tactical_adjusted_probabilities": tactical_probs,
                "value_adjusted_probabilities": value_probs,
                "model_probabilities": model_probs,
                "market_probabilities": market_probs,
                "fused_probabilities": fused_probs,
                "normal_odds_change": prediction_row.get("normal_odds_change"),
                "ai_adjustment": ai_adjustment,
                "applied_tactical": bool(ai_adjustment.get("applied_tactical")),
                "applied_value": bool(ai_adjustment.get("applied_value")),
                "applied_weather": bool(ai_adjustment.get("applied_weather")),
                "mistake_tags": mistake_tags,
                "base_model_top_outcome": OUTCOME_NAMES[max(range(3), key=lambda idx: base_probs[idx])] if base_probs else None,
                "context_model_top_outcome": OUTCOME_NAMES[max(range(3), key=lambda idx: context_probs[idx])] if context_probs else None,
                "tactical_model_top_outcome": OUTCOME_NAMES[max(range(3), key=lambda idx: tactical_probs[idx])] if tactical_probs else None,
                "value_model_top_outcome": OUTCOME_NAMES[max(range(3), key=lambda idx: value_probs[idx])] if value_probs else None,
                "model_top_outcome": OUTCOME_NAMES[max(range(3), key=lambda idx: model_probs[idx])] if model_probs else None,
                "fused_top_outcome": OUTCOME_NAMES[max(range(3), key=lambda idx: fused_probs[idx])] if fused_probs else None,
                "predicted_top3": prediction.get("top_scores") or [],
                "exact_hit": exact,
                "top3_hit": top3,
                "top5_hit": top5,
                **market_score_diagnosis,
                **funnel_diagnosis,
                **diagnosis,
            }
        )

    snapshot_reviewed = [row for row in reviewed if row.get("review_source") != "full_playback_proxy"]
    playback_proxy_reviewed = [row for row in reviewed if row.get("review_source") == "full_playback_proxy"]
    snapshot_bucket = review_bucket(snapshot_reviewed)
    playback_proxy_bucket = review_bucket(playback_proxy_reviewed)
    combined_bucket = review_bucket(reviewed)

    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = REVIEW_DOCS_DIR / f"postmatch-calibration_{timestamp}.json"
    summary: Dict[str, object] = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "snapshots_scanned": len(snapshot_files()),
        "results_loaded": len(results),
        "reviewed_matches": len(reviewed),
        "matched_snapshot_matches": matched_snapshot_count,
        "playback_proxy_matches": playback_proxy_count,
        "snapshot_metrics": snapshot_bucket,
        "playback_proxy_metrics": playback_proxy_bucket,
        "combined_metrics": combined_bucket,
        "score_metrics": combined_bucket["score_metrics"],
        "counterfactual_replay": {
            "sample_size": len(counterfactual_replay),
            "score_metrics": score_metric_summary(counterfactual_replay, counterfactual_exact_hits, counterfactual_top3_hits, counterfactual_top5_hits),
            "matches": counterfactual_replay,
        },
        "probability_metrics": combined_bucket["probability_metrics"],
        "full_playback": {
            "sample_size": len(full_playback),
            "score_metrics": score_metric_summary(
                full_playback,
                sum(1 for row in full_playback if row.get("exact_hit")),
                sum(1 for row in full_playback if row.get("top3_hit")),
                sum(1 for row in full_playback if row.get("top5_hit")),
            ),
            "probability_metrics": {
                "model": probability_stats(full_playback, "model_probabilities"),
            },
        },
        "full_playback_matches": full_playback,
        "matches": reviewed,
        "review_file": out_path.name,
    }
    summary["counterfactual_grid_search"] = counterfactual_grid_search(
        counterfactual_inputs,
        score_model,
        strengths,
        prematch_news_index,
        risk_config,
    )
    summary["counterfactual_replay"]["score_metric_delta_vs_snapshot"] = score_metric_delta(
        summary["snapshot_metrics"]["score_metrics"], summary["counterfactual_replay"]["score_metrics"]
    )
    summary["mistake_tag_metrics"] = combined_bucket["mistake_tag_metrics"]
    summary["adjustment_module_metrics"] = snapshot_bucket["adjustment_module_metrics"]
    summary["odds_movement_metrics"] = snapshot_bucket["odds_movement_metrics"]
    summary["odds_outcome_cross_metrics"] = snapshot_bucket["odds_outcome_cross_metrics"]
    summary["advice"] = build_advice(summary, reviewed)

    REVIEW_DOCS_DIR.mkdir(parents=True, exist_ok=True)
    update_calibration_config(summary)
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    reflection_path = write_reflection_report(summary)
    print(f"Snapshots scanned: {summary['snapshots_scanned']}")
    print(f"Results loaded: {summary['results_loaded']}")
    print(f"Reviewed matches: {summary['reviewed_matches']}")
    print(f"Matched snapshots: {summary['matched_snapshot_matches']}")
    print(f"Playback proxy matches: {summary['playback_proxy_matches']}")
    print(f"Snapshot WDL accuracy: {summary['snapshot_metrics']['probability_metrics']['model']['accuracy']}")
    print(f"Playback proxy WDL accuracy: {summary['playback_proxy_metrics']['probability_metrics']['model']['accuracy']}")
    print(f"Combined WDL accuracy: {summary['combined_metrics']['probability_metrics']['model']['accuracy']}")
    print(f"Full playback matches: {summary['full_playback']['sample_size']}")
    print(f"Exact score accuracy: {summary['score_metrics']['exact_score_accuracy']:.4f}")
    print(f"Top3 hit rate: {summary['score_metrics']['top3_hit_rate']:.4f}")
    print(f"Top5 hit rate: {summary['score_metrics']['top5_hit_rate']:.4f}")
    print(f"Counterfactual replay matches: {summary['counterfactual_replay']['sample_size']}")
    print(f"Counterfactual Top3 hit rate: {summary['counterfactual_replay']['score_metrics']['top3_hit_rate']:.4f}")
    print(f"Counterfactual Top5 hit rate: {summary['counterfactual_replay']['score_metrics']['top5_hit_rate']:.4f}")
    best_grid = summary.get("counterfactual_grid_search", {}).get("best") if isinstance(summary.get("counterfactual_grid_search"), dict) else None
    if isinstance(best_grid, dict):
        print(f"Counterfactual grid best score: {best_grid.get('score_value'):.4f}")
        print(f"Counterfactual grid best params: {best_grid.get('params')}")
    print(f"Model WDL accuracy: {summary['probability_metrics']['model']['accuracy']}")
    print(f"Review written: {out_path}")
    print(f"Reflection written: {reflection_path}")
    print(f"Calibration updated: {CALIBRATION_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
