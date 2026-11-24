#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOUR="${1:-7}"
MINUTE="${2:-0}"

PLIST_NAME="com.gmail-personal-mcp.daily-check.plist"
DST="$HOME/Library/LaunchAgents/$PLIST_NAME"

mkdir -p "$HOME/Library/LaunchAgents" "$BASE_DIR/reports"

cat > "$DST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.gmail-personal-mcp.daily-check</string>

  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${BASE_DIR}/scripts/run_daily_check.sh</string>
  </array>

  <!-- Runs daily at ${HOUR}:$(printf '%02d' "$MINUTE"); if Mac is asleep, launchd automatically runs on wake -->
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>${HOUR}</integer>
    <key>Minute</key>
    <integer>${MINUTE}</integer>
  </dict>

  <key>StandardOutPath</key>
  <string>${BASE_DIR}/reports/launchd.log</string>
  <key>StandardErrorPath</key>
  <string>${BASE_DIR}/reports/launchd.err.log</string>
</dict>
</plist>
EOF

launchctl unload "$DST" >/dev/null 2>&1 || true
launchctl load "$DST"
echo "Installed & loaded LaunchAgent: $DST"
echo "Schedule: Daily at $(printf '%02d:%02d' "$HOUR" "$MINUTE") (auto-runs on wake if Mac was asleep)."
echo "To uninstall: launchctl unload \"$DST\" && rm \"$DST\""
