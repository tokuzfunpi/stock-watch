#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LA_DIR="$HOME/Library/LaunchAgents"
UID_NUM="$(id -u)"

mkdir -p "$LA_DIR" "$REPO_ROOT/runs/scheduler"

install_one() {
  local src="$1"
  local dst="$2"
  cp "$src" "$dst"
  perl -pi -e "s|__REPO_ROOT__|$REPO_ROOT|g" "$dst"
}

install_one "$REPO_ROOT/tools/scheduling/com.stockwatch.preopen.plist" "$LA_DIR/com.stockwatch.preopen.plist"
install_one "$REPO_ROOT/tools/scheduling/com.stockwatch.postclose.plist" "$LA_DIR/com.stockwatch.postclose.plist"

chmod +x "$REPO_ROOT/tools/scheduling/stock-watch-preopen.sh"
chmod +x "$REPO_ROOT/tools/scheduling/stock-watch-postclose.sh"

launchctl bootout "gui/$UID_NUM" "$LA_DIR/com.stockwatch.preopen.plist" >/dev/null 2>&1 || true
launchctl bootout "gui/$UID_NUM" "$LA_DIR/com.stockwatch.postclose.plist" >/dev/null 2>&1 || true

launchctl bootstrap "gui/$UID_NUM" "$LA_DIR/com.stockwatch.preopen.plist"
launchctl bootstrap "gui/$UID_NUM" "$LA_DIR/com.stockwatch.postclose.plist"

echo "Installed launchd agents:"
echo "  $LA_DIR/com.stockwatch.preopen.plist (08:45 Asia/Taipei)"
echo "  $LA_DIR/com.stockwatch.postclose.plist (14:00 Asia/Taipei)"
echo
echo "Check status:"
echo "  launchctl print gui/$UID_NUM/com.stockwatch.preopen"
echo "  launchctl print gui/$UID_NUM/com.stockwatch.postclose"

