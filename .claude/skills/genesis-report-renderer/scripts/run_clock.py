#!/usr/bin/env python3
"""End-to-end run clock for Genesis audit agents. Standard library only.

The agent starts the clock as its very first action:
    python <skill-dir>/scripts/run_clock.py start <agent_id>
build_report.py reads it when rendering, so the reported execution time covers
the whole run (questions, Jira/MCP calls, evaluation and HTML render), and then
removes it.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# <workspace>/<.agents|.claude|.cursor|.github>/skills/genesis-report-renderer/scripts/run_clock.py
WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
MAX_AGE_SECONDS = 6 * 3600
CLOCK_NAME = ".run-clock.json"


def clock_path(agent_id: str) -> Path:
    return WORKSPACE_ROOT / "reports" / agent_id / CLOCK_NAME


def start(agent_id: str) -> dict:
    now = time.time()
    clock = {
        "agent_id": agent_id,
        "epoch": now,
        "started_at": datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds"),
    }
    path = clock_path(agent_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clock), encoding="utf-8")
    return clock


def read(agent_id: str) -> Optional[dict]:
    """Return the clock if it exists and is fresh; stale clocks are deleted."""
    path = clock_path(agent_id)
    if not path.is_file():
        return None
    try:
        clock = json.loads(path.read_text(encoding="utf-8"))
        age = time.time() - float(clock["epoch"])
    except (OSError, ValueError, KeyError, TypeError):
        clear(agent_id)
        return None
    if age < 0 or age > MAX_AGE_SECONDS:
        clear(agent_id)
        return None
    return clock


def clear(agent_id: str) -> None:
    try:
        clock_path(agent_id).unlink()
    except OSError:
        pass


def format_duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m {secs:02d}s"


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Start/inspect the end-to-end run clock of a Genesis agent")
    parser.add_argument("command", choices=["start", "status", "clear"])
    parser.add_argument("agent_id", help="kebab-case agent id, e.g. story-dod or sprint-dod")
    args = parser.parse_args(argv)

    if args.command == "start":
        clock = start(args.agent_id)
        print(f"CLOCK started for {args.agent_id} at {clock['started_at']}")
    elif args.command == "status":
        clock = read(args.agent_id)
        if clock is None:
            print(f"CLOCK none for {args.agent_id}")
            return 1
        print(f"CLOCK {args.agent_id}: {format_duration(time.time() - clock['epoch'])} since {clock['started_at']}")
    else:
        clear(args.agent_id)
        print(f"CLOCK cleared for {args.agent_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
