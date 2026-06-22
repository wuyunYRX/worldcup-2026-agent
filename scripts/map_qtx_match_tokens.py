#!/usr/bin/env python3
"""Map latest run snapshot matches to QTX match tokens from QTX 2026 World Cup portal."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Tuple


ROOT = Path(__file__).resolve().parents[1]
RUN_DOCS_DIR = ROOT / "docs" / "run"
DEFAULT_QTX_WORLD_CUP_URL = "https://www.qtx.com/worldcup/"


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


def parse_qtx_schedule(qtx_url: str = DEFAULT_QTX_WORLD_CUP_URL) -> Dict[str, List[Tuple[str, str, str]]]:
    mapping: Dict[str, List[Tuple[str, str, str]]] = {}
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 2400})
        page.goto(qtx_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
        anchors = page.locator("a").all()
        for anchor in anchors:
            try:
                href = anchor.get_attribute("href") or ""
                match = re.search(r"/fenxi/([A-Za-z0-9]+)\.html", href)
                if not match:
                    continue
                text = anchor.inner_text().strip()
                parts = [re.sub(r"\s+", " ", item.strip()) for item in text.splitlines() if item.strip()]
                if len(parts) < 5:
                    continue
                if not re.fullmatch(r"\d{2}-\d{2} \d{2}:\d{2}", parts[1]):
                    continue
                match_time = parts[1]
                home = parts[2]
                away = parts[4]
                token = match.group(1)
                mapping.setdefault(match_time, []).append((home, away, token))
            except Exception:
                continue
        browser.close()
    return mapping


def main() -> int:
    env = {**os.environ, **load_env(ROOT / ".env")}
    qtx_url = env.get("QTX_WORLD_CUP_URL", DEFAULT_QTX_WORLD_CUP_URL)
    snapshot_path = latest_snapshot()
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    mapping = parse_qtx_schedule(qtx_url)

    updated = 0
    for match in snapshot.get("matches", []):
        full_time = str(match.get("match_time", ""))
        if len(full_time) >= 16 and " " in full_time:
            date_part, time_part = full_time.split(" ", 1)
            time_key = f"{date_part[5:]} {time_part[:5]}"
        else:
            time_key = full_time
        token = ""
        candidates = mapping.get(time_key, [])
        if len(candidates) == 1:
            token = candidates[0][2]
        if token:
            match["qtx_match_token"] = token
            updated += 1

    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Snapshot updated: {snapshot_path}")
    print(f"Mapped qtx tokens: {updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
