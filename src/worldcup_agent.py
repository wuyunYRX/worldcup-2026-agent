#!/usr/bin/env python3
"""Generate a 2026 World Cup prediction and lottery EV HTML report.

The report renderer uses Playwright to create a PNG screenshot for Telegram.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import math
import os
import re
import shutil
import sys
import uuid
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib import parse, request

try:
    from .kelly_criterion import fractional_kelly, kelly_fractions_for_match, kelly_stakes_for_match
    from .probability_fusion import fuse_wdl_probabilities
except ImportError:
    from kelly_criterion import fractional_kelly, kelly_fractions_for_match, kelly_stakes_for_match
    from probability_fusion import fuse_wdl_probabilities


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ODDS_URL = "https://cp.zgzcw.com/lottery/jchtplayvsForJsp.action?lotteryId=47&type=jcmini"
WC2026_RESULTS_PATH = ROOT / "data" / "raw" / "wc2026_football_data_matches.json"
OUTCOME_NAMES = ["主胜", "平", "客胜"]
DRAW_PROBABILITY_FLOOR = 0.22
BALANCED_MATCH_DRAW_FLOOR = 0.26
STRONG_FAVORITE_DRAW_FLOOR = 0.25
MAX_DRAW_CALIBRATION_BOOST = 0.08
UNDERDOG_PROBABILITY_FLOOR = 0.18
MAX_UNDERDOG_CALIBRATION_BOOST = 0.03
DEFAULT_RISK_CONFIG = {
    "model_weight": 0.4,
    "kelly_fraction": 0.25,
    "min_edge": 0.05,
    "bankroll": 10.0,
    "min_stake": 2.0,
    "max_stake_per_pick": 5.0,
    "max_total_stake": 15.0,
}
TEAM_TRANSLATIONS = {
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
    "USA": "美国",
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


def normalize_text(value: str) -> str:
    text = html.unescape(value).strip()
    return re.sub(r"[\uE000-\uF8FF\uFFFD]", "", text).strip()


def decode_page_bytes(data: bytes) -> str:
    for enc, errors in (("utf-8", "strict"), ("gb18030", "ignore"), ("gbk", "ignore")):
        try:
            text = data.decode(enc, errors=errors)
        except UnicodeDecodeError:
            continue
        if any(token in text for token in ("比赛时间", "spArr", 'class="beginBet')):
            return text
        normalized = normalize_text(text)
        if any(token in normalized for token in ("比赛时间", "世界杯", 'class="beginBet')):
            return normalized
        return text
    return data.decode("utf-8", errors="ignore")


def load_env(path: Path) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def fetch_text(url: str, timeout: int = 25) -> str:
    req = request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 WorldCupAgent/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    return decode_page_bytes(data)


def load_probability_model(path: Path) -> Dict[Tuple[str, str], Tuple[float, float, float]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    model: Dict[Tuple[str, str], Tuple[float, float, float]] = {}
    for key, values in raw.items():
        home, away = key.split("|", 1)
        if len(values) != 3:
            raise ValueError(f"Model row must have 3 probabilities: {key}")
        total = sum(values)
        if not 0.98 <= total <= 1.02:
            raise ValueError(f"Model probabilities must sum near 1: {key}={values}")
        model[(home, away)] = (float(values[0]), float(values[1]), float(values[2]))
    return model


def load_score_model(path: Path) -> Optional[Dict[str, object]]:
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return None
    if not isinstance(raw.get("feature_names"), list):
        return None
    if not isinstance(raw.get("home_model"), dict) or not isinstance(raw.get("away_model"), dict):
        return None
    return raw


def load_risk_config(path: Path, env: Optional[Dict[str, str]] = None) -> Dict[str, float]:
    config = dict(DEFAULT_RISK_CONFIG)
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        if isinstance(raw, dict):
            for key in config:
                value = raw.get(key)
                if isinstance(value, (int, float)):
                    config[key] = float(value)
    if env and env.get("BANKROLL"):
        try:
            config["bankroll"] = float(env["BANKROLL"])
        except ValueError:
            pass
    return config


def team_display_name(name: str) -> str:
    return TEAM_TRANSLATIONS.get(name, name)


def build_group_standings(results_path: Path = WC2026_RESULTS_PATH) -> Tuple[List[dict], List[dict]]:
    if not results_path.exists():
        return [], []
    try:
        raw = json.loads(results_path.read_text(encoding="utf-8"))
    except Exception:
        return [], []

    table: Dict[str, Dict[str, dict]] = {}
    for match in raw.get("matches", []):
        if match.get("status") != "FINISHED" or match.get("stage") != "GROUP_STAGE":
            continue
        group = str(match.get("group", "")).replace("GROUP_", "")
        home = team_display_name(str(match.get("homeTeam", {}).get("name", "")))
        away = team_display_name(str(match.get("awayTeam", {}).get("name", "")))
        score = match.get("score", {}).get("fullTime", {})
        home_goals = score.get("home")
        away_goals = score.get("away")
        if not group or not home or not away or home_goals is None or away_goals is None:
            continue
        group_table = table.setdefault(group, {})
        for team in (home, away):
            group_table.setdefault(team, {"group": group, "team": team, "played": 0, "win": 0, "draw": 0, "loss": 0, "gf": 0, "ga": 0, "gd": 0, "points": 0})

        home_row = group_table[home]
        away_row = group_table[away]
        home_row["played"] += 1
        away_row["played"] += 1
        home_row["gf"] += int(home_goals)
        home_row["ga"] += int(away_goals)
        away_row["gf"] += int(away_goals)
        away_row["ga"] += int(home_goals)
        if home_goals > away_goals:
            home_row["win"] += 1
            away_row["loss"] += 1
            home_row["points"] += 3
        elif home_goals < away_goals:
            away_row["win"] += 1
            home_row["loss"] += 1
            away_row["points"] += 3
        else:
            home_row["draw"] += 1
            away_row["draw"] += 1
            home_row["points"] += 1
            away_row["points"] += 1
        home_row["gd"] = home_row["gf"] - home_row["ga"]
        away_row["gd"] = away_row["gf"] - away_row["ga"]

    standings: List[dict] = []
    for group, rows in sorted(table.items()):
        ranked = sorted(rows.values(), key=lambda row: (-row["points"], -row["gd"], -row["gf"], row["team"]))
        for rank, row in enumerate(ranked, start=1):
            standings.append({**row, "rank": rank})
    third_rows = [row for row in standings if row["rank"] == 3]
    best_thirds = sorted(third_rows, key=lambda row: (-row["points"], -row["gd"], -row["gf"], row["team"]))
    for rank, row in enumerate(best_thirds, start=1):
        row["third_rank"] = rank
    return standings, best_thirds


def row_probabilities(row: dict) -> Optional[Tuple[float, float, float]]:
    return row.get("fused_probabilities") or row.get("probabilities")


def apply_value_metrics(
    probabilities: Optional[Tuple[float, float, float]],
    normal_odds: List[float],
    risk_config: Dict[str, float],
) -> Tuple[Tuple[float, float, float], Tuple[float, float, float], List[Optional[float]], List[float], List[float]]:
    market_probs = market_probabilities_from_odds(normal_odds, None)
    fused_probs = fuse_wdl_probabilities(probabilities, market_probs, risk_config.get("model_weight", 0.4))
    ev = [
        fused_probs[idx] * normal_odds[idx] - 1 if normal_odds[idx] > 0 and fused_probs[idx] > 0 else None
        for idx in range(3)
    ]
    kelly_fractions = kelly_fractions_for_match(
        fused_probs,
        normal_odds,
        risk_config.get("kelly_fraction", 0.25),
        risk_config.get("min_edge", 0.05),
    )
    kelly_stakes = kelly_stakes_for_match(
        fused_probs,
        normal_odds,
        risk_config.get("bankroll", 10.0),
        risk_config.get("kelly_fraction", 0.25),
        risk_config.get("min_edge", 0.05),
        risk_config.get("min_stake", 2.0),
        risk_config.get("max_stake_per_pick", 5.0),
    )
    return market_probs, fused_probs, ev, kelly_fractions, kelly_stakes


def parse_attrs(tag: str) -> Dict[str, str]:
    return {k.lower(): normalize_text(v) for k, v in re.findall(r'(\w+)="([^"]*)"', tag)}


def parse_float_triplet(part: str) -> List[float]:
    nums = []
    for item in part.split():
        try:
            nums.append(float(item))
        except ValueError:
            nums.append(0.0)
    while len(nums) < 3:
        nums.append(0.0)
    return nums[:3]


def poisson_probs(lam: float, max_goals: int = 7) -> List[float]:
    return [math.exp(-lam) * (lam ** goals) / math.factorial(goals) for goals in range(max_goals + 1)]


def dixon_coles_tau(home_goals: int, away_goals: int, home_lam: float, away_lam: float, rho: float) -> float:
    if home_goals == 0 and away_goals == 0:
        return 1.0 - (home_lam * away_lam * rho)
    if home_goals == 0 and away_goals == 1:
        return 1.0 + (home_lam * rho)
    if home_goals == 1 and away_goals == 0:
        return 1.0 + (away_lam * rho)
    if home_goals == 1 and away_goals == 1:
        return 1.0 - rho
    return 1.0


def score_grid(home_lam: float, away_lam: float, max_goals: int = 7, rho: float = 0.0) -> List[Tuple[int, int, float]]:
    home_probs = poisson_probs(home_lam, max_goals)
    away_probs = poisson_probs(away_lam, max_goals)
    grid = [
        (h, a, max(home_probs[h] * away_probs[a] * dixon_coles_tau(h, a, home_lam, away_lam, rho), 1e-12))
        for h in range(max_goals + 1)
        for a in range(max_goals + 1)
    ]
    total = sum(prob for _, _, prob in grid)
    return [(h, a, prob / total) for h, a, prob in grid]


def rerank_score_grid(
    grid: List[Tuple[int, int, float]],
    lambda_home: float,
    lambda_away: float,
    market_home: float = 0.0,
    market_draw: float = 0.0,
    market_away: float = 0.0,
) -> List[Tuple[int, int, float]]:
    total_goals = lambda_home + lambda_away
    goal_diff = lambda_home - lambda_away
    adjusted: List[Tuple[int, int, float]] = []

    for home_goals, away_goals, prob in grid:
        weight = 1.0
        score_sum = home_goals + away_goals
        if score_sum <= 2:
            weight *= 1.06
        if (home_goals, away_goals) in {(1, 1), (1, 0), (0, 1), (2, 1), (2, 0), (0, 0), (1, 2)}:
            weight *= 1.05
        if home_goals == away_goals and score_sum <= 2:
            weight *= 1.12
        if (home_goals, away_goals) == (0, 0) and total_goals <= 2.6:
            weight *= 1.10
        if total_goals <= 2.2 and score_sum >= 4:
            weight *= 0.88
        if total_goals <= 2.4 and score_sum >= 3:
            weight *= 0.94
        if total_goals >= 3.2 and score_sum <= 1:
            weight *= 0.90
        if market_draw >= max(market_home, market_away) and home_goals == away_goals:
            weight *= 1.08
        if market_draw >= 0.20 and home_goals == away_goals and score_sum <= 2:
            weight *= 1.06
        if goal_diff >= 0.45 and home_goals > away_goals:
            weight *= 1.06
        if goal_diff <= -0.45 and away_goals > home_goals:
            weight *= 1.06
        if abs(goal_diff) >= 0.7 and abs(home_goals - away_goals) >= 2:
            weight *= 1.04
        adjusted.append((home_goals, away_goals, prob * weight))

    total = sum(prob for _, _, prob in adjusted)
    return [(home_goals, away_goals, prob / total) for home_goals, away_goals, prob in adjusted]


def score_prediction_from_wdl(probabilities: Tuple[float, float, float]) -> dict:
    """Infer a simple Poisson score model from W/D/L probabilities.

    This is a transparent approximation, not a calibrated score market. It is
    sufficient for ranking likely scores and checking whether a W/D/L view
    implies a low- or high-scoring game.
    """

    best_error = float("inf")
    best_lambdas = (1.35, 1.10)
    # Search realistic international football scoring ranges.
    values = [round(0.25 + step * 0.05, 2) for step in range(76)]
    for home_lam in values:
        for away_lam in values:
            home_win = draw = away_win = 0.0
            for home_goals, away_goals, prob in score_grid(home_lam, away_lam, max_goals=6):
                if home_goals > away_goals:
                    home_win += prob
                elif home_goals == away_goals:
                    draw += prob
                else:
                    away_win += prob
            error = (
                (home_win - probabilities[0]) ** 2
                + (draw - probabilities[1]) ** 2
                + (away_win - probabilities[2]) ** 2
            )
            if error < best_error:
                best_error = error
                best_lambdas = (home_lam, away_lam)

    grid = score_grid(best_lambdas[0], best_lambdas[1], max_goals=7)
    grid = rerank_score_grid(grid, best_lambdas[0], best_lambdas[1], probabilities[0], probabilities[1], probabilities[2])
    top_scores = sorted(grid, key=lambda item: item[2], reverse=True)[:3]
    over_25 = sum(prob for home_goals, away_goals, prob in grid if home_goals + away_goals >= 3)
    both_score = sum(prob for home_goals, away_goals, prob in grid if home_goals > 0 and away_goals > 0)
    return {
        "lambda_home": best_lambdas[0],
        "lambda_away": best_lambdas[1],
        "top_scores": top_scores,
        "over_25": over_25,
        "both_score": both_score,
    }


def clamp_exp(value: float) -> float:
    return math.exp(max(min(value, 8.0), -8.0))


def dot(left: Iterable[float], right: Iterable[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def market_probabilities_from_odds(normal_odds: List[float], fallback: Optional[Tuple[float, float, float]]) -> Tuple[float, float, float]:
    if len(normal_odds) >= 3 and all(value > 0 for value in normal_odds[:3]):
        implied = [1.0 / value for value in normal_odds[:3]]
        total = sum(implied)
        if total > 0:
            return implied[0] / total, implied[1] / total, implied[2] / total
    if fallback:
        return fallback
    return (0.0, 0.0, 0.0)


def calibrate_wdl_probabilities(probabilities: Tuple[float, float, float]) -> Tuple[float, float, float]:
    home, draw, away = probabilities
    total = home + draw + away
    if total <= 0:
        return probabilities
    home, draw, away = home / total, draw / total, away / total

    favorite_idx = 0 if home >= away else 2
    favorite = max(home, away)
    draw_target = DRAW_PROBABILITY_FLOOR
    if abs(home - away) <= 0.20:
        draw_target = max(draw_target, BALANCED_MATCH_DRAW_FLOOR)
    if favorite >= 0.60:
        draw_target = max(draw_target, STRONG_FAVORITE_DRAW_FLOOR)

    boost = min(max(draw_target - draw, 0.0), MAX_DRAW_CALIBRATION_BOOST)
    if boost <= 0:
        return home, draw, away

    if favorite_idx == 0:
        home = max(home - boost, 0.0)
    else:
        away = max(away - boost, 0.0)
    draw += boost

    underdog_idx = 2 if home >= away else 0
    underdog = away if underdog_idx == 2 else home
    favorite = home if underdog_idx == 2 else away
    underdog_boost = min(max(UNDERDOG_PROBABILITY_FLOOR - underdog, 0.0), MAX_UNDERDOG_CALIBRATION_BOOST)
    if underdog_boost > 0 and favorite > underdog_boost:
        if underdog_idx == 2:
            home -= underdog_boost
            away += underdog_boost
        else:
            away -= underdog_boost
            home += underdog_boost

    total = home + draw + away
    return home / total, draw / total, away / total


def stage_flags(stage: str, round_text: str) -> Tuple[float, float]:
    lowered = f"{stage} {round_text}".lower()
    neutral_flag = 1.0
    knockout_flag = 1.0 if any(token in lowered for token in ("决赛", "淘汰", "1/", "semi", "quarter", "knockout")) else 0.0
    return neutral_flag, knockout_flag


def load_prematch_team_news(path: Path) -> Dict[Tuple[str, str, str], Dict[str, object]]:
    if not path.exists():
        return {}
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(rows, list):
        return {}
    index: Dict[Tuple[str, str, str], Dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = (
            normalize_text(str(row.get("match_time", ""))),
            normalize_text(str(row.get("home_team", ""))),
            normalize_text(str(row.get("away_team", ""))),
        )
        if key[0] and key[1] and key[2]:
            index[key] = row
    return index


def prematch_adjustments(prematch: Optional[Dict[str, object]]) -> Tuple[float, float, Dict[str, float]]:
    if not prematch:
        return 1.0, 1.0, {}

    def f(name: str) -> float:
        value = prematch.get(name, 0)
        try:
            return float(str(value))
        except (TypeError, ValueError):
            return 0.0

    home_mul = 1.0
    away_mul = 1.0

    home_mul *= max(0.7, 1.0 - 0.05 * f("home_injury_count"))
    away_mul *= max(0.7, 1.0 - 0.05 * f("away_injury_count"))
    home_mul *= max(0.75, 1.0 - 0.08 * f("home_suspension_count"))
    away_mul *= max(0.75, 1.0 - 0.08 * f("away_suspension_count"))

    if f("home_rotation_flag") >= 1:
        home_mul *= 0.94
    if f("away_rotation_flag") >= 1:
        away_mul *= 0.94
    if f("must_win_flag_home") >= 1:
        home_mul *= 1.06
    if f("must_win_flag_away") >= 1:
        away_mul *= 1.06

    metadata = {
        "home_injury_count": f("home_injury_count"),
        "away_injury_count": f("away_injury_count"),
        "home_suspension_count": f("home_suspension_count"),
        "away_suspension_count": f("away_suspension_count"),
        "home_rotation_flag": f("home_rotation_flag"),
        "away_rotation_flag": f("away_rotation_flag"),
        "must_win_flag_home": f("must_win_flag_home"),
        "must_win_flag_away": f("must_win_flag_away"),
        "home_lineup_known": f("home_lineup_known"),
        "away_lineup_known": f("away_lineup_known"),
    }
    return home_mul, away_mul, metadata


def prematch_float(prematch: Dict[str, object], key: str) -> float:
    value = prematch.get(key, 0)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0


def score_prediction_from_trained_model(
    score_model: Optional[Dict[str, object]],
    home: str,
    away: str,
    stage: str,
    round_text: str,
    match_time: str,
    normal_odds: List[float],
    fallback_probabilities: Optional[Tuple[float, float, float]],
    strengths: Dict[str, float],
    prematch_news_index: Optional[Dict[Tuple[str, str, str], Dict[str, object]]] = None,
) -> Optional[dict]:
    if not score_model:
        return None
    feature_names = score_model.get("feature_names", [])
    home_model = score_model.get("home_model", {})
    away_model = score_model.get("away_model", {})
    home_weights = home_model.get("weights", []) if isinstance(home_model, dict) else []
    away_weights = away_model.get("weights", []) if isinstance(away_model, dict) else []
    if not isinstance(feature_names, list) or not home_weights or not away_weights:
        return None

    home_strength = strengths.get(home, 0.0)
    away_strength = strengths.get(away, 0.0)
    home_elo = 1500.0 + home_strength * 220.0
    away_elo = 1500.0 + away_strength * 220.0
    elo_diff = home_elo - away_elo
    market_home, market_draw, market_away = market_probabilities_from_odds(normal_odds, fallback_probabilities)
    neutral_flag, knockout_flag = stage_flags(stage, round_text)

    feature_map = {
        "bias": 1.0,
        "elo_diff_scaled": elo_diff / 100.0,
        "home_elo_scaled": home_elo / 2000.0,
        "away_elo_scaled": away_elo / 2000.0,
        "home_last5_goals_for": 0.0,
        "home_last5_goals_against": 0.0,
        "away_last5_goals_for": 0.0,
        "away_last5_goals_against": 0.0,
        "home_last10_goals_for": 0.0,
        "home_last10_goals_against": 0.0,
        "away_last10_goals_for": 0.0,
        "away_last10_goals_against": 0.0,
        "home_injury_count": 0.0,
        "away_injury_count": 0.0,
        "home_suspension_count": 0.0,
        "away_suspension_count": 0.0,
        "home_lineup_known": 0.0,
        "away_lineup_known": 0.0,
        "home_rotation_flag": 0.0,
        "away_rotation_flag": 0.0,
        "must_win_flag_home": 0.0,
        "must_win_flag_away": 0.0,
        "home_recent_goals_for": 0.0,
        "home_recent_goals_against": 0.0,
        "away_recent_goals_for": 0.0,
        "away_recent_goals_against": 0.0,
        "home_rest_days": 0.0,
        "away_rest_days": 0.0,
        "home_market_prob": market_home,
        "draw_market_prob": market_draw,
        "away_market_prob": market_away,
        "neutral_flag": neutral_flag,
        "knockout_flag": knockout_flag,
    }
    prematch = None
    if prematch_news_index is not None:
        prematch = prematch_news_index.get((normalize_text(match_time), normalize_text(home), normalize_text(away)))
        if prematch:
            feature_map["home_injury_count"] = prematch_float(prematch, "home_injury_count")
            feature_map["away_injury_count"] = prematch_float(prematch, "away_injury_count")
            feature_map["home_suspension_count"] = prematch_float(prematch, "home_suspension_count")
            feature_map["away_suspension_count"] = prematch_float(prematch, "away_suspension_count")
            feature_map["home_lineup_known"] = prematch_float(prematch, "home_lineup_known")
            feature_map["away_lineup_known"] = prematch_float(prematch, "away_lineup_known")
            feature_map["home_rotation_flag"] = prematch_float(prematch, "home_rotation_flag")
            feature_map["away_rotation_flag"] = prematch_float(prematch, "away_rotation_flag")
            feature_map["must_win_flag_home"] = prematch_float(prematch, "must_win_flag_home")
            feature_map["must_win_flag_away"] = prematch_float(prematch, "must_win_flag_away")
    features = [float(feature_map.get(name, 0.0)) for name in feature_names]
    lambda_home = clamp_exp(dot(features, home_weights))
    lambda_away = clamp_exp(dot(features, away_weights))
    home_mul, away_mul, prematch_meta = prematch_adjustments(prematch)
    lambda_home *= home_mul
    lambda_away *= away_mul
    rho = 0.0
    score_correlation = score_model.get("score_correlation", {}) if isinstance(score_model, dict) else {}
    if isinstance(score_correlation, dict):
        try:
            rho = float(score_correlation.get("rho", 0.0))
        except (TypeError, ValueError):
            rho = 0.0
    grid = score_grid(lambda_home, lambda_away, max_goals=7, rho=rho)
    grid = rerank_score_grid(grid, lambda_home, lambda_away, market_home, market_draw, market_away)
    top_scores = sorted(grid, key=lambda item: item[2], reverse=True)[:3]
    over_25 = sum(prob for home_goals, away_goals, prob in grid if home_goals + away_goals >= 3)
    both_score = sum(prob for home_goals, away_goals, prob in grid if home_goals > 0 and away_goals > 0)
    return {
        "lambda_home": lambda_home,
        "lambda_away": lambda_away,
        "top_scores": top_scores,
        "over_25": over_25,
        "both_score": both_score,
        "prematch_adjustments": prematch_meta,
        "source": "trained_score_model",
    }


def score_text(prediction: dict) -> str:
    return " / ".join(f"{home}-{away} ({pct(prob, 1)})" for home, away, prob in prediction["top_scores"])


def simplify_team_name(name: str) -> str:
    return re.sub(r"[^\u4e00-\u9fffA-Za-z]", "", normalize_text(name))


def common_prefix_len(a: str, b: str) -> int:
    count = 0
    for left, right in zip(a, b):
        if left != right:
            break
        count += 1
    return count


def build_team_aliases(model: Dict[Tuple[str, str], Tuple[float, float, float]]) -> Dict[str, str]:
    teams = sorted({team for pair in model for team in pair})
    aliases: Dict[str, str] = {}
    for team in teams:
        aliases[simplify_team_name(team)] = team
    alias_path = ROOT / "config" / "team_aliases.json"
    if alias_path.exists():
        try:
            configured_aliases = json.loads(alias_path.read_text(encoding="utf-8"))
        except Exception:
            configured_aliases = {}
        if isinstance(configured_aliases, dict):
            for canonical, variants in configured_aliases.items():
                canonical_text = normalize_text(str(canonical))
                if not canonical_text:
                    continue
                aliases[simplify_team_name(canonical_text)] = canonical_text
                if isinstance(variants, list):
                    for variant in variants:
                        aliases[simplify_team_name(str(variant))] = canonical_text
    return aliases


def resolve_team_name(name: str, aliases: Dict[str, str]) -> str:
    simplified = simplify_team_name(name)
    if not simplified:
        return name
    exact = aliases.get(simplified)
    if exact:
        return exact

    best_name = name
    best_score = 0
    for alias_key, canonical in aliases.items():
        score = max(common_prefix_len(simplified, alias_key), common_prefix_len(alias_key, simplified))
        if score > best_score:
            best_score = score
            best_name = canonical
    if best_score >= 2:
        return best_name
    return name


def infer_team_strengths(model: Dict[Tuple[str, str], Tuple[float, float, float]]) -> Dict[str, float]:
    strengths: Dict[str, float] = {}
    counts: Dict[str, int] = {}
    for (home, away), (home_win, _draw, away_win) in model.items():
        edge = home_win - away_win
        strengths[home] = strengths.get(home, 0.0) + edge
        strengths[away] = strengths.get(away, 0.0) - edge
        counts[home] = counts.get(home, 0) + 1
        counts[away] = counts.get(away, 0) + 1
    for team, total in list(strengths.items()):
        strengths[team] = total / max(counts.get(team, 1), 1)
    return strengths


def estimate_match_probabilities(home: str, away: str, strengths: Dict[str, float]) -> Optional[Tuple[float, float, float]]:
    if home not in strengths or away not in strengths:
        return None

    delta = strengths[home] - strengths[away] + 0.08
    draw = max(0.18, min(0.30, 0.26 - abs(delta) * 0.12))
    remaining = 1.0 - draw
    home_share = 1.0 / (1.0 + math.exp(-delta * 3.2))
    home_win = remaining * home_share
    away_win = remaining - home_win
    return calibrate_wdl_probabilities((home_win, draw, away_win))


def parse_odds_page(
    page: str,
    model: Dict[Tuple[str, str], Tuple[float, float, float]],
    score_model: Optional[Dict[str, object]] = None,
    risk_config: Optional[Dict[str, float]] = None,
) -> List[dict]:
    risk_config = risk_config or dict(DEFAULT_RISK_CONFIG)
    aliases = build_team_aliases(model)
    strengths = infer_team_strengths(model)
    prematch_news_index = load_prematch_team_news(ROOT / "data" / "raw" / "prematch_team_news.json")
    blocks = re.findall(r'(<tr\b(?=[^>]*\bm="[^"]+")[\s\S]*?</tr>)', page, flags=re.I)
    rows: List[dict] = []
    for block in blocks:
        tag = block.split(">", 1)[0]
        attrs = parse_attrs(tag)
        qtx_match_token = ""
        fenxi_match = re.search(r'https?://live\.qtx\.com/fenxi/([A-Za-z0-9]+)\.html', block)
        if fenxi_match:
            qtx_match_token = fenxi_match.group(1)
        titles = [normalize_text(x) for x in re.findall(r'<a[^>]+title="([^"]+)"[^>]*>', block)]
        if len(titles) < 2:
            continue
        raw_home, raw_away = titles[-2:]
        home = resolve_team_name(raw_home, aliases)
        away = resolve_team_name(raw_away, aliases)
        probs = model.get((home, away))
        if probs is None:
            probs = estimate_match_probabilities(home, away, strengths)
        if probs is not None:
            probs = calibrate_wdl_probabilities(probs)

        match_time = ""
        match_time_match = re.search(r'title="比赛时间:([^"]+)"', block)
        if match_time_match:
            match_time = normalize_text(match_time_match.group(1))

        sp_arr_match = re.search(r'class="spArr"[\s\S]*?value="([^"]*)"', block)
        sp_value = html.unescape(sp_arr_match.group(1)) if sp_arr_match else ""
        parts = sp_value.split("|") if sp_value else []
        normal_odds = parse_float_triplet(parts[0]) if parts else [0.0, 0.0, 0.0]
        handicap_odds = parse_float_triplet(parts[1]) if len(parts) > 1 else [0.0, 0.0, 0.0]
        market_probs, fused_probs, ev, kelly_fractions, kelly_stakes = apply_value_metrics(probs, normal_odds, risk_config)

        stage = "小组赛"
        round_text = ""
        trained_prediction = score_prediction_from_trained_model(
            score_model=score_model,
            home=home,
            away=away,
            stage=stage,
            round_text=round_text,
            match_time=match_time,
            normal_odds=normal_odds,
            fallback_probabilities=probs,
            strengths=strengths,
            prematch_news_index=prematch_news_index,
        )

        rows.append(
            {
                "num": attrs.get("mn", ""),
                "home": home,
                "away": away,
                "qtx_match_token": qtx_match_token,
                "match_time": match_time,
                "deadline": attrs.get("t", ""),
                "handicap": attrs.get("rq", ""),
                "single": attrs.get("dg", ""),
                "probabilities": probs,
                "market_probabilities": market_probs,
                "fused_probabilities": fused_probs,
                "normal_odds": normal_odds,
                "handicap_odds": handicap_odds,
                "ev": ev,
                "kelly_fractions": kelly_fractions,
                "kelly_stakes": kelly_stakes,
                "score_prediction": trained_prediction or (score_prediction_from_wdl(probs) if probs else None),
            }
        )
    return sorted(rows, key=lambda r: (r["match_time"], r["num"]))


def fetch_qtx_future_matches(
    model: Dict[Tuple[str, str], Tuple[float, float, float]],
    score_model: Optional[Dict[str, object]] = None,
    risk_config: Optional[Dict[str, float]] = None,
) -> List[dict]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return []

    risk_config = risk_config or dict(DEFAULT_RISK_CONFIG)
    aliases = build_team_aliases(model)
    strengths = infer_team_strengths(model)
    prematch_news_index = load_prematch_team_news(ROOT / "data" / "raw" / "prematch_team_news.json")
    rows: List[dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 2200})
        page.goto("https://www.qtx.com/worldcup/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
        for anchor in page.locator("a").all():
            try:
                href = anchor.get_attribute("href") or ""
                match = re.search(r"/fenxi/([A-Za-z0-9]+)\.html", href)
                if not match:
                    continue
                text = anchor.inner_text().strip()
                parts = [re.sub(r"\s+", " ", item.strip()) for item in text.splitlines() if item.strip()]
                if len(parts) < 5 or not re.fullmatch(r"\d{2}-\d{2} \d{2}:\d{2}", parts[1]):
                    continue
                if parts[3].lower() != "vs":
                    continue
                home = resolve_team_name(parts[2], aliases)
                away = resolve_team_name(parts[4], aliases)
                probs = model.get((home, away))
                if probs is None:
                    probs = estimate_match_probabilities(home, away, strengths)
                if probs is None:
                    continue
                probs = calibrate_wdl_probabilities(probs)
                match_time = f"2026-{parts[1]}"
                normal_odds = [0.0, 0.0, 0.0]
                handicap_odds = [0.0, 0.0, 0.0]
                market_probs, fused_probs, ev, kelly_fractions, kelly_stakes = apply_value_metrics(probs, normal_odds, risk_config)
                trained_prediction = score_prediction_from_trained_model(
                    score_model=score_model,
                    home=home,
                    away=away,
                    stage="小组赛",
                    round_text="",
                    match_time=match_time,
                    normal_odds=normal_odds,
                    fallback_probabilities=probs,
                    strengths=strengths,
                    prematch_news_index=prematch_news_index,
                )
                rows.append(
                    {
                        "num": "QTX补充",
                        "home": home,
                        "away": away,
                        "qtx_match_token": match.group(1),
                        "match_time": match_time,
                        "deadline": "",
                        "handicap": "",
                        "single": "",
                        "probabilities": probs,
                        "market_probabilities": market_probs,
                        "fused_probabilities": fused_probs,
                        "normal_odds": normal_odds,
                        "handicap_odds": handicap_odds,
                        "ev": ev,
                        "kelly_fractions": kelly_fractions,
                        "kelly_stakes": kelly_stakes,
                        "score_prediction": trained_prediction or (score_prediction_from_wdl(probs) if probs else None),
                    }
                )
            except Exception:
                continue
        browser.close()
    return rows


def merge_candidate_rows(primary_rows: List[dict], extra_rows: List[dict]) -> List[dict]:
    merged: Dict[Tuple[str, str, str], dict] = {}
    merge_model = {
        (str(row.get("home", "")), str(row.get("away", ""))): (0.34, 0.32, 0.34)
        for row in primary_rows + extra_rows
        if row.get("home") and row.get("away")
    }
    aliases = build_team_aliases(merge_model)

    def key_for(row: dict) -> Tuple[str, str, str]:
        home = resolve_team_name(str(row.get("home", "")), aliases)
        away = resolve_team_name(str(row.get("away", "")), aliases)
        return normalize_text(str(row.get("match_time", ""))), normalize_text(home), normalize_text(away)

    for row in primary_rows:
        key = key_for(row)
        merged[key] = row
    for row in extra_rows:
        key = key_for(row)
        existing = merged.get(key)
        if existing is None:
            merged[key] = row
        elif not existing.get("qtx_match_token") and row.get("qtx_match_token"):
            existing["qtx_match_token"] = row.get("qtx_match_token")
    return sorted(merged.values(), key=lambda item: (item.get("match_time", ""), item.get("home", ""), item.get("away", "")))


def is_qtx_supplement(row: dict) -> bool:
    return str(row.get("num", "")) == "QTX补充"


def prioritize_primary_rows(rows: List[dict]) -> List[dict]:
    return sorted(rows, key=lambda row: (is_qtx_supplement(row), row.get("match_time", ""), row.get("home", ""), row.get("away", "")))


def probability_text(probabilities: Optional[Tuple[float, float, float]]) -> str:
    if not probabilities:
        return "待补模型"
    return " / ".join(pct(x) for x in probabilities)


def score_prediction_text(prediction: Optional[dict]) -> str:
    if not prediction:
        return "待补模型"
    return score_text(prediction)


def pct(value: float, digits: int = 0) -> str:
    return f"{value * 100:.{digits}f}%"


def kelly_fraction_text(fraction: float) -> str:
    if fraction > 0:
        denominator = round(1 / fraction)
        if denominator > 0 and abs(fraction - 1 / denominator) < 1e-9:
            return f"1/{denominator} Kelly"
    return f"{fraction * 100:.0f}% Kelly"


def odds_text(odds: Iterable[float]) -> str:
    values = list(odds)
    if not values or all(v <= 0 for v in values):
        return "普通胜平负未开"
    return " / ".join(f"{v:.2f}" for v in values)


def ev_text(value: Optional[float]) -> str:
    if value is None:
        return "只开让球盘"
    return f"{value * 100:+.1f}%"


def ev_class(value: Optional[float]) -> str:
    if value is None:
        return "muted"
    if value > 0:
        return "pos"
    if value > -0.02:
        return "near"
    return "neg"


def group_update_html() -> str:
    standings, best_thirds = build_group_standings()
    if not standings:
        return "<p>暂未读取到官方已完赛小组赛赛果，积分区块待赛果数据更新后自动生成。</p>"
    group_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(row['group'])}</td>"
        f"<td>{row['rank']}</td>"
        f"<td>{html.escape(row['team'])}</td>"
        f"<td>{row['played']}</td>"
        f"<td>{row['win']}-{row['draw']}-{row['loss']}</td>"
        f"<td>{row['gf']}/{row['ga']}</td>"
        f"<td>{row['gd']:+d}</td>"
        f"<td>{row['points']}</td>"
        "</tr>"
        for row in standings
    )
    third_rows = "\n".join(
        "<tr>"
        f"<td>{row.get('third_rank', '')}</td>"
        f"<td>{html.escape(row['group'])}</td>"
        f"<td>{html.escape(row['team'])}</td>"
        f"<td>{row['played']}</td>"
        f"<td>{row['gf']}/{row['ga']}</td>"
        f"<td>{row['gd']:+d}</td>"
        f"<td>{row['points']}</td>"
        f"<td>{'暂列晋级线' if row.get('third_rank', 99) <= 8 else '暂列淘汰线'}</td>"
        "</tr>"
        for row in best_thirds
    )
    third_rows_html = third_rows or '<tr><td colspan="8">暂无第三名排序。</td></tr>'
    return f"""
