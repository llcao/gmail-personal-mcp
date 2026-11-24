#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPORTS_DIR="$BASE_DIR/reports"
STAMP_FILE="$REPORTS_DIR/.last_run_date"
TODAY="$(date +%Y-%m-%d)"

mkdir -p "$REPORTS_DIR"

# Skip if already completed today (unless --force is passed)
if [[ "${1:-}" != "--force" && -f "$STAMP_FILE" ]] && [[ "$(cat "$STAMP_FILE")" == "$TODAY" ]]; then
  exit 0
fi

# Wait up to 45s for network (respecting macOS system proxy) after Mac wakes from sleep
for _ in $(seq 1 15); do
  if "$BASE_DIR/.venv/bin/python" -c 'import urllib.request; urllib.request.urlopen("https://www.googleapis.com/", timeout=3)' >/dev/null 2>&1; then
    break
  fi
  sleep 3
done

if [[ ! -f "$BASE_DIR/token.json" ]]; then
  /usr/bin/osascript -e 'display notification "Please run: ./.venv/bin/python gmail_triage.py auth" with title "Gmail Personal MCP"'
  exit 0
fi

cd "$BASE_DIR"
"$BASE_DIR/.venv/bin/python" "$BASE_DIR/gmail_triage.py" fetch

SUMMARY=$("$BASE_DIR/.venv/bin/python" -c '
import json
d = json.load(open("reports/latest_fetch.json", encoding="utf-8"))["counts"]
print(f"Priority: {d.get(\"priority_urgent\", 0)} | Urgent: {d.get(\"urgent_candidate\", 0)} | Read-Unreplied: {d.get(\"read_unreplied\", 0)}")
')

echo "$TODAY" > "$STAMP_FILE"

/usr/bin/osascript -e "display notification \"$SUMMARY\" with title \"Daily Gmail Briefing ($TODAY)\""
