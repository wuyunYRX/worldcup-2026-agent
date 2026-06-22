#!/usr/bin/env python3
"""Backfill prematch features (injury/suspension/lineup/rotation/must_win) for historical matches.

Strategy:
  1. Rule engine: deterministic features (rotation, must_win, lineup_known, rest_days)
  2. LLM: injury/suspension counts via OpenAI-compatible API

Output: data/raw/prematch_training_samples.json (compatible with build_training_data.py)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRAIN_CSV = ROOT / "data" / "processed" / "train_dataset.csv"
DEFAULT_VALIDATION_CSV = ROOT / "data" / "processed" / "validation_dataset.csv"
DEFAULT_TEST_CSV = ROOT / "data" / "processed" / "test_dataset.csv"
DEFAULT_OUTPUT = ROOT / "data" / "raw" / "prematch_training_samples.json"

DEFAULT_LLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_LLM_MODEL = "glm-4-flash"


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


def parse_round_number(round_text: str) -> int:
    m = re.search(r"第(\d+)轮", round_text)
    return int(m.group(1)) if m else 0


def is_knockout(stage: str) -> bool:
    return any(token in stage for token in ("决赛", "淘汰", "1/", "半", "四分"))


def rule_based_features(row: Dict[str, str]) -> Dict[str, Any]:
    stage = row.get("stage", "")
    round_text = row.get("round_text", "")
    group_name = row.get("group_name", "")
    round_num = parse_round_number(round_text)

    home_rotation = 0
    away_rotation = 0
    must_win_home = 0
    must_win_away = 0
    home_lineup_known = 0
    away_lineup_known = 0

    if is_knockout(stage):
        must_win_home = 1
        must_win_away = 1
        home_lineup_known = 1
        away_lineup_known = 1
    elif "小组" in stage or group_name:
        if round_num == 1:
            home_lineup_known = 0
            away_lineup_known = 0
        elif round_num == 2:
            home_lineup_known = 1
            away_lineup_known = 1
        elif round_num >= 3:
            home_lineup_known = 1
            away_lineup_known = 1
            must_win_home = 1
            must_win_away = 1
            home_rotation = 1
            away_rotation = 1
        else:
            home_lineup_known = 0
            away_lineup_known = 0

    rest_home = row.get("home_rest_days", "0").strip()
    rest_away = row.get("away_rest_days", "0").strip()

    return {
        "home_rotation_flag": home_rotation,
        "away_rotation_flag": away_rotation,
        "must_win_flag_home": must_win_home,
        "must_win_flag_away": must_win_away,
        "home_lineup_known": home_lineup_known,
        "away_lineup_known": away_lineup_known,
        "home_rest_days": int(rest_home) if rest_home.isdigit() else 0,
        "away_rest_days": int(rest_away) if rest_away.isdigit() else 0,
        "home_injury_count": 0,
        "away_injury_count": 0,
        "home_suspension_count": 0,
        "away_suspension_count": 0,
    }


def call_llm(
    api_key: str,
    prompt: str,
    base_url: str = DEFAULT_LLM_BASE_URL,
    model: str = DEFAULT_LLM_MODEL,
    timeout: int = 30,
    retries: int = 1,
) -> Optional[str]:
    attempts = max(retries, 0) + 1
    for attempt in range(attempts):
        response = call_llm_responses(api_key, prompt, base_url=base_url, model=model, timeout=timeout)
        if response:
            return response
        response = call_llm_chat(api_key, prompt, base_url=base_url, model=model, timeout=timeout)
        if response:
            return response
        if attempt < attempts - 1:
            time.sleep(1.0 + attempt)
    return None


def post_json(api_key: str, url: str, payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "OpenAI/JS 6.0.0",
            "X-Stainless-Lang": "js",
            "X-Stainless-Package-Version": "6.0.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="ignore")
    if not raw.lstrip().startswith("{"):
        raise ValueError(f"non-json response: {raw[:120]}")
    return json.loads(raw)


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


def call_llm_responses(
    api_key: str,
    prompt: str,
    base_url: str = DEFAULT_LLM_BASE_URL,
    model: str = DEFAULT_LLM_MODEL,
    timeout: int = 30,
) -> Optional[str]:
    payload = {
        "model": model,
        "input": prompt,
        "store": False,
        "max_output_tokens": 300,
    }
    try:
        body = post_json(api_key, f"{base_url.rstrip('/')}/responses", payload, timeout)
        return extract_responses_text(body)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:200]
        print(f"  LLM responses API error: HTTP {exc.code} {exc.reason}: {detail}")
    except Exception as exc:
        print(f"  LLM responses API error: {exc}")
    return None


def call_llm_chat(
    api_key: str,
    prompt: str,
    base_url: str = DEFAULT_LLM_BASE_URL,
    model: str = DEFAULT_LLM_MODEL,
    timeout: int = 30,
) -> Optional[str]:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 300,
    }
    try:
        body = post_json(api_key, f"{base_url.rstrip('/')}/chat/completions", payload, timeout)
        choices = body.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:200]
        print(f"  LLM chat API error: HTTP {exc.code} {exc.reason}: {detail}")
    except Exception as exc:
        print(f"  LLM chat API error: {exc}")
    return None


def classify_llm_failure(base_url: str, response_hint: str) -> str:
    hint = response_hint.lower()
    if "error code: 1010" in hint:
        return "gateway_forbidden_1010"
    if "<!doctype html" in hint or "<html" in hint:
        return "frontend_html_response"
    if "no available" in hint or "unsupported" in hint or "model" in hint:
        return "model_or_account_pool_unavailable"
    if "/api/" in base_url and "404" in hint:
        return "wrong_api_path"
    return "unknown"


def llm_smoke_test(api_key: str, base_url: str, model: str, timeout: int = 30) -> Dict[str, Any]:
    prompt = '只返回JSON：{"ok": true}'
    try:
        body = post_json(
            api_key,
            f"{base_url.rstrip('/')}/responses",
            {"model": model, "input": prompt, "store": False, "max_output_tokens": 30},
            timeout,
        )
        content = extract_responses_text(body)
        return {"ok": bool(content), "status": 200, "reason": "ok", "api": "responses", "preview": content[:160]}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:220]
        responses_error = {"status": exc.code, "reason": classify_llm_failure(base_url, detail), "preview": detail}
    except Exception as exc:
        responses_error = {"status": None, "reason": type(exc).__name__, "preview": str(exc)[:220]}

    try:
        body = post_json(
            api_key,
            f"{base_url.rstrip('/')}/chat/completions",
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 30,
            },
            timeout,
        )
        choices = body.get("choices", [])
        content = choices[0].get("message", {}).get("content", "") if choices else ""
        return {"ok": bool(content), "status": 200, "reason": "ok", "api": "chat", "responses_error": responses_error, "preview": content[:160]}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:220]
        return {"ok": False, "status": exc.code, "reason": classify_llm_failure(base_url, detail), "api": "chat", "responses_error": responses_error, "preview": detail}
    except Exception as exc:
        return {"ok": False, "status": None, "reason": type(exc).__name__, "api": "chat", "responses_error": responses_error, "preview": str(exc)[:220]}


def build_injury_prompt(row: Dict[str, str]) -> str:
    season = row.get("season", "")[:4]
    competition = row.get("competition", "")
    stage = row.get("stage", "")
    group_name = row.get("group_name", "")
    round_text = row.get("round_text", "")
    home = row.get("home_team", "")
    away = row.get("away_team", "")
    match_time = row.get("match_time_utc", "")[:10]

    return (
        f"请根据你的足球知识，判断以下比赛中双方主力球员的伤停情况。"
        f"只需返回JSON，不要解释。\n\n"
        f"比赛：{season}年{competition} {stage} {group_name}{round_text}\n"
        f"时间：{match_time}\n"
        f"{home} vs {away}\n\n"
        f'返回格式：{{"home_key_injuries": 数量, "away_key_injuries": 数量, '
        f'"home_suspended": 数量, "away_suspended": 数量, "confidence": "high/medium/low"}}\n'
        f"注意：只统计主力/重要轮换球员的伤停，替补球员不算。如果不确定，数量填0。"
    )


def parse_llm_injury_response(text: Optional[str]) -> Dict[str, int]:
    if not text:
        return {}
    try:
        m = re.search(r"\{[^}]+\}", text, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
            return {
                "home_injury_count": int(data.get("home_key_injuries", 0)),
                "away_injury_count": int(data.get("away_key_injuries", 0)),
                "home_suspension_count": int(data.get("home_suspended", 0)),
                "away_suspension_count": int(data.get("away_suspended", 0)),
            }
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    return {}


def build_match_key(row: Dict[str, str]) -> str:
    match_time = row.get("match_time_utc", "")[:10]
    home = row.get("home_team", "")
    away = row.get("away_team", "")
    return f"{match_time}|{home}|{away}"


def load_existing_output(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            return {}
        index: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            key = f"{row.get('match_time', '')}|{row.get('home_team', '')}|{row.get('away_team', '')}"
            index[key] = row
        return index
    except Exception:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default=str(ROOT / ".env"))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--skip-llm", action="store_true", help="Only run rule engine, skip LLM calls")
    parser.add_argument("--llm-model", default="glm-4-flash", help="ZhipuGLM model name")
    parser.add_argument("--llm-delay", type=float, default=0.5, help="Delay between LLM calls (seconds)")
    parser.add_argument("--llm-smoke-test", action="store_true", help="Run one LLM call and exit")
    parser.add_argument("--llm-base-url", default="", help="Override LLM base URL for smoke tests/backfill")
    parser.add_argument("--llm-timeout", type=int, default=120, help="LLM request timeout in seconds")
    parser.add_argument("--llm-retries", type=int, default=1, help="LLM retry count after responses/chat both fail")
    parser.add_argument("--start-index", type=int, default=0, help="Start processing from this row index")
    parser.add_argument("--limit", type=int, default=0, help="Maximum rows to process; 0 means all rows")
    parser.add_argument("--force-llm", action="store_true", help="Re-run LLM even when existing source is rule_and_llm")
    args = parser.parse_args()

    env = {**os.environ, **load_env(Path(args.env))}
    output_path = Path(args.output)

    api_key = env.get("LLM_API_KEY", "") or env.get("ZHIPU_API_KEY", "") or env.get("OPENAI_API_KEY", "")
    base_url = args.llm_base_url or env.get("LLM_BASE_URL", DEFAULT_LLM_BASE_URL)
    model = env.get("LLM_MODEL") or args.llm_model or DEFAULT_LLM_MODEL

    if args.llm_smoke_test:
        if not api_key:
            print("LLM smoke test failed: no API key configured.")
            return 1
        result = llm_smoke_test(api_key, base_url=base_url, model=model, timeout=args.llm_timeout)
        safe = {"ok": result["ok"], "status": result["status"], "reason": result["reason"], "base_url": base_url, "model": model, "preview": result["preview"]}
        print(json.dumps(safe, ensure_ascii=False))
        if result["ok"]:
            return 0
        return 1

    all_csv_rows: List[Dict[str, str]] = []
    for csv_path in [DEFAULT_TRAIN_CSV, DEFAULT_VALIDATION_CSV, DEFAULT_TEST_CSV]:
        if csv_path.exists():
            with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
                all_csv_rows.extend(list(csv.DictReader(f)))

    total_rows = len(all_csv_rows)
    start = max(args.start_index, 0)
    end = total_rows if args.limit <= 0 else min(total_rows, start + args.limit)
    selected_rows = all_csv_rows[start:end]

    print(f"Loaded {total_rows} rows from train/validation/test CSVs")
    print(f"Processing rows [{start}:{end}] ({len(selected_rows)} rows)")

    existing = load_existing_output(output_path)
    results: Dict[str, Dict[str, Any]] = dict(existing)

    rule_only_count = 0
    llm_count = 0
    skipped_existing = 0

    for i, row in enumerate(selected_rows, start=start):
        match_key = build_match_key(row)
        match_time = row.get("match_time_utc", "")[:16]
        home = row.get("home_team", "")
        away = row.get("away_team", "")
        season = row.get("season", "")[:4]
        competition = row.get("competition", "")

        if not args.force_llm and match_key in results and results[match_key].get("source") == "rule_and_llm":
            skipped_existing += 1
            continue

        features = rule_based_features(row)

        entry: Dict[str, Any] = {
            "match_time": match_time,
            "home_team": home,
            "away_team": away,
            "competition": competition,
            "season": season,
            "stage": row.get("stage", ""),
            "group_name": row.get("group_name", ""),
            "round_text": row.get("round_text", ""),
            "home_injury_count": features["home_injury_count"],
            "away_injury_count": features["away_injury_count"],
            "home_suspension_count": features["home_suspension_count"],
            "away_suspension_count": features["away_suspension_count"],
            "home_lineup_known": features["home_lineup_known"],
            "away_lineup_known": features["away_lineup_known"],
            "home_rotation_flag": features["home_rotation_flag"],
            "away_rotation_flag": features["away_rotation_flag"],
            "must_win_flag_home": features["must_win_flag_home"],
            "must_win_flag_away": features["must_win_flag_away"],
            "home_rest_days": features["home_rest_days"],
            "away_rest_days": features["away_rest_days"],
            "source": "rule_only",
            "qtx_match_token": "",
            "supporting_sentences": [],
        }

        if not args.skip_llm and api_key:
            prompt = build_injury_prompt(row)
            response = call_llm(api_key, prompt, base_url=base_url, model=model, timeout=args.llm_timeout, retries=args.llm_retries)
            injury_data = parse_llm_injury_response(response)
            if injury_data:
                entry.update(injury_data)
                entry["source"] = "rule_and_llm"
                llm_count += 1
            else:
                entry["source"] = "rule_llm_failed"
            time.sleep(args.llm_delay)
        elif not args.skip_llm and not api_key:
            if i == 0:
                print("No ZHIPU_API_KEY or LLM_API_KEY found in .env. Skipping LLM calls.")

        results[match_key] = entry
        rule_only_count += 1

        processed_count = i - start + 1
        if processed_count % 50 == 0:
            print(f"  Processed {processed_count}/{len(selected_rows)}...")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(list(results.values()), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    output_list = sorted(results.values(), key=lambda r: (r.get("match_time", ""), r.get("home_team", "")))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output_list, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\nDone!")
    print(f"  Total entries: {len(output_list)}")
    print(f"  Rule-only: {sum(1 for r in output_list if r.get('source') == 'rule_only')}")
    print(f"  Rule+LLM: {sum(1 for r in output_list if r.get('source') == 'rule_and_llm')}")
    print(f"  LLM failed: {sum(1 for r in output_list if r.get('source') == 'rule_llm_failed')}")
    print(f"  Skipped (existing): {skipped_existing}")
    print(f"  Output: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
