#!/usr/bin/env python3
"""Validate and normalize a Genesis report-data.json (contract 1.x). Standard library only."""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Set

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_clock import format_duration  # noqa: E402

SUPPORTED_MAJOR = 1
ITEM_STATUSES = {"pass", "fail", "manual", "info", "gap", "contradiction", "discrepancy", "na"}
SUMMARY_STATUSES = {"pass", "warning", "fail"}
SOURCE_STATUSES = {"ok", "partial", "unavailable", "error"}
MANUAL_REASONS = {"by-design", "tool-unavailable"}
NOTICE_LEVELS = {"info", "warning"}
LANGUAGES = {"es", "en"}
KNOWN_SECTION_TYPES = {"criteria", "issue-table", "distribution", "metric-gate", "key-value", "generic-table"}
PLANNED_SECTION_TYPES = {"coverage-matrix", "deliverables", "set-diff", "doc-score"}
KEBAB = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VERSION = re.compile(r"^(\d+)\.(\d+)$")
STATUS_CATEGORY_BY_NAME = {"closed": "pass", "done": "pass", "resolved": "info"}


class Findings:
    def __init__(self) -> None:
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def error(self, path: str, msg: str) -> None:
        self.errors.append(f"ERROR {path}: {msg}")

    def warn(self, path: str, msg: str) -> None:
        self.warnings.append(f"WARN {path}: {msg}")


