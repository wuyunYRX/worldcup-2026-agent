#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "Installing 2026 World Cup Prediction Agent..."

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required. Please install Python 3.10+ first."
  exit 1
fi

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip >/dev/null

if [ -s requirements.txt ]; then
  .venv/bin/python -m pip install -r requirements.txt
fi

mkdir -p docs logs launchd

if [ ! -f .env ]; then
  cp .env.example .env
fi

chmod +x run.sh

PLIST_PATH="launchd/worldcup-2026-agent.plist.example"
cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.local.worldcup-2026-agent</string>
  <key>ProgramArguments</key>
  <array>
    <string>${ROOT_DIR}/run.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${ROOT_DIR}</string>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>18</integer><key>Minute</key><integer>0</integer></dict>
  </array>
  <key>StandardOutPath</key>
  <string>${ROOT_DIR}/logs/agent.log</string>
  <key>StandardErrorPath</key>
  <string>${ROOT_DIR}/logs/agent.err.log</string>
</dict>
</plist>
PLIST

echo "Done."
echo "Next steps:"
echo "1. Edit .env if you want Telegram notification."
echo "2. Run ./run.sh"
echo "3. Open docs/worldcup-2026-agent-report.html"
