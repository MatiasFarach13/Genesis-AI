#!/usr/bin/env python3
"""Build a self-contained intive | GENESIS HTML report from a report-data.json. Standard library only.

Exit codes: 0 = OK, 1 = contract validation failed, 2 = file/environment problem.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_clock  # noqa: E402
from validate_report import configure_stdout, load_json, normalize, validate  # noqa: E402

RENDERER_VERSION = "1.2.0"
SKILL_DIR = Path(__file__).resolve().parent.parent
ASSETS = SKILL_DIR / "assets"
PLACEHOLDER = re.compile(r"/\*__(STYLES|DATA|PROFILE|RENDERER)__\*/|__(TITLE|LANG|VERSION)__")
TITLE_TOKEN = re.compile(r"\{([A-Za-z0-9_.]+)\}")


def safe_json(obj: Any) -> str:
    """Serialize JSON so it cannot break out of a <script> element."""
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    for char, escaped in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"),
                          ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        text = text.replace(char, escaped)
    return text


def lookup(data: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(part)
    return data


def resolve_title(profile: Optional[dict], data: dict) -> str:
    template = (profile or {}).get("title")
    if isinstance(template, str):
        title = TITLE_TOKEN.sub(lambda m: str(lookup(data, m.group(1)) or ""), template).strip(" ·-")
        if title:
            return title
    return data["summary"].get("headline") or data["meta"]["agent_id"]


def load_profile(agent_id: str, override: Optional[Path]) -> Optional[dict]:
    path = override or ASSETS / "profiles" / f"{agent_id}.profile.json"
    if not path.is_file():
        if override:
            raise OSError(f"profile not found: {override}")
        return None
    profile = load_json(path)
    if not isinstance(profile, dict):
        raise ValueError(f"profile must be a JSON object: {path}")
    return profile


def read_asset(name: str, forbidden: str) -> str:
    text = (ASSETS / name).read_text(encoding="utf-8")
    if forbidden in text.lower():
        raise ValueError(f"asset {name} must not contain '{forbidden}'")
    return text


def apply_run_clock(raw: dict, data: dict) -> Optional[dict]:
    """Overwrite timing meta with the end-to-end run clock (agent start -> render). Returns the clock used."""
    clock = run_clock.read(data["meta"]["agent_id"])
    if clock is None:
        return None
    now = time.time()
    total = now - float(clock["epoch"])
    previous = data["meta"].get("execution_time_seconds")
    timing = {
        "started_at": clock["started_at"],
        "generated_at": datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds"),
        "execution_time_seconds": round(total, 2),
        "execution_time": run_clock.format_duration(total),
        "timing": {"source": "run-clock"},
    }
    if isinstance(previous, (int, float)) and not isinstance(previous, bool) and 0 <= previous <= total:
        timing["timing"]["engine_seconds"] = round(float(previous), 2)
    for target in (raw["meta"], data["meta"]):
        target.update(timing)
    return clock


def build(data: dict, profile: Optional[dict]) -> str:
    template = (ASSETS / "template.html").read_text(encoding="utf-8")
    values = {
        "STYLES": read_asset("styles.css", "</style"),
        "RENDERER": read_asset("renderer.js", "</script"),
        "DATA": safe_json(data),
        "PROFILE": safe_json(profile),
        "TITLE": html.escape(f"{resolve_title(profile, data)} | intive GENESIS"),
        "LANG": html.escape(data["meta"]["language"]),
        "VERSION": RENDERER_VERSION,
    }
    # Single pass: injected content is never re-scanned for placeholders.
    return PLACEHOLDER.sub(lambda m: values[m.group(1) or m.group(2)], template)


def main(argv: Optional[List[str]] = None) -> int:
    t_start = time.perf_counter()
    configure_stdout()
    parser = argparse.ArgumentParser(description="Render a Genesis report-data.json into report.html")
    parser.add_argument("input", type=Path, help="path to report-data.json")
    parser.add_argument("--out", type=Path, help="output HTML path (default: report.html next to the input)")
    parser.add_argument("--profile", type=Path, help="force a profile file instead of assets/profiles/<agent_id>")
    parser.add_argument("--validate-only", action="store_true", help="only validate the contract")
    args = parser.parse_args(argv)

    try:
        data = load_json(args.input)
    except OSError as exc:
        print(f"ERROR $: cannot read input: {exc}")
        return 2
    except json.JSONDecodeError as exc:
        print(f"ERROR $: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}")
        return 1

    t_val_start = time.perf_counter()
    findings = validate(data)
    t_val = time.perf_counter() - t_val_start
    for line in findings.warnings + findings.errors:
        print(line)
    if findings.errors:
        print(f"FAILED: {len(findings.errors)} validation error(s). Fix the JSON and run again.")
        return 1
    if args.validate_only:
        print(f"OK: {args.input} is valid (validation: {t_val:.3f}s)")
        return 0

    raw = data
    data = normalize(data)
    clock = apply_run_clock(raw, data)
    try:
        profile = load_profile(data["meta"]["agent_id"], args.profile)
        if profile is None:
            print(f"WARN $.meta.agent_id: no profile for '{data['meta']['agent_id']}'; generic layout used")
        t_build_start = time.perf_counter()
        output = build(data, profile)
        out_path = args.out or args.input.with_name("report.html")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        if clock is not None:
            args.input.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
        t_build = time.perf_counter() - t_build_start
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR assets: {exc}")
        return 2
    if clock is not None:
        run_clock.clear(data["meta"]["agent_id"])
    else:
        print(f"WARN $.meta.execution_time: no run clock for '{data['meta']['agent_id']}'; "
              "agent-reported time kept (start it with run_clock.py start <agent_id>)")

    t_total = time.perf_counter() - t_start
    agent_exec = data["meta"].get("execution_time")
    agent_info = f" | agent execution: {agent_exec}{' (end-to-end)' if clock else ''}" if agent_exec else ""
    print(f"OK: {out_path} (skill build: {t_total:.3f}s [validate: {t_val:.3f}s, render: {t_build:.3f}s]{agent_info})")
    return 0

if __name__ == "__main__":
    sys.exit(main())
