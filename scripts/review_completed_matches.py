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

from worldcup_agent import (  # noqa: E402
    calibrate_wdl_probabilities,
    estimate_match_probabilities,
    infer_team_strengths,
    load_probability_model,
    score_prediction_from_wdl,
)

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


def build_full_playback_samples(results: List[Dict[str, object]]) -> List[Dict[str, object]]:
    model_path = ROOT / "config" / "model_probabilities.json"
    if not model_path.exists():
        return []
    model = load_probability_model(model_path)
    strengths = infer_team_strengths(model)
    calibration_config = load_probability_config()
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
        score_prediction = score_prediction_from_wdl(probs)
        diagnosis = score_diagnosis(score_prediction, actual_home, actual_away, probs)
        samples.append(
            {
                "match_time": actual.get("match_time", ""),
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
                **diagnosis,
                "sample_source": source,
            }
        )
    return samples


def snapshot_files() -> List[Path]:
    return sorted(RUN_DOCS_DIR.glob("worldcup-2026-agent-predictions_*.json"))


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
    if not isinstance(values, list) or len(values) < 3:
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
    if not isinstance(values, list) or len(values) < 3:
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


def brier(probabilities: Tuple[float, float, float], outcome_idx: int) -> float:
    return sum((probabilities[idx] - (1.0 if idx == outcome_idx else 0.0)) ** 2 for idx in range(3))


def logloss(probabilities: Tuple[float, float, float], outcome_idx: int) -> float:
    return -math.log(max(probabilities[outcome_idx], 1e-12))


def top1_hit(prediction: Dict[str, object], actual_home: int, actual_away: int) -> bool:
    top_scores = prediction.get("top_scores") or []
    if not top_scores:
        return False
    home, away, _prob = top_scores[0]
    return int(home) == actual_home and int(away) == actual_away


def top3_hit(prediction: Dict[str, object], actual_home: int, actual_away: int) -> bool:
    top_scores = prediction.get("top_scores") or []
    for home, away, _prob in top_scores:
        if int(home) == actual_home and int(away) == actual_away:
            return True
    return False


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


def score_metric_summary(reviewed: List[Dict[str, object]], exact_hits: int, top3_hits: int) -> Dict[str, object]:
    total_errors = [float(row["total_goals_error"]) for row in reviewed if row.get("total_goals_error") is not None]
    diff_errors = [float(row["goal_diff_error"]) for row in reviewed if row.get("goal_diff_error") is not None]
    labels = [label for row in reviewed for label in row.get("score_diagnosis", [])]

    def label_rate(label: str) -> float:
        return labels.count(label) / len(reviewed) if reviewed else 0.0

    return {
        "exact_score_accuracy": (exact_hits / len(reviewed)) if reviewed else 0.0,
        "top3_hit_rate": (top3_hits / len(reviewed)) if reviewed else 0.0,
        "avg_total_goals_error": metric_summary(total_errors),
        "avg_abs_total_goals_error": metric_summary([abs(value) for value in total_errors]),
        "avg_goal_diff_error": metric_summary(diff_errors),
        "avg_abs_goal_diff_error": metric_summary([abs(value) for value in diff_errors]),
        "underestimated_total_goals_rate": label_rate("underestimated_total_goals"),
        "overestimated_total_goals_rate": label_rate("overestimated_total_goals"),
        "underestimated_draw_rate": label_rate("underestimated_draw"),
        "underestimated_underdog_goal_rate": label_rate("underestimated_underdog_goal"),
        "underestimated_big_win_rate": label_rate("underestimated_big_win"),
        "wrong_goal_diff_direction_rate": label_rate("wrong_goal_diff_direction"),
        "score_distribution_too_narrow_rate": label_rate("score_distribution_too_narrow"),
    }