<h3>小组实时积分</h3>
<table><thead><tr><th>组</th><th>排名</th><th>球队</th><th>赛</th><th>胜-平-负</th><th>进/失</th><th>净胜</th><th>分</th></tr></thead><tbody>
{group_rows}
</tbody></table>
<h3>最佳第三名排序</h3>
<table><thead><tr><th>排名</th><th>组</th><th>球队</th><th>赛</th><th>进/失</th><th>净胜</th><th>分</th><th>状态</th></tr></thead><tbody>
{third_rows_html}
</tbody></table>
<p class="muted">排序规则按积分、净胜球、进球数、球队名做临时排序；官方同分细则仍以 FIFA 为准。</p>
"""


def best_ev_remark(row: dict) -> str:
    ev = row["ev"]
    odds = row["normal_odds"]
    probabilities = row_probabilities(row)
    kelly_fractions = row.get("kelly_fractions") or [0.0, 0.0, 0.0]
    kelly_stakes = row.get("kelly_stakes") or [0.0, 0.0, 0.0]
    if not probabilities:
        if all(x <= 0 for x in odds):
            return f"已抓到实时赛程；普通胜平负未开，当前让球 {row['handicap']}，让球赔率 {odds_text(row['handicap_odds'])}。"
        return "已抓到实时赔率，但当前模型未覆盖此对阵，暂不计算 EV 和 Kelly。"
    if all(x <= 0 for x in odds):
        return f"普通胜平负未开；当前让球 {row['handicap']}，让球赔率 {odds_text(row['handicap_odds'])}。"
    best_idx = max(range(3), key=lambda i: (kelly_fractions[i], ev[i] if ev[i] is not None else -999))
    best = ev[best_idx]
    if kelly_fractions[best_idx] > 0 and kelly_stakes[best_idx] > 0:
        return f"最佳方向：{OUTCOME_NAMES[best_idx]} EV {ev_text(best)}，Kelly {kelly_fractions[best_idx] * 100:.1f}%资金，建议 {kelly_stakes[best_idx]:.1f} 元。"
    if best is not None and best > 0:
        return f"最佳 EV：{OUTCOME_NAMES[best_idx]} {ev_text(best)}，但 Kelly 不足最低投注门槛，建议观望。"
    if best is not None and best > -0.02:
        return f"接近保本：{OUTCOME_NAMES[best_idx]} {ev_text(best)}。"
    return "热门方向赔率被压低，暂不作主推。"


def select_candidates(rows: List[dict]) -> List[Tuple[float, dict, int]]:
    candidates: List[Tuple[float, dict, int]] = []
    for row in rows:
        kelly_fractions = row.get("kelly_fractions") or [0.0, 0.0, 0.0]
        for idx, value in enumerate(row["ev"]):
            kelly_value = kelly_fractions[idx] if idx < len(kelly_fractions) else 0.0
            if kelly_value > 0 or (value is not None and value > -0.02):
                candidates.append((kelly_value, row, idx))
    return sorted(candidates, key=lambda item: (item[0], item[1]["ev"][item[2]] or -999), reverse=True)


def select_ticket(rows: List[dict], risk_config: Optional[Dict[str, float]] = None) -> List[Tuple[dict, int]]:
    risk_config = risk_config or dict(DEFAULT_RISK_CONFIG)
    picks: List[Tuple[dict, int]] = []
    used_matches = set()
    total_stake = 0.0
    max_total_stake = risk_config.get("max_total_stake", 15.0)
    for value, row, idx in select_candidates(rows):
        kelly_stakes = row.get("kelly_stakes") or [0.0, 0.0, 0.0]
        stake = kelly_stakes[idx] if idx < len(kelly_stakes) else 0.0
        match_key = (row["num"], row["home"], row["away"])
        if value <= 0 or stake <= 0 or match_key in used_matches:
            continue
        if total_stake + stake > max_total_stake:
            continue
        picks.append((row, idx))
        used_matches.add(match_key)
        total_stake += stake
        if len(picks) >= 5:
            break
    return picks


def ticket_metrics(picks: List[Tuple[dict, int]], stake_per_pick: float = 2.0) -> Tuple[float, float, float, float]:
    if not picks:
        return 0.0, 0.0, 0.0, 0.0
    total_stake = stake_per_pick * len(picks)
    expected_return = sum(row["probabilities"][idx] * row["normal_odds"][idx] * stake_per_pick for row, idx in picks)

    grouped: Dict[Tuple[str, str, str], dict] = {}
    for row, idx in picks:
        key = (row["num"], row["home"], row["away"])
        grouped.setdefault(key, {"row": row, "indices": []})["indices"].append(idx)
    groups = list(grouped.values())

    profit_probability = 0.0

    def walk(position: int, probability: float, odds_sum: float) -> None:
        nonlocal profit_probability
        if position == len(groups):
            if odds_sum * stake_per_pick > total_stake:
                profit_probability += probability
            return
        group = groups[position]
        row = group["row"]
        selected = set(group["indices"])
        for outcome_idx, outcome_probability in enumerate(row["probabilities"]):
            add = row["normal_odds"][outcome_idx] if outcome_idx in selected else 0.0
            walk(position + 1, probability * outcome_probability, odds_sum + add)

    walk(0, 1.0, 0.0)
    roi = expected_return / total_stake - 1
    return total_stake, expected_return, roi, profit_probability


def ticket_metrics_with_stakes(picks: List[Tuple[dict, int]]) -> Tuple[float, float, float, float, List[dict]]:
    if not picks:
        return 0.0, 0.0, 0.0, 0.0, []

    stake_details: List[dict] = []
    total_stake = 0.0
    expected_return = 0.0
    for row, idx in picks:
        probabilities = row_probabilities(row)
        odds = row["normal_odds"][idx]
        stake = (row.get("kelly_stakes") or [0.0, 0.0, 0.0])[idx]
        kelly_value = (row.get("kelly_fractions") or [0.0, 0.0, 0.0])[idx]
        stake_details.append({"stake": stake, "kelly_fraction": kelly_value})
        total_stake += stake
        if probabilities and odds > 0 and stake > 0:
            expected_return += probabilities[idx] * odds * stake

    grouped: Dict[Tuple[str, str, str], dict] = {}
    for pos, (row, idx) in enumerate(picks):
        key = (row["num"], row["home"], row["away"])
        group = grouped.setdefault(key, {"row": row, "indices": [], "stakes": {}})
        group["indices"].append(idx)
        group["stakes"][idx] = stake_details[pos]["stake"]
    groups = list(grouped.values())

    profit_probability = 0.0

    def walk(position: int, probability: float, return_sum: float) -> None:
        nonlocal profit_probability
        if position == len(groups):
            if return_sum > total_stake:
                profit_probability += probability
            return
        group = groups[position]
        row = group["row"]
        probabilities = row_probabilities(row)
        if not probabilities:
            return
        selected = set(group["indices"])
        stakes = group["stakes"]
        for outcome_idx, outcome_probability in enumerate(probabilities):
            outcome_return = 0.0
            if outcome_idx in selected:
                outcome_return = row["normal_odds"][outcome_idx] * stakes.get(outcome_idx, 0.0)
            walk(position + 1, probability * outcome_probability, return_sum + outcome_return)

    walk(0, 1.0, 0.0)
    roi = expected_return / total_stake - 1 if total_stake > 0 else 0.0
    return total_stake, expected_return, roi, profit_probability, stake_details


def row_html(row: dict) -> str:
    probs = row["probabilities"]
    fused_probs = row.get("fused_probabilities") or probs
    evs = row["ev"]
    kelly_fractions = row.get("kelly_fractions") or [0.0, 0.0, 0.0]
    probability_cell = probability_text(probs)
    fused_probability_cell = probability_text(fused_probs)
    ev_cells = " / ".join(f'<span class="{ev_class(x)}">{ev_text(x)}</span>' for x in evs)
    kelly_cells = " / ".join(f"{value * 100:.1f}%" for value in kelly_fractions)
    return (
        "<tr>"
        f"<td>{html.escape(row['num'])}</td>"
        f"<td>{html.escape(row['match_time'])}</td>"
        f"<td>{html.escape(row['home'])} vs {html.escape(row['away'])}</td>"
        f"<td>{html.escape(probability_cell)}</td>"
        f"<td>{html.escape(fused_probability_cell)}</td>"
        f"<td>{html.escape(score_prediction_text(row['score_prediction']))}</td>"
        f"<td>{html.escape(odds_text(row['normal_odds']))}</td>"
        f"<td>{ev_cells}</td>"
        f"<td>{html.escape(kelly_cells)}</td>"
        f"<td>{html.escape(best_ev_remark(row))}</td>"
        "</tr>"
    )


def generate_report(rows: List[dict], odds_url: str, generated_at: dt.datetime, risk_config: Optional[Dict[str, float]] = None) -> str:
    risk_config = risk_config or dict(DEFAULT_RISK_CONFIG)
    candidates = select_candidates(rows)
    picks = select_ticket(rows, risk_config)
    stake, expected_return, roi, profit_probability, stake_details = ticket_metrics_with_stakes(picks)
    modeled_rows = [row for row in rows if row["probabilities"]]
    display_rows = prioritize_primary_rows(rows)

    focus = display_rows[:2]
    focus_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(row['match_time'])}</td>"
        f"<td>{html.escape(row['home'])} vs {html.escape(row['away'])}</td>"
        f"<td>{html.escape(probability_text(row['probabilities']))}</td>"
        f"<td>{html.escape(score_prediction_text(row['score_prediction']))}</td>"
        f"<td>{html.escape(odds_text(row['normal_odds']))}</td>"
        f"<td>{html.escape(best_ev_remark(row))}</td>"
        "</tr>"
        for row in focus
    )

    candidate_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(row['num'])}</td>"
        f"<td>{html.escape(row['home'])} vs {html.escape(row['away'])}</td>"
        f"<td>{OUTCOME_NAMES[idx]}</td>"
        f"<td>{pct(row['probabilities'][idx], 1)}</td>"
        f"<td>{pct((row.get('fused_probabilities') or row['probabilities'])[idx], 1)}</td>"
        f"<td>{row['normal_odds'][idx]:.2f}</td>"
        f"<td>{1 / (row.get('fused_probabilities') or row['probabilities'])[idx]:.2f}</td>"
        f"<td class=\"{ev_class(row['ev'][idx])}\">{ev_text(row['ev'][idx])}</td>"
        f"<td>{(row.get('kelly_fractions') or [0.0, 0.0, 0.0])[idx] * 100:.1f}%</td>"
        "</tr>"
        for value, row, idx in candidates[:12]
    )

    ticket_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(row['num'])}</td>"
        f"<td>{html.escape(row['home'])} vs {html.escape(row['away'])}</td>"
        f"<td>{OUTCOME_NAMES[idx]}</td>"
        f"<td>{pct((row.get('fused_probabilities') or row['probabilities'])[idx], 1)}</td>"
        f"<td>{row['normal_odds'][idx]:.2f}</td>"
        f"<td>{ev_text(row['ev'][idx])}</td>"
        f"<td>{(row.get('kelly_fractions') or [0.0, 0.0, 0.0])[idx] * 100:.1f}%</td>"
        f"<td>{detail['stake']:.1f} 元</td>"
        "</tr>"
        for (row, idx), detail in zip(picks, stake_details)
    )
    if not ticket_rows:
        ticket_rows = '<tr><td colspan="8">当前没有满足 Kelly 风控条件的正 EV 主票，建议跳过。</td></tr>'

    no_normal = [
        f"{row['home']} vs {row['away']}"
        for row in rows
        if all(x <= 0 for x in row["normal_odds"])
    ]
    no_normal_text = "、".join(no_normal) if no_normal else "无"
    kelly_label = kelly_fraction_text(risk_config.get("kelly_fraction", 0.25))

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>世界杯每日预测与购彩报告</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; line-height: 1.55; color: #172033; background: #f6f8fb; margin: 0; padding: 24px; }}
main {{ max-width: 1180px; margin: 0 auto; background: #fff; padding: 28px; border: 1px solid #d9e1ec; }}
h1 {{ margin: 0 0 8px; font-size: 28px; }}
h2 {{ margin: 24px 0 10px; font-size: 19px; }}
table {{ width: 100%; border-collapse: collapse; margin: 10px 0 16px; font-size: 13px; }}
th, td {{ border: 1px solid #dfe6ef; padding: 8px; vertical-align: top; }}
th {{ background: #eef3f8; text-align: left; }}
.stamp, .muted {{ color: #617086; }}
.notice {{ background: #fff8df; border-left: 4px solid #d99a00; padding: 10px 12px; }}
.ticket {{ background: #f2fff7; border-left: 4px solid #1b9c66; padding: 10px 12px; }}
.pos {{ color: #087f4f; font-weight: 700; }}
.near {{ color: #9a6500; font-weight: 700; }}
.neg {{ color: #8a2432; }}
</style>
</head>
<body>
<main>
<h1>世界杯每日预测与购彩报告</h1>
<p class="stamp">生成时间：{generated_at.strftime("%Y-%m-%d %H:%M:%S")}（本机时间）</p>
<p class="notice">所有概率均为模型与市场赔率的保守融合估计，不是保证命中，也不是官方建议。购彩建议只用于控制风险和比较赔率价值，请控制预算、少串关，不要把预测当确定收益。</p>

<section id="summary">
<h2>本轮摘要</h2>
<p>当前抓取到可售世界杯场次 {len(rows)} 场，其中模型已覆盖 {len(modeled_rows)} 场。普通胜平负未开场次：{html.escape(no_normal_text)}。</p>
</section>

<section id="matches-24h">
<h2>今日/未来 24 小时重点比赛</h2>
<table><thead><tr><th>开赛时间</th><th>场次</th><th>模型胜平负概率</th><th>最可能比分</th><th>当前不让球赔率</th><th>判断</th></tr></thead><tbody>
{focus_rows}
</tbody></table>
</section>

<section id="group-update">
<h2>小组名次和最佳第三名变化</h2>
{group_update_html()}
</section>

<section id="live-postmatch">
<h2>赛前/实时/赛后更新</h2>
<p>当前脚本主要完成赔率、概率和 EV 自动化；首发、伤停、天气和实时赛况建议在赛前 60-90 分钟接入可靠数据源后刷新。</p>
</section>

<section id="remaining-probabilities">
<h2>当前剩余可售世界杯场次</h2>
<table><thead><tr><th>比赛编号</th><th>开赛时间</th><th>场次</th><th>模型胜平负概率（主胜/平/客胜）</th><th>融合概率</th><th>最可能比分</th><th>当前不让球赔率（主胜/平/客胜）</th><th>EV（主胜/平/客胜）</th><th>Kelly%</th><th>备注</th></tr></thead><tbody>
{''.join(row_html(row) for row in display_rows)}
</tbody></table>
</section>

<section id="ev-board">
<h2>赔率与 EV 筛选</h2>
<p>EV = 融合概率 × 当前赔率 - 1；Kelly% 使用 {kelly_label}，并受单注与总投入上限约束。</p>
<table><thead><tr><th>编号</th><th>场次</th><th>选项</th><th>模型概率</th><th>融合概率</th><th>赔率</th><th>保本赔率</th><th>EV</th><th>Kelly%</th></tr></thead><tbody>
{candidate_rows or '<tr><td colspan="9">当前没有正 EV 或接近正 EV 的普通胜平负选项。</td></tr>'}
</tbody></table>
</section>

<section id="ticket">
<h2>最终建议票单</h2>
<div class="ticket">
<p><b>主建议：单关/分散买，少串关。</b> Kelly 资金分配策略（{kelly_label}，资金量 {risk_config.get('bankroll', 10.0):.0f} 元）：共 {len(picks)} 注，合计 {stake:.2f} 元；融合概率期望返还约 {expected_return:.2f} 元，ROI 约 {roi * 100:.1f}%；整组最终赚钱概率约 {profit_probability * 100:.1f}%。</p>
</div>
<table><thead><tr><th>编号</th><th>场次</th><th>购买选项</th><th>融合概率</th><th>赔率</th><th>EV</th><th>Kelly%</th><th>建议投注额</th></tr></thead><tbody>
{ticket_rows}
</tbody></table>
</section>

<section id="changes">
<h2>与上一版变化</h2>
<p>当前版本会为每次运行保存带时间戳的报告、截图和预测快照，可据此回看赔率、EV、推荐票单与赛后结果的偏差。</p>
</section>

<section id="risks">
<h2>关键风险因素</h2>
<ul>
<li>临场首发、伤停、红黄牌和天气变化会影响概率。</li>
<li>赔率临近停售会变化，下单前必须重新计算。</li>
<li>部分强弱悬殊场次只开让球盘，本报告不会在缺少净胜球分布时强推让球。</li>
<li>模型概率需要持续校准；示例概率不应直接视为长期稳定优势。</li>
</ul>
</section>

<section id="sources">
<h2>来源</h2>
<ul>
<li><a href="{html.escape(odds_url)}">足彩网竞彩足球胜平负/让球赔率页</a></li>
<li><a href="https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/scores-fixtures">FIFA 2026 World Cup scores and fixtures</a></li>
</ul>
</section>
</main>
</body>
</html>
"""


