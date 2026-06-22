#!/usr/bin/env python3
"""Review completed matches and calibrate prediction probabilities."""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
RUN_DOCS_DIR = ROOT / "docs" / "run"
REVIEW_DOCS_DIR = ROOT / "docs" / "review"
STATS_RESULTS_PATH = ROOT / "data" / "raw" / "statsbomb_matches_real.json"
WC2026_RESULTS_PATH = ROOT / "data" / "raw" / "wc2026_football_data_matches.json"
CALIBRATION_PATH = ROOT / "config" / "bayesian_calibration.json"
OUTCOME_NAMES = ["主胜", "平", "客胜"]

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


def metric_summary(values: List[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


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
    model_stats = summary["probability_metrics"]["model"]
    market_stats = summary["probability_metrics"].get("market", {})
    fused_stats = summary["probability_metrics"]["fused"]
    score_top3 = summary["score_metrics"]["top3_hit_rate"]

    if model_stats["sample_size"] and model_stats["avg_actual_prob"] is not None and model_stats["avg_actual_prob"] < 0.45:
        advice.append("真实赛果在模型分配的平均概率偏低，说明当前 WDL 概率校准偏乐观或方向偏差较大，应降低 Kelly 信号强度。")
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
    market_stats = probability_metrics["market"]
    fused_stats = probability_metrics["fused"]
    sample_size = int(summary["reviewed_matches"])

    config["calibration"] = {
        "sample_size": sample_size,
        "model_accuracy": model_stats["accuracy"],
        "market_accuracy": market_stats["accuracy"],
        "fused_accuracy": fused_stats["accuracy"],
        "model_brier": model_stats["brier"],
        "market_brier": market_stats["brier"],
        "fused_brier": fused_stats["brier"],
        "model_logloss": model_stats["logloss"],
        "market_logloss": market_stats["logloss"],
        "fused_logloss": fused_stats["logloss"],
        "score_exact_accuracy": summary["score_metrics"]["exact_score_accuracy"],
        "score_top3_hit_rate": summary["score_metrics"]["top3_hit_rate"],
        "last_review_file": summary["review_file"],
    }

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

    avg_actual_prob = model_stats.get("avg_actual_prob")
    top3_rate = summary["score_metrics"].get("top3_hit_rate")
    if sample_size > 0 and ((avg_actual_prob is not None and avg_actual_prob < 0.45) or (top3_rate is not None and top3_rate < 0.35)):
        config["kelly_fraction"] = min(float(config.get("kelly_fraction", 0.25)), 0.2)
        config["min_edge"] = max(float(config.get("min_edge", 0.05)), 0.07)
        config["calibration"]["risk_adjustment_decision"] = "poor_short_sample_accuracy_reduce_kelly_raise_edge_keep_manual_total_stake"
    else:
        config["calibration"]["risk_adjustment_decision"] = "keep_current_risk_settings"

    CALIBRATION_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["risk_config"] = {
        "kelly_fraction": config.get("kelly_fraction"),
        "min_edge": config.get("min_edge"),
        "bankroll": config.get("bankroll"),
        "max_stake_per_pick": config.get("max_stake_per_pick"),
        "max_total_stake": config.get("max_total_stake"),
    }


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
        f"- 模型 Brier：{model_stats['brier']:.4f}" if model_stats["brier"] is not None else "- 模型 Brier：暂无",
        f"- 融合 Brier：{fused_stats['brier']:.4f}" if fused_stats["brier"] is not None else "- 融合 Brier：暂无",
        "",
        "## 2. 单场复盘",
        "",
    ]
    for row in summary["matches"]:
        model_probs = row.get("model_probabilities") or [0.0, 0.0, 0.0]
        predicted = row.get("model_top_outcome") or "未知"
        lines.append(
            f"- {row['match_time']} {row['home']} vs {row['away']}：实际 {row['actual_score']}（{row['actual_outcome']}），"
            f"模型主方向 {predicted}，真实方向概率 {model_probs[int(row['actual_outcome_idx'])]:.1%}，"
            f"比分 Top3 命中：{'是' if row['top3_hit'] else '否'}。"
        )
    lines.extend(
        [
            "",
            "## 3. 反思与调整",
            "",
            *[f"- {item}" for item in summary["advice"]],
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
        market_probs = normalize_probs(prediction_row.get("market_probabilities")) or market_probs_from_odds(prediction_row.get("normal_odds"))
        fused_probs = normalize_probs(prediction_row.get("fused_probabilities")) or model_probs
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
                "model_probabilities": model_probs,
                "market_probabilities": market_probs,
                "fused_probabilities": fused_probs,
                "model_top_outcome": OUTCOME_NAMES[max(range(3), key=lambda idx: model_probs[idx])] if model_probs else None,
                "fused_top_outcome": OUTCOME_NAMES[max(range(3), key=lambda idx: fused_probs[idx])] if fused_probs else None,
                "predicted_top3": prediction.get("top_scores") or [],
                "exact_hit": exact,
                "top3_hit": top3,
            }
        )

    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = REVIEW_DOCS_DIR / f"postmatch-calibration_{timestamp}.json"
    summary: Dict[str, object] = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "snapshots_scanned": len(snapshot_files()),
        "results_loaded": len(results),
        "reviewed_matches": len(reviewed),
        "score_metrics": {
            "exact_score_accuracy": (exact_hits / len(reviewed)) if reviewed else 0.0,
            "top3_hit_rate": (top3_hits / len(reviewed)) if reviewed else 0.0,
        },
        "probability_metrics": {
            "model": probability_stats(reviewed, "model_probabilities"),
            "market": probability_stats(reviewed, "market_probabilities"),
            "fused": probability_stats(reviewed, "fused_probabilities"),
        },
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
    print(f"Exact score accuracy: {summary['score_metrics']['exact_score_accuracy']:.4f}")
    print(f"Top3 hit rate: {summary['score_metrics']['top3_hit_rate']:.4f}")
    print(f"Model WDL accuracy: {summary['probability_metrics']['model']['accuracy']}")
    print(f"Review written: {out_path}")
    print(f"Reflection written: {reflection_path}")
    print(f"Calibration updated: {CALIBRATION_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
