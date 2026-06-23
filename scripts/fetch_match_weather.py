#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
RUN_DOCS_DIR = ROOT / "docs" / "run"
VENUE_CONFIG_PATH = ROOT / "config" / "worldcup_venues_2026.json"
OUT_PATH = ROOT / "data" / "raw" / "match_weather_forecast.json"
DEFAULT_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
DEFAULT_OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
DEFAULT_OPEN_METEO_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
DEFAULT_QTX_QINGBAO_BASE_URL = "https://live.qtx.com/qingbao"
CITY_ALIASES = {
    "加利福尼亚州圣克拉拉": "Santa Clara",
    "圣克拉拉": "Santa Clara",
    "旧金山": "San Francisco",
    "达拉斯": "Dallas",
    "亚特兰大": "Atlanta",
    "西雅图": "Seattle",
    "休斯敦": "Houston",
    "迈阿密": "Miami",
    "波士顿": "Boston",
    "蒙特雷": "Monterrey",
    "瓜达拉哈拉": "Guadalajara",
    "墨西哥城": "Mexico City",
    "温哥华": "Vancouver",
    "多伦多": "Toronto",
    "洛杉矶": "Los Angeles",
    "堪萨斯城": "Kansas City",
    "费城": "Philadelphia",
    "新泽西": "East Rutherford",
}
VENUE_CITY_ALIASES = {
    "休斯敦体育场": "Houston",
    "瓜达拉哈拉体育场": "Guadalajara",
    "旧金山湾区体育场": "Santa Clara",
}


def latest_snapshot() -> Path:
    files = sorted(RUN_DOCS_DIR.glob("worldcup-2026-agent-predictions_*.json"))
    if not files:
        raise FileNotFoundError(f"No prediction snapshot found in {RUN_DOCS_DIR}")
    return files[-1]


def load_venue_config(path: Path = VENUE_CONFIG_PATH) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    rows = payload.get("matches") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {}
    index: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = (str(row.get("match_time", "")), str(row.get("home_team", "")), str(row.get("away_team", "")))
        if not all(key):
            continue
        index[key] = row
    return index


def fetch_json(url: str) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "WorldCupAgent/1.0"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "WorldCupAgent/1.0"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def qingbao_url(token: str, base_url: str = DEFAULT_QTX_QINGBAO_BASE_URL) -> str:
    return f"{base_url.rstrip('/')}/{token}.html"


