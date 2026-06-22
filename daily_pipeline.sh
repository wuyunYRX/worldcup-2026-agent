#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [ -f ".venv/Scripts/python.exe" ]; then
    PYTHON=".venv/Scripts/python.exe"
else
    PYTHON="python3"
fi

RUN_AI_BACKFILL="${RUN_AI_BACKFILL:-0}"
AI_BACKFILL_START="${AI_BACKFILL_START:-0}"
AI_BACKFILL_LIMIT="${AI_BACKFILL_LIMIT:-10}"
AI_BACKFILL_TIMEOUT="${AI_BACKFILL_TIMEOUT:-120}"
AI_BACKFILL_RETRIES="${AI_BACKFILL_RETRIES:-1}"
AI_BACKFILL_DELAY="${AI_BACKFILL_DELAY:-0.5}"
AI_BACKFILL_REQUIRED="${AI_BACKFILL_REQUIRED:-0}"

RUN_POSTMATCH_REVIEW="${RUN_POSTMATCH_REVIEW:-0}"
POSTMATCH_REVIEW_REQUIRED="${POSTMATCH_REVIEW_REQUIRED:-0}"

warn_or_fail() {
    local message="$1"
    local required="$2"
    echo "WARN: ${message}" >&2
    if [ "$required" = "1" ]; then
        exit 1
    fi
}

echo "[1/6] Generate initial report"
"$PYTHON" src/worldcup_agent.py --env .env --skip-screenshot

echo "[2/6] Map QTX match tokens"
"$PYTHON" scripts/map_qtx_match_tokens.py || warn_or_fail "QTX token mapping failed" "0"

echo "[3/6] Fetch prematch team news"
"$PYTHON" scripts/fetch_prematch_team_news.py || warn_or_fail "Prematch team news fetch failed" "0"

if [ "$RUN_AI_BACKFILL" = "1" ]; then
    echo "[4/6] Run limited AI prematch backfill"
    "$PYTHON" scripts/backfill_prematch_features.py \
        --env .env \
        --start-index "$AI_BACKFILL_START" \
        --limit "$AI_BACKFILL_LIMIT" \
        --llm-timeout "$AI_BACKFILL_TIMEOUT" \
        --llm-retries "$AI_BACKFILL_RETRIES" \
        --llm-delay "$AI_BACKFILL_DELAY" \
        || warn_or_fail "AI prematch backfill failed" "$AI_BACKFILL_REQUIRED"
else
    echo "[4/6] Skip AI prematch backfill (RUN_AI_BACKFILL=0)"
fi

echo "[5/6] Generate final report"
"$PYTHON" src/worldcup_agent.py --env .env

if [ "$RUN_POSTMATCH_REVIEW" = "1" ]; then
    echo "[6/6] Run postmatch review"
    "$PYTHON" scripts/review_completed_matches.py || warn_or_fail "Postmatch review failed" "$POSTMATCH_REVIEW_REQUIRED"
else
    echo "[6/6] Skip postmatch review (RUN_POSTMATCH_REVIEW=0)"
fi

echo "Daily pipeline completed."
