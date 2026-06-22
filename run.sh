#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [ -f ".venv/Scripts/python.exe" ]; then
    PYTHON=".venv/Scripts/python.exe"
else
    PYTHON="python3"
fi

echo "[run.sh] Running prepass world cup agent..."
"$PYTHON" src/worldcup_agent.py --env .env --skip-screenshot
echo "[run.sh] Mapping qtx match tokens..."
"$PYTHON" scripts/map_qtx_match_tokens.py
echo "[run.sh] Fetching prematch team news..."
"$PYTHON" scripts/fetch_prematch_team_news.py
echo "[run.sh] Running final world cup agent..."
"$PYTHON" src/worldcup_agent.py --env .env