def render_report_screenshot(report_path: Path, screenshot_path: Path) -> bool:
    """Render the HTML report to a full-page PNG screenshot.

    Playwright is imported lazily so the analysis/report generation still works
    even if a user has not installed screenshot support yet.
    """

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        print(f"Screenshot skipped: Playwright is not available ({exc}).", file=sys.stderr)
        return False

    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1800, "height": 2200}, device_scale_factor=2)
        page.goto(report_path.resolve().as_uri(), wait_until="networkidle")
        page.screenshot(path=str(screenshot_path), full_page=True)
        browser.close()
    return True


def multipart_body(fields: Dict[str, str], file_field: str, file_path: Path) -> Tuple[bytes, str]:
    boundary = f"----WorldCupAgent{uuid.uuid4().hex}"
    chunks: List[bytes] = []
    for key, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        chunks.append(value.encode("utf-8"))
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}\r\n".encode("utf-8"))
    chunks.append(
        (
            f'Content-Disposition: form-data; name="{file_field}"; '
            f'filename="{file_path.name}"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode("utf-8")
    )
    chunks.append(file_path.read_bytes())
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), boundary


def send_telegram_screenshot(env: Dict[str, str], screenshot_path: Path, generated_at: dt.datetime) -> None:
    if env.get("SEND_TELEGRAM", "0") not in {"1", "true", "TRUE", "yes"}:
        return
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = env.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("Telegram skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing.", file=sys.stderr)
        return
    if not screenshot_path.exists():
        print(f"Telegram skipped: screenshot does not exist: {screenshot_path}", file=sys.stderr)
        return

    caption = (
        "世界杯每日预测更新\n"
        f"生成时间：{generated_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
        "完整高清报告截图见附件。"
    )
    payload, boundary = multipart_body(
        {
            "chat_id": chat_id,
            "caption": caption,
            "disable_web_page_preview": "true",
        },
        "document",
        screenshot_path,
    )
    url = f"https://api.telegram.org/bot{token}/sendDocument"

    def post(opener: request.OpenerDirector) -> None:
        req = request.Request(
            url,
            data=payload,
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with opener.open(req, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="ignore"))
        if not body.get("ok"):
            raise RuntimeError(f"Telegram API error: {body}")

    try:
        post(request.build_opener())
    except Exception as first_error:
        proxy = env.get("TELEGRAM_PROXY", "")
        if not proxy:
            raise
        try:
            post(request.build_opener(request.ProxyHandler({"http": proxy, "https": proxy})))
        except Exception as second_error:
            print(f"Telegram send failed: {type(second_error).__name__}: {second_error}", file=sys.stderr)
            print(f"First attempt was: {type(first_error).__name__}: {first_error}", file=sys.stderr)