def html_to_text(html: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def parse_venue_from_text(text: str) -> Dict[str, str]:
    patterns = [
        r"本场比赛在([^，。]+?)的([^，。]+?(?:体育场|球场))举行",
        r"比赛在([^，。]+?)的([^，。]+?(?:体育场|球场))举行",
        r"本场(?:世界杯)?[^，。]*?在([^，。]+?(?:体育场|球场))举行",
        r"本场比赛将在([^，。]+?(?:体育场|球场))进行",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            if match.lastindex and match.lastindex >= 2:
                return {"venue_city": match.group(1).strip(), "venue_name": match.group(2).strip()}
            venue_name = match.group(1).strip()
            return {"venue_city": VENUE_CITY_ALIASES.get(venue_name, ""), "venue_name": venue_name}
    return {}


def normalize_city_query(city: str) -> str:
    text = city.strip()
    if text in CITY_ALIASES:
        return CITY_ALIASES[text]
    if "州" in text:
        text = text.split("州")[-1]
    return CITY_ALIASES.get(text, CITY_ALIASES.get(city.strip(), text or city.strip()))


def geocode_city(city: str, base_url: str = DEFAULT_OPEN_METEO_GEOCODE_URL) -> Optional[Tuple[float, float, str]]:
    query_city = normalize_city_query(city)
    if not query_city:
        return None
    query = urllib.parse.urlencode({"name": query_city, "count": 1, "language": "en", "format": "json"})
    payload = fetch_json(f"{base_url}?{query}")
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list) or not results:
        return None
    first = results[0]
    try:
        lat = float(first.get("latitude"))
        lon = float(first.get("longitude"))
    except (TypeError, ValueError):
        return None
    timezone = str(first.get("timezone", "auto")) or "auto"
    return lat, lon, timezone


def resolve_venue_for_match(match: Dict[str, Any], configured: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if configured:
        return configured
    token = str(match.get("qtx_match_token", ""))
    if not token:
        return None
    try:
        text = html_to_text(fetch_text(qingbao_url(token)))
        venue_bits = parse_venue_from_text(text)
    except Exception:
        return None
    if not venue_bits:
        return None
    geocoded = geocode_city(venue_bits.get("venue_city", ""))
    if geocoded is None:
        return {**venue_bits}
    lat, lon, timezone = geocoded
    return {**venue_bits, "latitude": lat, "longitude": lon, "timezone": timezone, "source": "qtx_qingbao_text"}


def nearest_hourly_record(hourly: Dict[str, Any], kickoff: dt.datetime) -> Optional[Tuple[int, str]]:
    times = hourly.get("time")
    if not isinstance(times, list) or not times:
        return None
    best_idx: Optional[int] = None
    best_delta: Optional[float] = None
    best_time = ""
    for idx, value in enumerate(times):
        try:
            hourly_dt = dt.datetime.fromisoformat(str(value))
        except ValueError:
            continue
        delta = abs((hourly_dt - kickoff).total_seconds())
        if best_delta is None or delta < best_delta:
            best_idx = idx
            best_delta = delta
            best_time = str(value)
    if best_idx is None:
        return None
    return best_idx, best_time


def weather_api_mode(kickoff: dt.datetime, now: Optional[dt.datetime] = None) -> Tuple[str, str]:
    current = now or dt.datetime.now()
    if kickoff.date() < current.date():
        return "archive", DEFAULT_OPEN_METEO_ARCHIVE_URL
    return "forecast", DEFAULT_OPEN_METEO_URL


def build_unavailable_weather_entry(match: Dict[str, Any], venue: Dict[str, Any], status: str) -> Dict[str, Any]:
    return {
        "match_time": str(match.get("match_time", "")),
        "home_team": str(match.get("home", "")),
        "away_team": str(match.get("away", "")),
        "venue_name": str(venue.get("venue_name", "")),
        "venue_city": str(venue.get("venue_city", "")),
        "venue_timezone": str(venue.get("timezone", "auto")) or "auto",
        "venue_indoor": bool(venue.get("indoor", False)),
        "temperature_c": 0.0,
        "humidity_pct": 0.0,
        "wind_kph": 0.0,
        "precipitation_mm": 0.0,
        "weather_severity": 0.0,
        "weather_summary": f"已识别比赛场地：{venue.get('venue_city', '')} {venue.get('venue_name', '')}；天气暂不可用：{status}",
        "weather_source": "open_meteo_unavailable",
        "weather_status": status,
    }


def build_weather_entry(match: Dict[str, Any], venue: Dict[str, Any], base_url: Optional[str] = None, now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    match_time = str(match.get("match_time", ""))
    kickoff = dt.datetime.strptime(match_time, "%Y-%m-%d %H:%M")
    lat = float(venue.get("latitude"))
    lon = float(venue.get("longitude"))
    timezone = str(venue.get("timezone", "auto")) or "auto"
    mode, default_url = weather_api_mode(kickoff, now=now)
    api_url = base_url or default_url
    query = urllib.parse.urlencode(
        {
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m",
            "timezone": timezone,
            "start_date": kickoff.strftime("%Y-%m-%d"),
            "end_date": kickoff.strftime("%Y-%m-%d"),
            "forecast_days": 1,
        }
    )
    try:
        payload = fetch_json(f"{api_url}?{query}")
    except urllib.error.HTTPError as exc:
        if exc.code == 400:
            return build_unavailable_weather_entry(match, venue, "forecast_not_available_yet" if mode == "forecast" else "archive_not_available")
        raise
    hourly = payload.get("hourly", {}) if isinstance(payload, dict) else {}
    nearest = nearest_hourly_record(hourly, kickoff)
    if nearest is None:
        raise ValueError("missing_hourly_weather")
    idx, weather_time = nearest
    def pick(field: str) -> float:
        values = hourly.get(field)
        if not isinstance(values, list) or len(values) <= idx:
            return 0.0
        try:
            return float(values[idx] or 0.0)
        except (TypeError, ValueError):
            return 0.0

    temperature_c = pick("temperature_2m")
    humidity_pct = pick("relative_humidity_2m")
    precipitation_mm = pick("precipitation")
    wind_kph = pick("wind_speed_10m")
    weather_severity = 0.0
    if temperature_c >= 35.0 or wind_kph >= 35.0 or precipitation_mm >= 8.0:
        weather_severity = 1.0
    elif temperature_c >= 30.0 or humidity_pct >= 75.0 or wind_kph >= 25.0 or precipitation_mm >= 2.0:
        weather_severity = 0.7
    summary_parts = [f"{venue.get('venue_city', '')} {weather_time}"]
    if temperature_c:
        summary_parts.append(f"气温 {temperature_c:.1f}C")
    if humidity_pct:
        summary_parts.append(f"湿度 {humidity_pct:.0f}%")
    if precipitation_mm:
        summary_parts.append(f"降水 {precipitation_mm:.1f}mm")
    if wind_kph:
        summary_parts.append(f"风速 {wind_kph:.1f}km/h")
    return {
        "match_time": match_time,
        "home_team": str(match.get("home", "")),
        "away_team": str(match.get("away", "")),
        "venue_name": str(venue.get("venue_name", "")),
        "venue_city": str(venue.get("venue_city", "")),
        "venue_timezone": timezone,
        "venue_indoor": bool(venue.get("indoor", False)),
        "temperature_c": round(temperature_c, 1),
        "humidity_pct": round(humidity_pct, 1),
        "wind_kph": round(wind_kph, 1),
        "precipitation_mm": round(precipitation_mm, 1),
        "weather_severity": round(weather_severity, 2),
        "weather_summary": "；".join(part for part in summary_parts if part),
        "weather_source": f"open_meteo_{mode}",
        "weather_status": mode,
    }


def main() -> int:
    snapshot = json.loads(latest_snapshot().read_text(encoding="utf-8"))
    matches = snapshot.get("matches", []) if isinstance(snapshot, dict) else []
    venue_index = load_venue_config()
    results: List[Dict[str, Any]] = []
    hit = 0
    for match in matches:
        if not isinstance(match, dict):
            continue
        key = (str(match.get("match_time", "")), str(match.get("home", "")), str(match.get("away", "")))
        venue = resolve_venue_for_match(match, venue_index.get(key))
        if not venue:
            results.append({
                "match_time": key[0],
                "home_team": key[1],
                "away_team": key[2],
                "weather_source": "missing_venue_mapping",
                "weather_status": "missing_venue",
                "fetch_error": "missing_venue_mapping",
            })
            continue
        try:
            results.append(build_weather_entry(match, venue))
            hit += 1
        except Exception as exc:
            error_text = str(exc) or type(exc).__name__
            results.append({
                "match_time": key[0],
                "home_team": key[1],
                "away_team": key[2],
                "venue_name": str(venue.get("venue_name", "")),
                "venue_city": str(venue.get("venue_city", "")),
                "weather_source": "open_meteo_forecast",
                "fetch_error": error_text,
                "weather_summary": f"已识别比赛场地：{venue.get('venue_city', '')} {venue.get('venue_name', '')}；天气预报暂不可用：{error_text}",
            })
    OUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Match weather written: {OUT_PATH}")
    print(f"Venue mappings matched: {hit}")
    print(f"Matches scanned: {len(matches)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
