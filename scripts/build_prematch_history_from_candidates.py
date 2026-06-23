#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple


ROOT = Path(__file__).resolve().parents[1]
RUN_DOCS_DIR = ROOT / "docs" / "run"
OUTPUT_PATH = ROOT / "data" / "raw" / "prematch_team_news_history.json"

FIELDS = [
    "match_time",
    "home_team",
    "away_team",
    "qtx_match_token",
    "home_injury_count",
    "away_injury_count",
    "home_suspension_count",
    "away_suspension_count",
    "home_lineup_known",
    "away_lineup_known",
    "home_rotation_flag",
    "away_rotation_flag",
    "must_win_flag_home",
    "must_win_flag_away",
    "home_pressing_level",
    "away_pressing_level",
    "home_defensive_line",
    "away_defensive_line",
    "home_tactical_stability",
    "away_tactical_stability",
    "home_transition_risk",
    "away_transition_risk",
    "tactical_mismatch_home",
    "tactical_mismatch_away",
    "tactical_summary",
    "tactical_source",
    "home_squad_value_eur_m",
    "away_squad_value_eur_m",
    "home_value_ratio",
    "away_value_ratio",
    "home_top_player_value_eur_m",
    "away_top_player_value_eur_m",
    "home_core_player_count",
    "away_core_player_count",
    "home_big5_league_players",
    "away_big5_league_players",
    "home_squad_depth_score",
    "away_squad_depth_score",
    "home_star_dependency",
    "away_star_dependency",
    "home_absence_value_loss_eur_m",
    "away_absence_value_loss_eur_m",
    "player_value_mismatch_home",
    "player_value_mismatch_away",
    "value_summary",
    "value_source",
    "temperature_c",
    "humidity_pct",
    "wind_kph",
    "precipitation_mm",
    "weather_severity",
    "weather_summary",
    "weather_source",
]


def candidate_files() -> List[Path]:
    return sorted(RUN_DOCS_DIR.glob("worldcup-2026-agent-training-candidates_*.json"))


def completeness_score(row: Dict[str, object]) -> int:
    score = 0
    for field in FIELDS[4:]:
        value = row.get(field)
        if isinstance(value, str) and value.strip():
            score += 1
        elif isinstance(value, (int, float)) and value != 0:
            score += 1
    return score


def first_nonempty_text(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def enrich_from_ai_adjustment(item: Dict[str, object], row: Dict[str, object]) -> Dict[str, object]:
    adjustment = row.get("ai_adjustment")
    if not isinstance(adjustment, dict):
        return item
    tactical_reasons = adjustment.get("tactical_reasons") if isinstance(adjustment.get("tactical_reasons"), list) else []
    value_reasons = adjustment.get("value_reasons") if isinstance(adjustment.get("value_reasons"), list) else []
    weather_reasons = adjustment.get("weather_reasons") if isinstance(adjustment.get("weather_reasons"), list) else []
    if tactical_reasons and not str(item.get("tactical_summary", "")).strip():
        item["tactical_summary"] = "；".join(str(reason) for reason in tactical_reasons[:3])
        item["tactical_source"] = first_nonempty_text(item.get("tactical_source"), "training_candidate_ai_adjustment")
    if value_reasons and not str(item.get("value_summary", "")).strip():
        item["value_summary"] = "；".join(str(reason) for reason in value_reasons[:3])
        item["value_source"] = first_nonempty_text(item.get("value_source"), "training_candidate_ai_adjustment")
    if weather_reasons and not str(item.get("weather_summary", "")).strip():
        item["weather_summary"] = "；".join(str(reason) for reason in weather_reasons[:3])
        item["weather_source"] = first_nonempty_text(item.get("weather_source"), "training_candidate_ai_adjustment")
    if adjustment.get("applied_tactical") and not str(item.get("tactical_source", "")).strip():
        item["tactical_source"] = "training_candidate_ai_adjustment"
    if adjustment.get("applied_value") and not str(item.get("value_source", "")).strip():
        item["value_source"] = "training_candidate_ai_adjustment"
    if adjustment.get("applied_weather") and not str(item.get("weather_source", "")).strip():
        item["weather_source"] = "training_candidate_ai_adjustment"
    return item


def main() -> int:
    best: Dict[Tuple[str, str, str], Dict[str, object]] = {}
    for path in candidate_files():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows = payload.get("rows")
        if not isinstance(rows, list):
            continue
        generated_at = str(payload.get("generated_at", ""))
        for row in rows:
            if not isinstance(row, dict):
                continue
            item = {
                "match_time": str(row.get("match_time", "")),
                "home_team": str(row.get("home_team", "")),
                "away_team": str(row.get("away_team", "")),
                **{field: row.get(field, "") for field in FIELDS[3:]},
                "source": "training_candidate_history",
                "source_snapshot": path.name,
                "source_generated_at": generated_at,
            }
            item = enrich_from_ai_adjustment(item, row)
            key = (item["match_time"], item["home_team"], item["away_team"])
            if not all(key):
                continue
            current = best.get(key)
            current_score = completeness_score(current) if isinstance(current, dict) else -1
            item_score = completeness_score(item)
            if current is None or item_score > current_score or (item_score == current_score and generated_at > str(current.get("source_generated_at", ""))):
                best[key] = item

    OUTPUT_PATH.write_text(json.dumps(list(best.values()), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"History rows: {len(best)}")
    print(f"Output: {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
