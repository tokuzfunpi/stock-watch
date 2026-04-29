#!/usr/bin/env bash
set -euo pipefail

LA_DIR="$HOME/Library/LaunchAgents"
UID_NUM="$(id -u)"

launchctl bootout "gui/$UID_NUM" "$LA_DIR/com.stockwatch.preopen.plist" >/dev/null 2>&1 || true
launchctl bootout "gui/$UID_NUM" "$LA_DIR/com.stockwatch.postclose.plist" >/dev/null 2>&1 || true

rm -f "$LA_DIR/com.stockwatch.preopen.plist" "$LA_DIR/com.stockwatch.postclose.plist"
echo "Removed launchd agents."