def diagnosis_label_text(labels: object) -> str:
    if not isinstance(labels, list) or not labels:
        return "无明显比分偏差标签"
    return "、".join(DIAGNOSIS_LABELS.get(str(label), str(label)) for label in labels)


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
    if score_metrics.get("underestimated_total_goals_rate", 0.0) >= 0.25:
        advice.append("低估总进球的场次占比较高，应检查总进球 lambda 和大比分尾部权重。")
    if score_metrics.get("underestimated_draw_rate", 0.0) >= 0.25:
        advice.append("实际平局但模型主方向非平局的场次偏多，应继续观察并可能提高平局保护。")
    if score_metrics.get("underestimated_big_win_rate", 0.0) >= 0.20:
        advice.append("强队大胜或客队大胜尾部覆盖不足，应让深盘/强弱差更明显地影响比分尾部分布。")
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

    probability_metrics = summary["probability_metrics"]
    model_stats = probability_metrics["model"]
    base_stats = probability_metrics.get("base", {})
    context_stats = probability_metrics.get("context", {})
    tactical_stats = probability_metrics.get("tactical", {})
    value_stats = probability_metrics.get("value", {})
    market_stats = probability_metrics["market"]
    fused_stats = probability_metrics["fused"]
    full_playback = summary.get("full_playback", {}) if isinstance(summary.get("full_playback"), dict) else {}
    full_model_stats = full_playback.get("probability_metrics", {}).get("model", {}) if isinstance(full_playback.get("probability_metrics"), dict) else {}
    full_score_metrics = full_playback.get("score_metrics", {}) if isinstance(full_playback.get("score_metrics"), dict) else {}
    sample_size = int(summary["reviewed_matches"])
    config.setdefault("draw_probability_floor", 0.22)
    config.setdefault("balanced_match_draw_floor", 0.26)
    config.setdefault("strong_favorite_draw_floor", 0.25)
    config.setdefault("max_draw_calibration_boost", 0.08)
    config.setdefault("underdog_probability_floor", 0.18)
    config.setdefault("max_underdog_calibration_boost", 0.03)
    config.setdefault("enable_ai_probability_adjustment", 1.0)
    config.setdefault("ai_probability_max_delta", 0.03)
    config.setdefault("ai_probability_high_confidence_delta", 0.05)

    config["calibration"] = {
        "sample_size": sample_size,
        "base_model_accuracy": base_stats.get("accuracy"),
        "context_model_accuracy": context_stats.get("accuracy"),
        "tactical_model_accuracy": tactical_stats.get("accuracy"),
        "value_model_accuracy": value_stats.get("accuracy"),
        "model_accuracy": model_stats["accuracy"],
        "market_accuracy": market_stats["accuracy"],
        "fused_accuracy": fused_stats["accuracy"],
        "model_brier": model_stats["brier"],
        "base_model_brier": base_stats.get("brier"),
        "context_model_brier": context_stats.get("brier"),
        "tactical_model_brier": tactical_stats.get("brier"),
        "value_model_brier": value_stats.get("brier"),
        "market_brier": market_stats["brier"],
        "fused_brier": fused_stats["brier"],
        "base_model_logloss": base_stats.get("logloss"),
        "context_model_logloss": context_stats.get("logloss"),
        "tactical_model_logloss": tactical_stats.get("logloss"),
        "value_model_logloss": value_stats.get("logloss"),
        "model_logloss": model_stats["logloss"],
        "market_logloss": market_stats["logloss"],
        "fused_logloss": fused_stats["logloss"],
        "score_exact_accuracy": summary["score_metrics"]["exact_score_accuracy"],
        "score_top3_hit_rate": summary["score_metrics"]["top3_hit_rate"],
        "avg_total_goals_error": summary["score_metrics"].get("avg_total_goals_error"),
        "avg_abs_total_goals_error": summary["score_metrics"].get("avg_abs_total_goals_error"),
        "avg_goal_diff_error": summary["score_metrics"].get("avg_goal_diff_error"),
        "avg_abs_goal_diff_error": summary["score_metrics"].get("avg_abs_goal_diff_error"),
        "underestimated_total_goals_rate": summary["score_metrics"].get("underestimated_total_goals_rate"),
        "underestimated_draw_rate": summary["score_metrics"].get("underestimated_draw_rate"),
        "underestimated_underdog_goal_rate": summary["score_metrics"].get("underestimated_underdog_goal_rate"),
        "underestimated_big_win_rate": summary["score_metrics"].get("underestimated_big_win_rate"),
        "last_review_file": summary["review_file"],
    }
    if full_playback:
        config["calibration"]["full_playback_sample_size"] = full_playback.get("sample_size")
        config["calibration"]["full_playback_model_accuracy"] = full_model_stats.get("accuracy")
        config["calibration"]["full_playback_model_brier"] = full_model_stats.get("brier")
        config["calibration"]["full_playback_model_logloss"] = full_model_stats.get("logloss")
        config["calibration"]["full_playback_score_top3_hit_rate"] = full_score_metrics.get("top3_hit_rate")

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

    avg_actual_prob = model_stats.get("avg_actual_prob")
    top3_rate = summary["score_metrics"].get("top3_hit_rate")
    if sample_size > 0 and ((avg_actual_prob is not None and avg_actual_prob < 0.45) or (top3_rate is not None and top3_rate < 0.35)):
        config["kelly_fraction"] = min(float(config.get("kelly_fraction", 0.25)), 0.2)
        config["min_edge"] = max(float(config.get("min_edge", 0.05)), 0.07)
        config["calibration"]["risk_adjustment_decision"] = "poor_short_sample_accuracy_reduce_kelly_raise_edge_keep_manual_total_stake"
    else:
        config["calibration"]["risk_adjustment_decision"] = "keep_current_risk_settings"

    full_sample_size = int(full_playback.get("sample_size", 0) or 0) if full_playback else 0
    if full_sample_size >= 10:
        draw_rows = [row for row in summary.get("full_playback_matches", []) if int(row.get("actual_outcome_idx", -1)) == 1 and row.get("model_probabilities")]
        low_draw_hits = [row for row in draw_rows if row["model_probabilities"][1] < 0.25]
        if draw_rows and len(low_draw_hits) / len(draw_rows) >= 0.4:
            config["draw_probability_floor"] = min(0.26, max(float(config.get("draw_probability_floor", 0.22)), 0.23))
            config["balanced_match_draw_floor"] = min(0.29, max(float(config.get("balanced_match_draw_floor", 0.26)), 0.27))
            config["strong_favorite_draw_floor"] = min(0.28, max(float(config.get("strong_favorite_draw_floor", 0.25)), 0.26))
            config["max_draw_calibration_boost"] = min(0.10, max(float(config.get("max_draw_calibration_boost", 0.08)), 0.09))
            config["calibration"]["probability_parameter_decision"] = "full_playback_raises_draw_protection"
        else:
            config["calibration"]["probability_parameter_decision"] = "full_playback_keep_probability_parameters"
    elif full_playback:
        config["calibration"]["probability_parameter_decision"] = "full_playback_sample_below_10_keep_parameters"

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
    }
    summary["calibration_ai_decision"] = config["calibration"].get("ai_adjustment_decision")
    summary["calibration_tactical_decision"] = config["calibration"].get("tactical_adjustment_decision")
    summary["calibration_value_decision"] = config["calibration"].get("value_adjustment_decision")


