#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Optional


ROOT_DIR = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT_DIR / "data" / "sync-schedule-state.json"
LOCK_DIR = ROOT_DIR / "data" / ".sync-scheduler.lock"
SYNC_SCRIPT = ROOT_DIR / "scripts" / "sync-now.sh"


def due_slot(now: datetime) -> datetime:
    local_now = now.astimezone()
    today = local_now.date()
    slots = [
        datetime.combine(today, time(0, 0), tzinfo=local_now.tzinfo),
        datetime.combine(today, time(12, 0), tzinfo=local_now.tzinfo),
    ]
    due = [slot for slot in slots if slot <= local_now]
    if due:
        return due[-1]
    return datetime.combine(today - timedelta(days=1), time(12, 0), tzinfo=local_now.tzinfo)


def load_last_success_slot() -> Optional[str]:
    try:
        payload = json.loads(STATE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    value = payload.get("last_success_slot")
    return str(value) if value else None


def save_success_slot(slot_id: str) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(
            {
                "last_success_slot": slot_id,
                "updated_at": datetime.now().astimezone().isoformat(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def main() -> int:
    ROOT_DIR.joinpath("data").mkdir(parents=True, exist_ok=True)
    try:
        LOCK_DIR.mkdir()
    except FileExistsError:
        print(f"{datetime.now().isoformat()} sync scheduler already running; skipping")
        return 0

    try:
        slot_id = due_slot(datetime.now()).isoformat()
        if load_last_success_slot() == slot_id:
            print(f"{datetime.now().isoformat()} no sync due; latest completed slot is {slot_id}")
            return 0

        print(f"{datetime.now().isoformat()} running scheduled portfolio sync for slot {slot_id}")
        completed = subprocess.run([str(SYNC_SCRIPT)], cwd=str(ROOT_DIR), text=True)
        if completed.returncode == 0:
            save_success_slot(slot_id)
        return completed.returncode
    finally:
        try:
            LOCK_DIR.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
