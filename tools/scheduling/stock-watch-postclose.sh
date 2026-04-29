#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

mkdir -p runs/scheduler
LOG_PATH="runs/scheduler/postclose.log"

python_bin="python3.11"
if [[ -x ".venv/bin/python" ]]; then
  if .venv/bin/python -c "import pandas" >/dev/null 2>&1; then
    python_bin=".venv/bin/python"
  fi
fi

{
  echo "=== stock-watch postclose ==="
  echo "started_at=$(TZ=Asia/Taipei date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "repo_root=$REPO_ROOT"
  echo "python=$python_bin"
  # Keep evaluation history updated as older horizons mature.
  "$python_bin" -m stock_watch daily --mode postclose --all-dates --max-days 60

  # Yahoo/TW tickers sometimes lag after close; retry evaluation if today's rows are missing.
  for attempt in 1 2 3 4; do
    missing_count="$(
      "$python_bin" - <<'PY'
import datetime
import pandas as pd
import pytz

tz = pytz.timezone("Asia/Taipei")
today = datetime.datetime.now(tz).strftime("%Y-%m-%d")

try:
  df = pd.read_csv("runs/verification/watchlist_daily/reco_outcomes.csv")
except Exception:
  print(999)
  raise SystemExit(0)

if df.empty or "status" not in df.columns or "signal_date" not in df.columns:
  print(999)
  raise SystemExit(0)

sub = df[df["signal_date"].astype(str) == today]
if sub.empty:
  print(999)
  raise SystemExit(0)

status = sub["status"].astype(str).str.strip()
print(int((status == "signal_date_missing").sum()))
PY
    )"

    if [[ "$missing_count" -eq 0 ]]; then
      echo "eval_retry=ok attempt=$attempt"
      break
    fi

    echo "eval_retry=signal_date_missing count=$missing_count attempt=$attempt"
    if [[ "$attempt" -lt 4 ]]; then
      sleep 600
      "$python_bin" -m stock_watch verification daily --mode postclose --all-dates --max-days 60
    fi
  done

  # Backup verification history outside the repo (so `runs/` cleanup won't erase it).
  archive_dir="/Users/tokuzfunpi/.codex/automations/stock-watch-backup/archives/$(TZ=Asia/Taipei date '+%Y%m%d')"
  mkdir -p "$archive_dir"
  cp -f "runs/verification/watchlist_daily/reco_snapshots.csv" "$archive_dir/reco_snapshots.csv" 2>/dev/null || true
  cp -f "runs/verification/watchlist_daily/reco_outcomes.csv" "$archive_dir/reco_outcomes.csv" 2>/dev/null || true

  echo "finished_at=$(TZ=Asia/Taipei date '+%Y-%m-%d %H:%M:%S %Z')"
} >>"$LOG_PATH" 2>&1