def resolve_report_url(report_path: Path, env: Dict[str, str]) -> str:
    configured = env.get("REPORT_URL", "").strip()
    if not configured or "absolute/path/to" in configured:
        return report_path.resolve().as_uri()
    parsed = parse.urlsplit(configured)
    if parsed.path.endswith(".html"):
        base_path = parsed.path.rsplit("/", 1)[0]
        path = f"{base_path}/{report_path.name}" if base_path else report_path.name
        return parse.urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))
    return configured


def timestamped_path(path: Path, generated_at: dt.datetime) -> Path:
    stamp = generated_at.strftime("%Y%m%d_%H%M%S")
    return path.with_name(f"{path.stem}_{stamp}{path.suffix}")


def serialize_rows(rows: List[dict], generated_at: dt.datetime, odds_url: str) -> Dict[str, object]:
    payload_rows: List[Dict[str, object]] = []
    for row in rows:
        prediction = row.get("score_prediction") or {}
        payload_rows.append(
            {
                "num": row.get("num", ""),
                "match_time": row.get("match_time", ""),
                "deadline": row.get("deadline", ""),
                "home": row.get("home", ""),
                "away": row.get("away", ""),
                "qtx_match_token": row.get("qtx_match_token", ""),
                "probabilities": row.get("probabilities"),
                "market_probabilities": row.get("market_probabilities"),
                "fused_probabilities": row.get("fused_probabilities"),
                "normal_odds": row.get("normal_odds"),
                "handicap_odds": row.get("handicap_odds"),
                "ev": row.get("ev"),
                "kelly_fractions": row.get("kelly_fractions"),
                "kelly_stakes": row.get("kelly_stakes"),
                "score_prediction": {
                    "lambda_home": prediction.get("lambda_home"),
                    "lambda_away": prediction.get("lambda_away"),
                    "top_scores": prediction.get("top_scores"),
                    "over_25": prediction.get("over_25"),
                    "both_score": prediction.get("both_score"),
                    "prematch_adjustments": prediction.get("prematch_adjustments"),
                    "source": prediction.get("source"),
                }
                if prediction
                else None,
            }
        )
    return {
        "generated_at": generated_at.isoformat(),
        "odds_url": odds_url,
        "matches": payload_rows,
    }


