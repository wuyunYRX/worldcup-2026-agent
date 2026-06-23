#!/usr/bin/env python3
"""Prototype fetcher for pre-match team news from QTX qingbao pages."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List
from html import unescape


ROOT = Path(__file__).resolve().parents[1]
RUN_DOCS_DIR = ROOT / "docs" / "run"
OUT_PATH = ROOT / "data" / "raw" / "prematch_team_news.json"
DEFAULT_QTX_QINGBAO_BASE_URL = "https://live.qtx.com/qingbao"
DEFAULT_LLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_LLM_MODEL = "glm-4-flash"
TEAM_VALUE_PROFILES_PATH = ROOT / "config" / "team_value_profiles.json"
MATCH_WEATHER_PATH = ROOT / "data" / "raw" / "match_weather_forecast.json"

INJURY_HINTS = ("伤停", "伤病", "缺阵", "伤缺", "受伤", "复出")
SUSPENSION_HINTS = ("停赛", "禁赛", "红牌", "黄牌停赛")
LINEUP_HINTS = ("预计首发", "首发", "阵容")
ROTATION_HINTS = ("轮换", "替补", "保留主力")
MUST_WIN_HINTS = ("必须取胜", "必须赢", "背水一战", "出线", "晋级")
POSSESSION_HINTS = ("控球", "传控", "控场", "组织推进", "短传渗透")
COUNTER_HINTS = ("反击", "防守反击", "快速转换", "打身后")
PRESSING_HINTS = ("高位逼抢", "压迫", "前场逼抢", "高压")
LOW_BLOCK_HINTS = ("低位防守", "密集防守", "摆大巴")
DIRECT_HINTS = ("长传", "冲吊", "边路冲击", "快速推进")
SET_PIECE_HINTS = ("定位球", "角球", "任意球", "高空球")
FORMATION_HINTS = ("4-3-3", "4-2-3-1", "3-5-2", "5-4-1", "3-4-3", "4-4-2")
VALUE_HINTS = ("总身价", "阵容总身价", "身价达", "身价≥", "来自五大联赛", "五大联赛")
WEATHER_HINTS = ("天气", "气温", "温度", "高温", "湿度", "降雨", "下雨", "雨战", "强风", "风速")


def post_json(api_key: str, url: str, payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "WorldCupAgent/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def extract_responses_text(body: Dict[str, Any]) -> str:
    if isinstance(body.get("output_text"), str):
        return body["output_text"]
    chunks: List[str] = []
    for item in body.get("output", []) or []:
        for content in item.get("content", []) or []:
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "".join(chunks)


def call_llm(api_key: str, prompt: str, base_url: str, model: str, timeout: int) -> str:
    try:
        body = post_json(api_key, f"{base_url.rstrip('/')}/responses", {"model": model, "input": prompt, "store": False, "max_output_tokens": 500}, timeout)
        return extract_responses_text(body)
    except Exception:
        body = post_json(api_key, f"{base_url.rstrip('/')}/chat/completions", {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1, "max_tokens": 500}, timeout)
        choices = body.get("choices", [])
        if choices:
            return str(choices[0].get("message", {}).get("content", ""))
    return ""


def parse_json_object(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return {}
    try:
        payload = json.loads(match.group(0))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


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


def load_team_value_profiles(path: Path = TEAM_VALUE_PROFILES_PATH) -> Dict[str, Dict[str, object]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(team): values for team, values in payload.items() if isinstance(values, dict)}


def load_match_weather_index(path: Path = MATCH_WEATHER_PATH) -> Dict[tuple[str, str, str], Dict[str, object]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, list):
        return {}
    result: Dict[tuple[str, str, str], Dict[str, object]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        key = (str(item.get("match_time", "")), str(item.get("home_team", "")), str(item.get("away_team", "")))
        if all(key):
            result[key] = item
    return result


def merge_weather(entry: Dict[str, object], weather: Dict[str, object]) -> None:
    if not isinstance(weather, dict):
        return
    has_numeric_weather = any(float(weather.get(field) or 0.0) for field in ("temperature_c", "humidity_pct", "wind_kph", "precipitation_mm", "weather_severity"))
    has_existing_weather_summary = bool(entry.get("weather_summary"))
    for field in (
        "temperature_c",
        "humidity_pct",
        "wind_kph",
        "precipitation_mm",
        "weather_severity",
        "weather_summary",
        "weather_source",
        "weather_status",
        "venue_name",
        "venue_city",
        "venue_timezone",
        "venue_indoor",
    ):
        value = weather.get(field)
        if value in (None, "", 0.0, 0):
            continue
        if field in {"weather_summary", "weather_source"} and not has_numeric_weather and has_existing_weather_summary:
            continue
        entry[field] = value


def latest_snapshot() -> Path:
    files = sorted(RUN_DOCS_DIR.glob("worldcup-2026-agent-predictions_*.json"))
    if not files:
        raise FileNotFoundError(f"No prediction snapshot found in {RUN_DOCS_DIR}")
    return files[-1]


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "ignore")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def html_to_text(html: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return normalize(unescape(text))


def qingbao_url(match_id: str, base_url: str = DEFAULT_QTX_QINGBAO_BASE_URL) -> str:
    return f"{base_url.rstrip('/')}/{match_id}.html"


def match_id_from_snapshot_item(item: Dict[str, object]) -> str:
    raw = str(item.get("num", ""))
    return raw


def extract_match_token(snapshot_item: Dict[str, object]) -> str:
    # Current prediction snapshot does not store qtx match id, so use the latest known demo token placeholder only if present.
    token = str(snapshot_item.get("qtx_match_token", ""))
    return token


def count_hits(sentences: List[str], hints: tuple[str, ...], team_name: str) -> int:
    count = 0
    for sentence in sentences:
        if team_name in sentence and any(hint in sentence for hint in hints):
            count += 1
    return count


def detect_style(sentences: List[str], team_name: str) -> str:
    checks = [
        (POSSESSION_HINTS, "possession"),
        (COUNTER_HINTS, "counter"),
        (PRESSING_HINTS, "pressing"),
        (LOW_BLOCK_HINTS, "low_block"),
        (DIRECT_HINTS, "direct"),
        (SET_PIECE_HINTS, "set_piece"),
    ]
    scores: Dict[str, int] = {}
    for hints, label in checks:
        scores[label] = sum(1 for sentence in sentences if team_name in sentence and any(hint in sentence for hint in hints))
    best = max(scores, key=lambda label: scores[label])
    return best if scores[best] > 0 else "balanced"


def detect_formation(sentences: List[str], team_name: str) -> str:
    for sentence in sentences:
        if team_name in sentence:
            for formation in FORMATION_HINTS:
                if formation in sentence:
                    return formation
            if "五后卫" in sentence:
                return "5-4-1"
            if "三中卫" in sentence:
                return "3-5-2"
    return "unknown"


def tactical_feature_summary(sentences: List[str], home: str, away: str) -> Dict[str, object]:
    home_style = detect_style(sentences, home)
    away_style = detect_style(sentences, away)
    home_pressing = min(1.0, 0.25 + 0.18 * count_hits(sentences, PRESSING_HINTS, home))
    away_pressing = min(1.0, 0.25 + 0.18 * count_hits(sentences, PRESSING_HINTS, away))
    home_line = max(0.1, 0.55 - 0.18 * count_hits(sentences, LOW_BLOCK_HINTS, home) + 0.12 * count_hits(sentences, PRESSING_HINTS, home))
    away_line = max(0.1, 0.55 - 0.18 * count_hits(sentences, LOW_BLOCK_HINTS, away) + 0.12 * count_hits(sentences, PRESSING_HINTS, away))
    home_stability = min(1.0, 0.55 + 0.15 * (1 if detect_formation(sentences, home) != "unknown" else 0))
    away_stability = min(1.0, 0.55 + 0.15 * (1 if detect_formation(sentences, away) != "unknown" else 0))
    home_transition = min(1.0, 0.25 + 0.18 * count_hits(sentences, COUNTER_HINTS, away) + 0.12 * max(home_line - 0.55, 0.0))
    away_transition = min(1.0, 0.25 + 0.18 * count_hits(sentences, COUNTER_HINTS, home) + 0.12 * max(away_line - 0.55, 0.0))
    mismatch_home = 0.0
    mismatch_away = 0.0
    if home_style == "pressing" and away_style in {"direct", "balanced"}:
        mismatch_home += 0.08
    if away_style == "counter" and home_line >= 0.6:
        mismatch_away += 0.10
    if home_style == "possession" and away_style == "low_block":
        mismatch_home -= 0.04
        mismatch_away += 0.04
    summary_parts = [f"主队{home_style}", f"客队{away_style}"]
    return {
        "home_coach_style": home_style,
        "away_coach_style": away_style,
        "home_formation": detect_formation(sentences, home),
        "away_formation": detect_formation(sentences, away),
        "home_pressing_level": round(home_pressing, 3),
        "away_pressing_level": round(away_pressing, 3),
        "home_defensive_line": round(min(home_line, 1.0), 3),
        "away_defensive_line": round(min(away_line, 1.0), 3),
        "home_tactical_stability": round(home_stability, 3),
        "away_tactical_stability": round(away_stability, 3),
        "home_transition_risk": round(home_transition, 3),
        "away_transition_risk": round(away_transition, 3),
        "tactical_mismatch_home": round(mismatch_home, 3),
        "tactical_mismatch_away": round(mismatch_away, 3),
        "tactical_summary": "，".join(summary_parts),
        "tactical_source": "keyword_rule",
    }


def build_tactical_prompt(home: str, away: str, text: str, current: Dict[str, object]) -> str:
    return (
        "请仅返回 JSON，不要解释。根据以下赛前情报，提取双方教练战术体系与阵型倾向。"
        "JSON字段必须包含：home_coach_style, away_coach_style, home_formation, away_formation, "
        "home_pressing_level, away_pressing_level, home_defensive_line, away_defensive_line, "
        "home_tactical_stability, away_tactical_stability, home_transition_risk, away_transition_risk, "
        "tactical_mismatch_home, tactical_mismatch_away, tactical_summary。数值范围 0 到 1。\n"
        f"主队：{home}\n客队：{away}\n"
        f"规则提取结果：{json.dumps(current, ensure_ascii=False)}\n"
        f"赛前情报：{text[:2400]}"
    )


def tactical_llm_enhancement(raw_text: str, home: str, away: str, current: Dict[str, object], env: Dict[str, str]) -> Dict[str, object]:
    enabled = env.get("ENABLE_LLM_TACTICAL_ANALYSIS", "1").lower() in {"1", "true", "yes", "on"}
    api_key = env.get("LLM_API_KEY", "") or env.get("ZHIPU_API_KEY", "") or env.get("OPENAI_API_KEY", "")
    if not enabled or not api_key:
        return current
    base_url = env.get("LLM_BASE_URL", DEFAULT_LLM_BASE_URL)
    model = env.get("LLM_MODEL", DEFAULT_LLM_MODEL)
    timeout = int(env.get("TACTICAL_LLM_TIMEOUT", "60") or "60")
    prompt = build_tactical_prompt(home, away, raw_text, current)
    try:
        text = call_llm(api_key, prompt, base_url, model, timeout)
        payload = parse_json_object(text)
    except Exception:
        return {**current, "tactical_source": "keyword_rule_llm_failed"}
    if not payload:
        return {**current, "tactical_source": "keyword_rule_llm_failed"}
    merged = {**current}
    for key in (
        "home_coach_style", "away_coach_style", "home_formation", "away_formation", "tactical_summary",
    ):
        if key in payload and payload[key] is not None:
            merged[key] = payload[key]
    for key in (
        "home_pressing_level", "away_pressing_level", "home_defensive_line", "away_defensive_line",
        "home_tactical_stability", "away_tactical_stability", "home_transition_risk", "away_transition_risk",
        "tactical_mismatch_home", "tactical_mismatch_away",
    ):
        try:
            if key in payload:
                merged[key] = max(0.0, min(1.0, float(payload[key])))
        except (TypeError, ValueError):
            continue
    merged["tactical_source"] = "keyword_rule_and_llm"
    return merged


def extract_money_values(text: str) -> List[float]:
    values: List[float] = []
    for number, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(亿欧|万欧|万欧元|亿欧元|百万欧|百万欧元)", text):
        amount = float(number)
        if "亿" in unit:
            values.append(amount * 100.0)
        elif "万" in unit:
            values.append(amount / 100.0)
        else:
            values.append(amount)
    return values


def value_summary_from_text(sentences: List[str], home: str, away: str, profiles: Dict[str, Dict[str, object]]) -> Dict[str, object]:
    home_profile = dict(profiles.get(home, {}))
    away_profile = dict(profiles.get(away, {}))

    def get_float(team_profile: Dict[str, object], key: str, default: float) -> float:
        try:
            return float(team_profile.get(key, default))
        except (TypeError, ValueError):
            return default

    home_squad_value = get_float(home_profile, "squad_value_eur_m", 0.0)
    away_squad_value = get_float(away_profile, "squad_value_eur_m", 0.0)
    home_top_value = get_float(home_profile, "top_player_value_eur_m", 0.0)
    away_top_value = get_float(away_profile, "top_player_value_eur_m", 0.0)
    home_big5 = get_float(home_profile, "big5_league_players", 0.0)
    away_big5 = get_float(away_profile, "big5_league_players", 0.0)
    home_depth = get_float(home_profile, "squad_depth_score", 0.5)
    away_depth = get_float(away_profile, "squad_depth_score", 0.5)
    home_star_dep = get_float(home_profile, "star_dependency", 0.5)
    away_star_dep = get_float(away_profile, "star_dependency", 0.5)

    text_blob = " ".join(sentences)
    for team_name, role in ((home, "home"), (away, "away")):
        for sentence in sentences:
            if team_name in sentence and any(hint in sentence for hint in VALUE_HINTS):
                values = extract_money_values(sentence)
                if values:
                    if role == "home":
                        home_squad_value = max(home_squad_value, max(values))
                    else:
                        away_squad_value = max(away_squad_value, max(values))
                match = re.search(r"共(\d+)名球员来自五大联赛", sentence)
                if match:
                    if role == "home":
                        home_big5 = max(home_big5, float(match.group(1)))
                    else:
                        away_big5 = max(away_big5, float(match.group(1)))
        ratio_patterns = [
            (rf"{re.escape(home)}身价≥{re.escape(away)}身价的(\d+(?:\.\d+)?)倍", "home"),
            (rf"{re.escape(away)}身价≥{re.escape(home)}身价的(\d+(?:\.\d+)?)倍", "away"),
        ]
        for pattern, role_name in ratio_patterns:
            match = re.search(pattern, text_blob)
            if match:
                ratio = float(match.group(1))
                if role_name == "home" and away_squad_value > 0:
                    home_squad_value = max(home_squad_value, away_squad_value * ratio)
                if role_name == "away" and home_squad_value > 0:
                    away_squad_value = max(away_squad_value, home_squad_value * ratio)

    home_value_ratio = home_squad_value / away_squad_value if away_squad_value > 0 else 1.0
    away_value_ratio = away_squad_value / home_squad_value if home_squad_value > 0 else 1.0
    player_value_mismatch_home = max(min((home_value_ratio - 1.0) / 4.0 + (home_big5 - away_big5) / 25.0, 1.0), -1.0)
    player_value_mismatch_away = max(min((away_value_ratio - 1.0) / 4.0 + (away_big5 - home_big5) / 25.0, 1.0), -1.0)
    summary = "主队价值体系与客队接近"
    if home_value_ratio >= 2.0 or home_big5 - away_big5 >= 5:
        summary = "主队总身价与阵容深度占优"
    elif away_value_ratio >= 2.0 or away_big5 - home_big5 >= 5:
        summary = "客队总身价与阵容深度占优"
    return {
        "home_squad_value_eur_m": round(home_squad_value, 3),
        "away_squad_value_eur_m": round(away_squad_value, 3),
        "home_value_ratio": round(home_value_ratio, 3),
        "away_value_ratio": round(away_value_ratio, 3),
        "home_top_player_value_eur_m": round(home_top_value, 3),
        "away_top_player_value_eur_m": round(away_top_value, 3),
        "home_core_player_count": int(home_profile.get("core_player_count", 0) or 0),
        "away_core_player_count": int(away_profile.get("core_player_count", 0) or 0),
        "home_big5_league_players": int(home_big5),
        "away_big5_league_players": int(away_big5),
        "home_squad_depth_score": round(home_depth, 3),
        "away_squad_depth_score": round(away_depth, 3),
        "home_star_dependency": round(home_star_dep, 3),
        "away_star_dependency": round(away_star_dep, 3),
        "home_absence_value_loss_eur_m": 0.0,
        "away_absence_value_loss_eur_m": 0.0,
        "player_value_mismatch_home": round(player_value_mismatch_home, 3),
        "player_value_mismatch_away": round(player_value_mismatch_away, 3),
        "value_summary": summary,
        "value_source": "keyword_rule" if home_squad_value > 0 or away_squad_value > 0 else "profile_fallback",
    }


def build_value_prompt(home: str, away: str, text: str, current: Dict[str, object]) -> str:
    return (
        "请仅返回 JSON，不要解释。根据赛前情报提取双方球员能力与球队价值体系字段。"
        "字段必须包含：home_squad_value_eur_m, away_squad_value_eur_m, home_value_ratio, away_value_ratio, "
        "home_top_player_value_eur_m, away_top_player_value_eur_m, home_core_player_count, away_core_player_count, "
        "home_big5_league_players, away_big5_league_players, home_squad_depth_score, away_squad_depth_score, "
        "home_star_dependency, away_star_dependency, home_absence_value_loss_eur_m, away_absence_value_loss_eur_m, "
        "player_value_mismatch_home, player_value_mismatch_away, value_summary。"
        f"\n主队：{home}\n客队：{away}\n规则结果：{json.dumps(current, ensure_ascii=False)}\n赛前情报：{text[:2400]}"
    )


def value_llm_enhancement(raw_text: str, home: str, away: str, current: Dict[str, object], env: Dict[str, str]) -> Dict[str, object]:
    enabled = env.get("ENABLE_LLM_PLAYER_VALUE_ANALYSIS", "1").lower() in {"1", "true", "yes", "on"}
    api_key = env.get("LLM_API_KEY", "") or env.get("ZHIPU_API_KEY", "") or env.get("OPENAI_API_KEY", "")
    if not enabled or not api_key:
        return current
    base_url = env.get("LLM_BASE_URL", DEFAULT_LLM_BASE_URL)
    model = env.get("LLM_MODEL", DEFAULT_LLM_MODEL)
    timeout = int(env.get("PLAYER_VALUE_LLM_TIMEOUT", "60") or "60")
    prompt = build_value_prompt(home, away, raw_text, current)
    try:
        text = call_llm(api_key, prompt, base_url, model, timeout)
        payload = parse_json_object(text)
    except Exception:
        return {**current, "value_source": "keyword_rule_llm_failed"}
    if not payload:
        return {**current, "value_source": "keyword_rule_llm_failed"}
    merged = {**current}
    for key in (
        "value_summary",
    ):
        if key in payload and payload[key] is not None:
            merged[key] = payload[key]
    numeric_keys = (
        "home_squad_value_eur_m", "away_squad_value_eur_m", "home_value_ratio", "away_value_ratio",
        "home_top_player_value_eur_m", "away_top_player_value_eur_m", "home_core_player_count", "away_core_player_count",
        "home_big5_league_players", "away_big5_league_players", "home_squad_depth_score", "away_squad_depth_score",
        "home_star_dependency", "away_star_dependency", "home_absence_value_loss_eur_m", "away_absence_value_loss_eur_m",
        "player_value_mismatch_home", "player_value_mismatch_away",
    )
    for key in numeric_keys:
        if key in payload:
            try:
                merged[key] = float(payload[key])
            except (TypeError, ValueError):
                continue
    merged["value_source"] = "keyword_rule_and_llm"
    return merged


def weather_summary_from_text(sentences: List[str]) -> Dict[str, object]:
    weather_sentences = [sentence for sentence in sentences if any(hint in sentence for hint in WEATHER_HINTS)]
    text_blob = " ".join(weather_sentences)

    def first_number(patterns: List[str]) -> float:
        for pattern in patterns:
            match = re.search(pattern, text_blob)
            if match:
                try:
                    return float(match.group(1))
                except (TypeError, ValueError):
                    continue
        return 0.0

    temperature_c = first_number([r"(?:气温|温度|高温)[约为可达]*(\d+(?:\.\d+)?)\s*(?:℃|度)"])
    humidity_pct = first_number([r"湿度[约为可达]*(\d+(?:\.\d+)?)\s*%"])
    wind_kph = first_number([r"风速[约为可达]*(\d+(?:\.\d+)?)\s*(?:公里|km/h|kph)"])
    precipitation_mm = first_number([r"降雨量[约为可达]*(\d+(?:\.\d+)?)\s*毫米", r"降水量[约为可达]*(\d+(?:\.\d+)?)\s*毫米"])
    if not precipitation_mm and any(token in text_blob for token in ("降雨", "下雨", "雨战", "暴雨")):
        precipitation_mm = 2.0
    if not wind_kph and "强风" in text_blob:
        wind_kph = 25.0
    if not temperature_c and "高温" in text_blob:
        temperature_c = 30.0

    severity = 0.0
    if temperature_c >= 35.0 or wind_kph >= 35.0 or precipitation_mm >= 8.0:
        severity = 1.0
    elif temperature_c >= 30.0 or humidity_pct >= 75.0 or wind_kph >= 25.0 or precipitation_mm >= 2.0:
        severity = 0.7
    summary = ""
    if weather_sentences:
        summary = "；".join(weather_sentences[:2])[:180]
    elif severity > 0:
        summary = "比赛天气存在不利因素"
    return {
        "temperature_c": round(temperature_c, 1),
        "humidity_pct": round(humidity_pct, 1),
        "wind_kph": round(wind_kph, 1),
        "precipitation_mm": round(precipitation_mm, 1),
        "weather_severity": round(severity, 2),
        "weather_summary": summary,
        "weather_source": "keyword_rule" if weather_sentences or severity > 0 else "none",
    }


def analyze_qingbao(text: str, home: str, away: str) -> Dict[str, object]:
    raw_text = html_to_text(text)
    sentences = [normalize(part) for part in re.split(r"[。！？\n]", raw_text) if normalize(part)]

    home_injury = count_hits(sentences, INJURY_HINTS, home)
    away_injury = count_hits(sentences, INJURY_HINTS, away)
    home_suspend = count_hits(sentences, SUSPENSION_HINTS, home)
    away_suspend = count_hits(sentences, SUSPENSION_HINTS, away)

    home_lineup_known = 1 if any(home in s and any(h in s for h in LINEUP_HINTS) for s in sentences) else 0
    away_lineup_known = 1 if any(away in s and any(h in s for h in LINEUP_HINTS) for s in sentences) else 0
    home_rotation_flag = 1 if any(home in s and any(h in s for h in ROTATION_HINTS) for s in sentences) else 0
    away_rotation_flag = 1 if any(away in s and any(h in s for h in ROTATION_HINTS) for s in sentences) else 0
    must_win_flag_home = 1 if any(home in s and any(h in s for h in MUST_WIN_HINTS) for s in sentences) else 0
    must_win_flag_away = 1 if any(away in s and any(h in s for h in MUST_WIN_HINTS) for s in sentences) else 0

    supporting = [s for s in sentences if any(h in s for h in INJURY_HINTS + SUSPENSION_HINTS + LINEUP_HINTS + ROTATION_HINTS + MUST_WIN_HINTS)][:12]
    tactical = tactical_feature_summary(sentences, home, away)
    value_summary = value_summary_from_text(sentences, home, away, load_team_value_profiles())
    weather_summary = weather_summary_from_text(sentences)
    return {
        "home_injury_count": home_injury,
        "away_injury_count": away_injury,
        "home_suspension_count": home_suspend,
        "away_suspension_count": away_suspend,
        "home_lineup_known": home_lineup_known,
        "away_lineup_known": away_lineup_known,
        "home_rotation_flag": home_rotation_flag,
        "away_rotation_flag": away_rotation_flag,
        "must_win_flag_home": must_win_flag_home,
        "must_win_flag_away": must_win_flag_away,
        "supporting_sentences": supporting,
        **tactical,
        **value_summary,
        **weather_summary,
    }


def main() -> int:
    env = {**os.environ, **load_env(ROOT / ".env")}
    qingbao_base_url = env.get("QTX_QINGBAO_BASE_URL", DEFAULT_QTX_QINGBAO_BASE_URL)
    profiles = load_team_value_profiles()
    weather_index = load_match_weather_index()
    snapshot = json.loads(latest_snapshot().read_text(encoding="utf-8"))
    matches = snapshot.get("matches", [])
    results: List[Dict[str, object]] = []

    # First prototype: structure + parser are ready; match token backfill will be added once qtx ids are stored in snapshot.
    for item in matches:
        home = str(item.get("home", ""))
        away = str(item.get("away", ""))
        match_time = str(item.get("match_time", ""))
        token = extract_match_token(item)
        value_defaults = value_summary_from_text([], home, away, profiles)
        entry: Dict[str, object] = {
            "match_time": match_time,
            "home_team": home,
            "away_team": away,
            "home_injury_count": 0,
            "away_injury_count": 0,
            "home_suspension_count": 0,
            "away_suspension_count": 0,
            "home_lineup_known": 0,
            "away_lineup_known": 0,
            "home_rotation_flag": 0,
            "away_rotation_flag": 0,
            "must_win_flag_home": 0,
            "must_win_flag_away": 0,
            "home_coach_style": "unknown",
            "away_coach_style": "unknown",
            "home_formation": "unknown",
            "away_formation": "unknown",
            "home_pressing_level": 0.0,
            "away_pressing_level": 0.0,
            "home_defensive_line": 0.5,
            "away_defensive_line": 0.5,
            "home_tactical_stability": 0.5,
            "away_tactical_stability": 0.5,
            "home_transition_risk": 0.3,
            "away_transition_risk": 0.3,
            "tactical_mismatch_home": 0.0,
            "tactical_mismatch_away": 0.0,
            "tactical_summary": "",
            "tactical_source": "none",
            "temperature_c": 0.0,
            "humidity_pct": 0.0,
            "wind_kph": 0.0,
            "precipitation_mm": 0.0,
            "weather_severity": 0.0,
            "weather_summary": "",
            "weather_source": "none",
            **value_defaults,
            "source": "qtx_qingbao_prototype",
            "qtx_match_token": token,
            "supporting_sentences": [],
        }
        if token:
            try:
                page = fetch(qingbao_url(token, qingbao_base_url))
                parsed = analyze_qingbao(page, home, away)
                parsed = tactical_llm_enhancement(html_to_text(page), home, away, parsed, env)
                parsed = value_llm_enhancement(html_to_text(page), home, away, parsed, env)
                entry.update(parsed)
            except Exception as exc:
                entry["fetch_error"] = type(exc).__name__
        else:
            entry["fetch_error"] = "missing_qtx_match_token"
        merge_weather(entry, weather_index.get((match_time, home, away), {}))
        results.append(entry)

    OUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Prematch news written: {OUT_PATH}")
    print(f"Matches exported: {len(results)}")
    print("Note: current prototype requires qtx_match_token in prediction snapshots for live fetch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