def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_iso(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _check_status(value: Any, path: str, f: Findings, allowed: Set[str], required: bool = True) -> None:
    if value is None and not required:
        return
    if value not in allowed:
        f.error(path, f"invalid status {value!r}; expected one of {sorted(allowed)}")


def _list_of_objects(container: dict, key: str, path: str, f: Findings, required: bool = True) -> list:
    value = container.get(key)
    if value is None and not required:
        return []
    if not isinstance(value, list):
        f.error(f"{path}.{key}", "must be an array")
        return []
    result = []
    for i, item in enumerate(value):
        if isinstance(item, dict):
            result.append((f"{path}.{key}[{i}]", item))
        else:
            f.error(f"{path}.{key}[{i}]", "must be an object")
    return result


def _validate_meta(meta: dict, f: Findings) -> None:
    agent_id = meta.get("agent_id")
    if not isinstance(agent_id, str) or not KEBAB.match(agent_id):
        f.error("$.meta.agent_id", "required, kebab-case string (e.g. 'sprint-dod')")
    if not _is_iso(meta.get("generated_at")):
        f.error("$.meta.generated_at", "required, ISO 8601 datetime (e.g. '2026-10-08T10:27:00-03:00')")
    if "run_id" not in meta:
        f.warn("$.meta.run_id", "missing; one will be generated (checklist progress is stored per run_id)")
    lang = meta.get("language")
    if lang is not None and lang not in LANGUAGES:
        f.warn("$.meta.language", f"unsupported {lang!r}; 'es' will be used")
    started = meta.get("started_at")
    if started is not None and not _is_iso(started):
        f.warn("$.meta.started_at", "should be an ISO 8601 datetime (e.g. '2026-10-29T11:46:00-03:00')")
    exec_sec = meta.get("execution_time_seconds")
    if exec_sec is not None and not _is_num(exec_sec):
        f.warn("$.meta.execution_time_seconds", "should be a number in seconds")
    if "scope" in meta and not isinstance(meta["scope"], dict):
        f.error("$.meta.scope", "must be an object")
    url = meta.get("jira_base_url")
    if url is not None and (not isinstance(url, str) or not url.lower().startswith("https://") or "<" in url):
        f.error("$.meta.jira_base_url", "must be an https:// URL (e.g. 'https://site.atlassian.net/browse/')")
    for path, src in _list_of_objects(meta, "sources", "$.meta", f, required=False):
        if not isinstance(src.get("name"), str):
            f.error(f"{path}.name", "required string")
        _check_status(src.get("status"), f"{path}.status", f, SOURCE_STATUSES)
        if src.get("status") != "ok" and not src.get("reason"):
            f.warn(f"{path}.reason", "recommended when status is not 'ok'")


def _validate_summary(summary: dict, f: Findings) -> None:
    _check_status(summary.get("status"), "$.summary.status", f, SUMMARY_STATUSES)
    score = summary.get("score")
    if score is not None and (not _is_num(score) or not 0 <= score <= 100):
        f.error("$.summary.score", "must be null or a number between 0 and 100")
    for path, kpi in _list_of_objects(summary, "kpis", "$.summary", f, required=False):
        if not isinstance(kpi.get("label"), str):
            f.error(f"{path}.label", "required string")
        if not _is_num(kpi.get("value")):
            f.error(f"{path}.value", "required number")
        if kpi.get("total") is not None and not _is_num(kpi.get("total")):
            f.error(f"{path}.total", "must be a number")
        _check_status(kpi.get("status"), f"{path}.status", f, ITEM_STATUSES, required=False)


def _validate_section(sec: dict, path: str, f: Findings) -> None:
    stype = sec.get("type")
    if not isinstance(stype, str) or not stype:
        f.error(f"{path}.type", "required string")
        return

    if stype == "criteria":
        for p, item in _list_of_objects(sec, "items", path, f):
            if not isinstance(item.get("title"), str):
                f.error(f"{p}.title", "required string")
            _check_status(item.get("status"), f"{p}.status", f, ITEM_STATUSES)
            if item.get("status") == "manual" and item.get("reason") not in MANUAL_REASONS:
                f.warn(f"{p}.reason", f"manual items should set reason to one of {sorted(MANUAL_REASONS)}")
            for key in ("evidence", "issues", "checks"):
                if key in item and not _str_list(item[key]):
                    f.error(f"{p}.{key}", "must be an array of strings")
            if "description" in item and not isinstance(item["description"], str):
                f.error(f"{p}.description", "must be a string")
            if "warning" in item and not isinstance(item["warning"], (str, bool)):
                f.warn(f"{p}.warning", "should be a string or boolean (e.g. '56 tareas postergadas al Sprint 184')")
    elif stype == "issue-table":
        for p, row in _list_of_objects(sec, "rows", path, f):
            if not isinstance(row.get("key"), str) or not row["key"]:
                f.error(f"{p}.key", "required string")
            _check_status(row.get("status_category"), f"{p}.status_category", f, ITEM_STATUSES, required=False)
            if "resolution" in row and row["resolution"] is not None and not isinstance(row["resolution"], str):
                f.error(f"{p}.resolution", "must be a string or null")
    elif stype == "distribution":
        for p, item in _list_of_objects(sec, "items", path, f):
            if not isinstance(item.get("label"), str):
                f.error(f"{p}.label", "required string")
            if not _is_num(item.get("value")) or item["value"] < 0:
                f.error(f"{p}.value", "required number >= 0")
            _check_status(item.get("status"), f"{p}.status", f, ITEM_STATUSES, required=False)
    elif stype == "metric-gate":
        for p, item in _list_of_objects(sec, "items", path, f):
            if not isinstance(item.get("label"), str):
                f.error(f"{p}.label", "required string")
            _check_status(item.get("status"), f"{p}.status", f, ITEM_STATUSES)
    elif stype == "key-value":
        for p, item in _list_of_objects(sec, "items", path, f):
            if not isinstance(item.get("key"), str):
                f.error(f"{p}.key", "required string")
    elif stype == "generic-table":
        if not isinstance(sec.get("rows"), list):
            f.error(f"{path}.rows", "must be an array")
        if "columns" in sec and not _str_list(sec["columns"]):
            f.error(f"{path}.columns", "must be an array of strings")
    elif stype in PLANNED_SECTION_TYPES:
        f.warn(f"{path}.type", f"'{stype}' has no dedicated component yet; generic fallback will be used")
    else:
        f.warn(f"{path}.type", f"unknown type '{stype}'; generic fallback will be used")


def validate(data: Any) -> Findings:
    f = Findings()
    if not isinstance(data, dict):
        f.error("$", "root must be an object")
        return f

    match = VERSION.match(str(data.get("schema_version", "")))
    if not match:
        f.error("$.schema_version", "required string 'MAJOR.MINOR' (e.g. '1.0')")
    elif int(match.group(1)) != SUPPORTED_MAJOR:
        f.error("$.schema_version", f"major version {match.group(1)} not supported (expected {SUPPORTED_MAJOR}.x)")

    meta = data.get("meta")
    if isinstance(meta, dict):
        _validate_meta(meta, f)
    else:
        f.error("$.meta", "required object")

    summary = data.get("summary")
    if isinstance(summary, dict):
        _validate_summary(summary, f)
    else:
        f.error("$.summary", "required object")

    seen_ids: Set[str] = set()
    for path, sec in _list_of_objects(data, "sections", "$", f):
        sid = sec.get("id")
        if sid is not None:
            if not isinstance(sid, str):
                f.error(f"{path}.id", "must be a string")
            elif sid in seen_ids:
                f.error(f"{path}.id", f"duplicate section id '{sid}'")
            else:
                seen_ids.add(sid)
        _validate_section(sec, path, f)

    action_ids: Set[str] = set()
    for path, action in _list_of_objects(data, "actions", "$", f, required=False):
        if not isinstance(action.get("text"), str) or not action["text"].strip():
            f.error(f"{path}.text", "required non-empty string")
        aid = action.get("id")
        if aid is not None:
            if aid in action_ids:
                f.error(f"{path}.id", f"duplicate action id '{aid}'")
            action_ids.add(aid)
        if "issues" in action and not _str_list(action["issues"]):
            f.error(f"{path}.issues", "must be an array of strings")

    for path, lim in _list_of_objects(data, "limitations", "$", f, required=False):
        if not isinstance(lim.get("text"), str):
            f.error(f"{path}.text", "required string")

    for path, notice in _list_of_objects(data, "notices", "$", f, required=False):
        if not isinstance(notice.get("text"), str) or not notice["text"].strip():
            f.error(f"{path}.text", "required non-empty string")
        if notice.get("level", "info") not in NOTICE_LEVELS:
            f.error(f"{path}.level", f"must be one of {sorted(NOTICE_LEVELS)}")

    return f


def normalize(data: dict) -> dict:
    """Return a copy with defaults filled in. Assumes validate() returned no errors."""
    out = copy.deepcopy(data)
    meta = out["meta"]
    meta.setdefault("language", "es")
    if meta["language"] not in LANGUAGES:
        meta["language"] = "es"
    meta.setdefault("scope", {})
    meta.setdefault("sources", [])
    if "run_id" not in meta:
        stamp = re.sub(r"[^0-9T]", "", meta["generated_at"])[:13]
        meta["run_id"] = f"{meta['agent_id']}-{meta['scope'].get('id', 'run')}-{stamp}"
    if "execution_time" not in meta and "started_at" in meta and "generated_at" in meta:
        try:
            d_start = datetime.fromisoformat(meta["started_at"].replace("Z", "+00:00"))
            d_end = datetime.fromisoformat(meta["generated_at"].replace("Z", "+00:00"))
            delta = (d_end - d_start).total_seconds()
            if delta >= 0:
                meta.setdefault("execution_time_seconds", round(delta, 2))
                meta.setdefault("execution_time", format_duration(delta))
        except Exception:
            pass
    elif "execution_time" not in meta and _is_num(meta.get("execution_time_seconds")):
        meta["execution_time"] = format_duration(meta["execution_time_seconds"])
    url: Optional[str] = meta.get("jira_base_url")
    if url and not url.endswith("/"):
        meta["jira_base_url"] = url + "/"

    out["summary"].setdefault("kpis", [])
    out.setdefault("actions", [])
    out.setdefault("limitations", [])
    out.setdefault("notices", [])

    for i, sec in enumerate(out["sections"]):
        sec.setdefault("id", f"{sec['type']}-{i + 1}")
        if sec["type"] == "issue-table":
            for row in sec["rows"]:
                if "status_category" not in row:
                    row["status_category"] = STATUS_CATEGORY_BY_NAME.get(str(row.get("status", "")).lower(), "na")

    for i, action in enumerate(out["actions"]):
        action.setdefault("id", f"a{i + 1}")
    return out


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def configure_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: Optional[List[str]] = None) -> int:
    configure_stdout()
    parser = argparse.ArgumentParser(description="Validate a Genesis report-data.json")
    parser.add_argument("input", type=Path)
    args = parser.parse_args(argv)
    try:
        data = load_json(args.input)
    except OSError as exc:
        print(f"ERROR $: cannot read file: {exc}")
        return 2
    except json.JSONDecodeError as exc:
        print(f"ERROR $: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}")
        return 1
    findings = validate(data)
    for line in findings.warnings + findings.errors:
        print(line)
    if findings.errors:
        return 1
    print(f"OK: {args.input} is valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
