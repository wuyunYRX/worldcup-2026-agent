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
    }


def main() -> int:
    env = {**os.environ, **load_env(ROOT / ".env")}
    qingbao_base_url = env.get("QTX_QINGBAO_BASE_URL", DEFAULT_QTX_QINGBAO_BASE_URL)
    snapshot = json.loads(latest_snapshot().read_text(encoding="utf-8"))
    matches = snapshot.get("matches", [])
    results: List[Dict[str, object]] = []

    # First prototype: structure + parser are ready; match token backfill will be added once qtx ids are stored in snapshot.
    for item in matches:
        home = str(item.get("home", ""))
        away = str(item.get("away", ""))
        match_time = str(item.get("match_time", ""))
        token = extract_match_token(item)
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
            "source": "qtx_qingbao_prototype",
            "qtx_match_token": token,
            "supporting_sentences": [],
        }
        if token:
            try:
                page = fetch(qingbao_url(token, qingbao_base_url))
                parsed = analyze_qingbao(page, home, away)
                parsed = tactical_llm_enhancement(html_to_text(page), home, away, parsed, env)
                entry.update(parsed)
            except Exception as exc:
                entry["fetch_error"] = type(exc).__name__
        else:
            entry["fetch_error"] = "missing_qtx_match_token"
        results.append(entry)

    OUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Prematch news written: {OUT_PATH}")
    print(f"Matches exported: {len(results)}")
    print("Note: current prototype requires qtx_match_token in prediction snapshots for live fetch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