def serialize_training_candidates(rows: List[dict], generated_at: dt.datetime) -> Dict[str, object]:
    candidates: List[Dict[str, object]] = []
    for row in rows:
        prediction = row.get("score_prediction") or {}
        prematch = prediction.get("prematch_adjustments") or {}
        candidates.append(
            {
                "generated_at": generated_at.isoformat(),
                "match_time": row.get("match_time", ""),
                "deadline": row.get("deadline", ""),
                "home_team": row.get("home", ""),
                "away_team": row.get("away", ""),
                "competition": "FIFA World Cup",
                "stage": "小组赛",
                "group_name": "",
                "round_text": row.get("num", ""),
                "qtx_match_token": row.get("qtx_match_token", ""),
                "probabilities": row.get("probabilities"),
                "normal_odds": row.get("normal_odds"),
                "handicap_odds": row.get("handicap_odds"),
                "home_injury_count": prematch.get("home_injury_count", 0),
                "away_injury_count": prematch.get("away_injury_count", 0),
                "home_suspension_count": prematch.get("home_suspension_count", 0),
                "away_suspension_count": prematch.get("away_suspension_count", 0),
                "home_lineup_known": prematch.get("home_lineup_known", 0),
                "away_lineup_known": prematch.get("away_lineup_known", 0),
                "home_rotation_flag": prematch.get("home_rotation_flag", 0),
                "away_rotation_flag": prematch.get("away_rotation_flag", 0),
                "must_win_flag_home": prematch.get("must_win_flag_home", 0),
                "must_win_flag_away": prematch.get("must_win_flag_away", 0),
                "lambda_home": prediction.get("lambda_home"),
                "lambda_away": prediction.get("lambda_away"),
                "top_scores": prediction.get("top_scores"),
                "source": "run_snapshot_training_candidate",
            }
        )
    return {"generated_at": generated_at.isoformat(), "rows": candidates}


