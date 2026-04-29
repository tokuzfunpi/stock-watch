#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

mkdir -p runs/scheduler
LOG_PATH="runs/scheduler/preopen.log"

python_bin="python3.11"
if [[ -x ".venv/bin/python" ]]; then
  if .venv/bin/python -c "import pandas" >/dev/null 2>&1; then
    python_bin=".venv/bin/python"
  fi
fi

{
  echo "=== stock-watch preopen ==="
  echo "started_at=$(TZ=Asia/Taipei date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "repo_root=$REPO_ROOT"
  echo "python=$python_bin"
  "$python_bin" -m stock_watch preopen
  echo "finished_at=$(TZ=Asia/Taipei date '+%Y-%m-%d %H:%M:%S %Z')"
} >>"$LOG_PATH" 2>&1
