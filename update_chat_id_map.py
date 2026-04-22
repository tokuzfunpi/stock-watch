from __future__ import annotations

import csv
import os
import sys
from pathlib import Path
from typing import Iterable

import requests

BASE_DIR = Path(__file__).resolve().parent
CHAT_ID_MAP_CSV = Path(os.getenv("CHAT_ID_MAP_CSV", BASE_DIR / "chat_id_map.csv"))
GETUPDATES_URL_PATH = Path(os.getenv("GETUPDATES_URL_PATH", BASE_DIR / "telegram_getupdates_url"))
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "20"))

FIELDNAMES = ["chat_id", "first_name", "last_name", "username", "chat_type", "source"]


def resolve_getupdates_url() -> str:
    env_url = os.getenv("TELEGRAM_GETUPDATES_URL", "").strip()
    if env_url:
        return env_url

    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    if token:
        return f"https://api.telegram.org/bot{token}/getUpdates"

    if GETUPDATES_URL_PATH.exists():
        return GETUPDATES_URL_PATH.read_text(encoding="utf-8-sig").strip()

    raise RuntimeError(
        "Missing Telegram getUpdates URL. Set TELEGRAM_GETUPDATES_URL, TELEGRAM_TOKEN, "
        "or create local telegram_getupdates_url."
    )


def extract_chat_rows(updates: Iterable[dict]) -> list[dict]:
    rows_by_chat_id: dict[str, dict] = {}
    for update in updates:
        payload = None
        for key in ["message", "edited_message", "channel_post", "edited_channel_post"]:
            if key in update:
                payload = update[key]
                break
        if not payload:
            continue

        chat = payload.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            continue

        row = {
            "chat_id": str(chat_id),
            "first_name": str(chat.get("first_name", "")).strip(),
            "last_name": str(chat.get("last_name", "")).strip(),
            "username": str(chat.get("username", "")).strip(),
            "chat_type": str(chat.get("type", "")).strip(),
            "source": "telegram getUpdates",
        }
        rows_by_chat_id[str(chat_id)] = row
    return list(rows_by_chat_id.values())


def load_existing_rows(csv_path: Path) -> dict[str, dict]:
    if not csv_path.exists():
        return {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        return {str(row["chat_id"]): row for row in csv.DictReader(f)}


def merge_rows(existing: dict[str, dict], incoming: list[dict]) -> tuple[list[dict], int, int]:
    added = 0
    updated = 0
    merged = {key: value.copy() for key, value in existing.items()}

    for row in incoming:
        chat_id = str(row["chat_id"])
        if chat_id not in merged:
            merged[chat_id] = row
            added += 1
            continue
        if merged[chat_id] != row:
            merged[chat_id] = row
            updated += 1

    output = sorted(merged.values(), key=lambda row: int(row["chat_id"]))
    return output, added, updated


def write_rows(csv_path: Path, rows: list[dict]) -> None:
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    try:
        url = resolve_getupdates_url()
        response = requests.get(url, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
        updates = payload.get("result", [])

        incoming = extract_chat_rows(updates)
        existing = load_existing_rows(CHAT_ID_MAP_CSV)
        merged_rows, added, updated = merge_rows(existing, incoming)
        write_rows(CHAT_ID_MAP_CSV, merged_rows)

        print(f"Updated {CHAT_ID_MAP_CSV}")
        print(f"Rows: {len(merged_rows)} | Added: {added} | Updated: {updated}")
        for row in merged_rows:
            label = " ".join(part for part in [row["first_name"], row["last_name"]] if part).strip() or row["username"] or row["chat_id"]
            print(f'- {row["chat_id"]} | {label} | {row["chat_type"]}')
        return 0
    except Exception as exc:
        print(f"update_chat_id_map failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