def filter_nearby_rows(rows: List[dict], generated_at: dt.datetime, days: int = 1) -> List[dict]:
    cutoff = generated_at + dt.timedelta(days=days)
    filtered: List[dict] = []
    for row in rows:
        raw = row.get("match_time", "")
        try:
            match_dt = dt.datetime.strptime(raw, "%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            filtered.append(row)
            continue
        if match_dt <= cutoff:
            filtered.append(row)
    return filtered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default=".env", help="Path to .env file")
    parser.add_argument("--odds-html", help="Use a local odds HTML file instead of fetching")
    parser.add_argument("--days", type=int, default=1, help="Only include matches within N days (default: 1)")
    parser.add_argument("--qtx-supplement", action="store_true", help="Supplement odds rows with QTX future matches")
    parser.add_argument("--latest-copy", action="store_true", help="Also write fixed latest HTML/PNG copy")
    parser.add_argument("--skip-screenshot", action="store_true", help="Write report/snapshots without rendering PNG or sending Telegram")
    args = parser.parse_args()

    env = {**os.environ, **load_env(ROOT / args.env)}
    odds_url = env.get("ODDS_URL", DEFAULT_ODDS_URL)
    report_base_path = ROOT / env.get("REPORT_PATH", "docs/run/worldcup-2026-agent-report.html")
    screenshot_base_path = ROOT / env.get("REPORT_SCREENSHOT_PATH", "docs/run/worldcup-2026-agent-report.png")

    model = load_probability_model(ROOT / "config" / "model_probabilities.json")
    score_model_path = ROOT / env.get("SCORE_MODEL_PATH", "models/score_model/score_model_v1.json")
    score_model = load_score_model(score_model_path)
    risk_config = load_risk_config(ROOT / "config" / "bayesian_calibration.json", env)
    if args.odds_html:
        page = decode_page_bytes(Path(args.odds_html).read_bytes())
    else:
        page = fetch_text(odds_url)

    rows = parse_odds_page(page, model, score_model=score_model, risk_config=risk_config)
    qtx_enabled = args.qtx_supplement or env.get("QTX_SUPPLEMENT", "0").lower() in {"1", "true", "yes", "on"}
    if qtx_enabled:
        qtx_rows = fetch_qtx_future_matches(model, score_model=score_model, risk_config=risk_config)
        rows = merge_candidate_rows(rows, qtx_rows)
    generated_at = dt.datetime.now()
    rows = filter_nearby_rows(rows, generated_at, days=args.days)
    if not rows:
        print("No World Cup odds rows were parsed.", file=sys.stderr)
    report = generate_report(rows, odds_url, generated_at, risk_config=risk_config)
    report_path = timestamped_path(report_base_path, generated_at)
    screenshot_path = timestamped_path(screenshot_base_path, generated_at)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    latest_copy_enabled = args.latest_copy or env.get("WRITE_LATEST_REPORT", "0").lower() in {"1", "true", "yes", "on"}
    snapshot_path = report_path.parent / f"worldcup-2026-agent-predictions_{generated_at.strftime('%Y%m%d_%H%M%S')}.json"
    training_candidate_path = report_path.parent / f"worldcup-2026-agent-training-candidates_{generated_at.strftime('%Y%m%d_%H%M%S')}.json"
    report_path.write_text(report, encoding="utf-8")
    if latest_copy_enabled:
        report_base_path.write_text(report, encoding="utf-8")
    snapshot_path.write_text(json.dumps(serialize_rows(rows, generated_at, odds_url), ensure_ascii=False, indent=2), encoding="utf-8")
    training_candidate_path.write_text(json.dumps(serialize_training_candidates(rows, generated_at), ensure_ascii=False, indent=2), encoding="utf-8")
    screenshot_ok = False
    if not args.skip_screenshot:
        screenshot_ok = render_report_screenshot(report_path, screenshot_path)
        if screenshot_ok and latest_copy_enabled:
            shutil.copyfile(screenshot_path, screenshot_base_path)
        send_telegram_screenshot(env, screenshot_path, generated_at)
    print(f"Report written: {report_path}")
    if latest_copy_enabled:
        print(f"Latest report copy written: {report_base_path}")
    print(f"Prediction snapshot written: {snapshot_path}")
    print(f"Training candidates written: {training_candidate_path}")
    if screenshot_ok:
        print(f"Screenshot written: {screenshot_path}")
        if latest_copy_enabled:
            print(f"Latest screenshot copy written: {screenshot_base_path}")
    if args.skip_screenshot:
        print("Screenshot skipped by --skip-screenshot")
    report_url = resolve_report_url(report_path, env)
    print(f"Report URL: {report_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
