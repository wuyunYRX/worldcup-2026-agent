#!/usr/bin/env python3
"""Fetch latest finished 2026 World Cup results into local raw data."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict


ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "data" / "raw" / "wc2026_football_data_matches.json"
DEFAULT_MATCHES_URL = "https://api.football-data.org/v4/competitions/2000/matches?season=2026&status=FINISHED"


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


def fetch_json(url: str, token: str, timeout: int = 30) -> Dict[str, object]:
    req = urllib.request.Request(url, headers={"X-Auth-Token": token, "User-Agent": "WorldCupAgent/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    env = {**os.environ, **load_env(ROOT / ".env")}
    token = env.get("FOOTBALL_DATA_API_TOKEN", "").strip()
    url = env.get("FOOTBALL_DATA_MATCHES_URL", DEFAULT_MATCHES_URL).strip() or DEFAULT_MATCHES_URL
    if not token:
        print("Latest results fetch skipped: FOOTBALL_DATA_API_TOKEN is not configured.")
        return 0

    try:
        payload = fetch_json(url, token)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:300]
        print(f"Latest results fetch failed: HTTP {exc.code} {exc.reason}: {detail}")
        return 0
    except Exception as exc:
        print(f"Latest results fetch failed: {type(exc).__name__}: {exc}")
        return 0

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    result_set = payload.get("resultSet", {}) if isinstance(payload, dict) else {}
    print(f"Latest results written: {OUT_PATH}")
    print(f"Finished matches: {result_set.get('count', 'unknown')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
