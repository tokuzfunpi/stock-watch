#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

mkdir -p runs/scheduler
LOG_PATH="runs/scheduler/preopen.log"

preferred_python="/Users/tokuzfunpi/codes/nvidia/311env/bin/python"
python_bin="python3.11"
if [[ -x "$preferred_python" ]]; then
  if "$preferred_python" -c "import pandas" >/dev/null 2>&1; then
    python_bin="$preferred_python"
  fi
elif [[ -x ".venv/bin/python" ]]; then
  if .venv/bin/python -c "import pandas" >/dev/null 2>&1; then
    python_bin=".venv/bin/python"
  fi
fi

{
  echo "=== stock-watch preopen ==="
  echo "started_at=$(TZ=Asia/Taipei date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "mode=preopen"
  echo "repo_root=$REPO_ROOT"
  echo "python=$python_bin"
  echo "command=$python_bin -m stock_watch preopen"
  "$python_bin" -m stock_watch preopen

  # Backup verification history outside the repo (so `runs/` cleanup won't erase it).
  archive_dir="/Users/tokuzfunpi/.codex/automations/stock-watch-backup/archives/$(TZ=Asia/Taipei date '+%Y%m%d')"
  mkdir -p "$archive_dir"
  cp -f "runs/verification/watchlist_daily/reco_snapshots.csv" "$archive_dir/reco_snapshots.csv" 2>/dev/null || true
  cp -f "runs/verification/watchlist_daily/reco_outcomes.csv" "$archive_dir/reco_outcomes.csv" 2>/dev/null || true

  echo "finished_at=$(TZ=Asia/Taipei date '+%Y-%m-%d %H:%M:%S %Z')"
} >>"$LOG_PATH" 2>&1
