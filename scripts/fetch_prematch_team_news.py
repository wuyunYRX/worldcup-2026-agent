#!/usr/bin/env python3
"""Prototype fetcher for pre-match team news from QTX qingbao pages."""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import Dict, List
from html import unescape


ROOT = Path(__file__).resolve().parents[1]
RUN_DOCS_DIR = ROOT / "docs" / "run"
OUT_PATH = ROOT / "data" / "raw" / "prematch_team_news.json"

INJURY_HINTS = ("伤停", "伤病", "缺阵", "伤缺", "受伤", "复出")
SUSPENSION_HINTS = ("停赛", "禁赛", "红牌", "黄牌停赛")
LINEUP_HINTS = ("预计首发", "首发", "阵容")
ROTATION_HINTS = ("轮换", "替补", "保留主力")
MUST_WIN_HINTS = ("必须取胜", "必须赢", "背水一战", "出线", "晋级")


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


def qingbao_url(match_id: str) -> str:
    return f"https://live.qtx.com/qingbao/{match_id}.html"


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
    }


def main() -> int:
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
            "source": "qtx_qingbao_prototype",
            "qtx_match_token": token,
            "supporting_sentences": [],
        }
        if token:
            try:
                page = fetch(qingbao_url(token))
                entry.update(analyze_qingbao(page, home, away))
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