def write_reflection_report(summary: Dict[str, object]) -> Path:
    path = REVIEW_DOCS_DIR / summary["review_file"].replace("postmatch-calibration_", "postmatch-reflection_").replace(".json", ".md")
    model_stats = summary["probability_metrics"]["model"]
    fused_stats = summary["probability_metrics"]["fused"]
    lines = [
        "# 赛后准确率校准与反思",
        "",
        f"生成时间：{summary['generated_at']}",
        "",
        "## 1. 本轮校准结果",
        "",
        f"- 扫描预测快照：{summary['snapshots_scanned']} 个",
        f"- 加载赛果：{summary['results_loaded']} 场",
        f"- 成功匹配赛前预测：{summary['reviewed_matches']} 场",
        f"- 比分 Exact：{summary['score_metrics']['exact_score_accuracy']:.1%}",
        f"- 比分 Top3：{summary['score_metrics']['top3_hit_rate']:.1%}",
        f"- WDL 模型方向准确率：{model_stats['accuracy']:.1%}" if model_stats["accuracy"] is not None else "- WDL 模型方向准确率：暂无",
        f"- AI 修正前 WDL 准确率：{summary['probability_metrics']['base']['accuracy']:.1%}" if summary['probability_metrics']['base']['accuracy'] is not None else "- AI 修正前 WDL 准确率：暂无",
        f"- 模型 Brier：{model_stats['brier']:.4f}" if model_stats["brier"] is not None else "- 模型 Brier：暂无",
        f"- AI 修正前 Brier：{summary['probability_metrics']['base']['brier']:.4f}" if summary['probability_metrics']['base']['brier'] is not None else "- AI 修正前 Brier：暂无",
        f"- 融合 Brier：{fused_stats['brier']:.4f}" if fused_stats["brier"] is not None else "- 融合 Brier：暂无",
        "",
        "## 1.1 全量已完赛回放校准",
        "",
        f"- 回放样本：{summary.get('full_playback', {}).get('sample_size', 0)} 场（使用当前模型回放，非真实赛前快照）。",
        f"- 回放 WDL 准确率：{summary.get('full_playback', {}).get('probability_metrics', {}).get('model', {}).get('accuracy'):.1%}" if summary.get('full_playback', {}).get('probability_metrics', {}).get('model', {}).get('accuracy') is not None else "- 回放 WDL 准确率：暂无",
        f"- 回放比分 Top3：{summary.get('full_playback', {}).get('score_metrics', {}).get('top3_hit_rate'):.1%}" if summary.get('full_playback', {}).get('score_metrics', {}).get('top3_hit_rate') is not None else "- 回放比分 Top3：暂无",
        "",
        "## 1.2 比分诊断",
        "",
        f"- 平均总进球误差：{summary['score_metrics'].get('avg_total_goals_error'):.2f}" if summary['score_metrics'].get('avg_total_goals_error') is not None else "- 平均总进球误差：暂无",
        f"- 平均总进球绝对误差：{summary['score_metrics'].get('avg_abs_total_goals_error'):.2f}" if summary['score_metrics'].get('avg_abs_total_goals_error') is not None else "- 平均总进球绝对误差：暂无",
        f"- 平均净胜球误差：{summary['score_metrics'].get('avg_goal_diff_error'):.2f}" if summary['score_metrics'].get('avg_goal_diff_error') is not None else "- 平均净胜球误差：暂无",
        f"- 平均净胜球绝对误差：{summary['score_metrics'].get('avg_abs_goal_diff_error'):.2f}" if summary['score_metrics'].get('avg_abs_goal_diff_error') is not None else "- 平均净胜球绝对误差：暂无",
        f"- 低估总进球：{summary['score_metrics'].get('underestimated_total_goals_rate', 0.0):.1%}",
        f"- 高估总进球：{summary['score_metrics'].get('overestimated_total_goals_rate', 0.0):.1%}",
        f"- 低估平局：{summary['score_metrics'].get('underestimated_draw_rate', 0.0):.1%}",
        f"- 低估弱队进球：{summary['score_metrics'].get('underestimated_underdog_goal_rate', 0.0):.1%}",
        f"- 低估大胜：{summary['score_metrics'].get('underestimated_big_win_rate', 0.0):.1%}",
        "",
        "## 2. 单场复盘",
        "",
    ]
    for row in summary["matches"]:
        model_probs = row.get("model_probabilities") or [0.0, 0.0, 0.0]
        predicted = row.get("model_top_outcome") or "未知"
        diagnosis_text = diagnosis_label_text(row.get("score_diagnosis"))
        lines.append(
            f"- {row['match_time']} {row['home']} vs {row['away']}：实际 {row['actual_score']}（{row['actual_outcome']}），"
            f"模型主方向 {predicted}，AI前主方向 {row.get('base_model_top_outcome') or '未知'}，真实方向概率 {model_probs[int(row['actual_outcome_idx'])]:.1%}，"
            f"比分 Top3 命中：{'是' if row['top3_hit'] else '否'}；诊断：{diagnosis_text}。"
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
            f"- 本轮样本只有 {summary['reviewed_matches']} 场，不调整模型/市场融合权重；只做短期风控收紧。",
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
    reviewed = []
    exact_hits = 0
    top3_hits = 0

    for actual in results:
        key = (str(actual.get("match_time", "")), str(actual.get("home_team", "")), str(actual.get("away_team", "")))
        prediction_row = predictions.get(key)
        if not prediction_row:
            continue
        actual_home = int(actual.get("home_goals", 0))
        actual_away = int(actual.get("away_goals", 0))
        outcome_idx = actual_outcome(actual_home, actual_away)
        prediction = prediction_row.get("score_prediction") or {}
        exact = top1_hit(prediction, actual_home, actual_away)
        top3 = top3_hit(prediction, actual_home, actual_away)
        exact_hits += 1 if exact else 0
        top3_hits += 1 if top3 else 0
        model_probs = normalize_probs(prediction_row.get("probabilities"))
        base_probs = normalize_probs(prediction_row.get("base_probabilities")) or model_probs
        context_probs = normalize_probs(prediction_row.get("context_adjusted_probabilities")) or model_probs
        tactical_probs = normalize_probs(prediction_row.get("tactical_adjusted_probabilities"))
        value_probs = normalize_probs(prediction_row.get("value_adjusted_probabilities"))
        market_probs = normalize_probs(prediction_row.get("market_probabilities")) or market_probs_from_odds(prediction_row.get("normal_odds"))
        fused_probs = normalize_probs(prediction_row.get("fused_probabilities")) or model_probs
        diagnosis = score_diagnosis(prediction, actual_home, actual_away, model_probs)
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
                "tactical_adjusted_probabilities": tactical_probs,
                "value_adjusted_probabilities": value_probs,
                "model_probabilities": model_probs,
                "market_probabilities": market_probs,
                "fused_probabilities": fused_probs,
                "ai_adjustment": prediction_row.get("ai_adjustment") or {},
                "applied_tactical": bool((prediction_row.get("ai_adjustment") or {}).get("applied_tactical")),
                "base_model_top_outcome": OUTCOME_NAMES[max(range(3), key=lambda idx: base_probs[idx])] if base_probs else None,
                "context_model_top_outcome": OUTCOME_NAMES[max(range(3), key=lambda idx: context_probs[idx])] if context_probs else None,
                "tactical_model_top_outcome": OUTCOME_NAMES[max(range(3), key=lambda idx: tactical_probs[idx])] if tactical_probs else None,
                "value_model_top_outcome": OUTCOME_NAMES[max(range(3), key=lambda idx: value_probs[idx])] if value_probs else None,
                "model_top_outcome": OUTCOME_NAMES[max(range(3), key=lambda idx: model_probs[idx])] if model_probs else None,
                "fused_top_outcome": OUTCOME_NAMES[max(range(3), key=lambda idx: fused_probs[idx])] if fused_probs else None,
                "predicted_top3": prediction.get("top_scores") or [],
                "exact_hit": exact,
                "top3_hit": top3,
                **diagnosis,
            }
        )

    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = REVIEW_DOCS_DIR / f"postmatch-calibration_{timestamp}.json"
    summary: Dict[str, object] = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "snapshots_scanned": len(snapshot_files()),
        "results_loaded": len(results),
        "reviewed_matches": len(reviewed),
        "score_metrics": score_metric_summary(reviewed, exact_hits, top3_hits),
        "probability_metrics": {
            "base": probability_stats(reviewed, "base_model_probabilities"),
            "context": probability_stats(reviewed, "context_adjusted_probabilities"),
            "tactical": probability_stats(reviewed, "tactical_adjusted_probabilities"),
            "value": probability_stats(reviewed, "value_adjusted_probabilities"),
            "model": probability_stats(reviewed, "model_probabilities"),
            "market": probability_stats(reviewed, "market_probabilities"),
            "fused": probability_stats(reviewed, "fused_probabilities"),
        },
        "full_playback": {
            "sample_size": len(full_playback),
            "score_metrics": score_metric_summary(
                full_playback,
                sum(1 for row in full_playback if row.get("exact_hit")),
                sum(1 for row in full_playback if row.get("top3_hit")),
            ),
            "probability_metrics": {
                "model": probability_stats(full_playback, "model_probabilities"),
            },
        },
        "full_playback_matches": full_playback,
        "matches": reviewed,
        "review_file": out_path.name,
    }
    summary["advice"] = build_advice(summary, reviewed)

    REVIEW_DOCS_DIR.mkdir(parents=True, exist_ok=True)
    update_calibration_config(summary)
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    reflection_path = write_reflection_report(summary)
    print(f"Snapshots scanned: {summary['snapshots_scanned']}")
    print(f"Results loaded: {summary['results_loaded']}")
    print(f"Reviewed matches: {summary['reviewed_matches']}")
    print(f"Full playback matches: {summary['full_playback']['sample_size']}")
    print(f"Exact score accuracy: {summary['score_metrics']['exact_score_accuracy']:.4f}")
    print(f"Top3 hit rate: {summary['score_metrics']['top3_hit_rate']:.4f}")
    print(f"Model WDL accuracy: {summary['probability_metrics']['model']['accuracy']}")
    print(f"Review written: {out_path}")
    print(f"Reflection written: {reflection_path}")
    print(f"Calibration updated: {CALIBRATION_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
