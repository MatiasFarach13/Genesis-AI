---
name: genesis-report-renderer
title: Genesis Report Renderer
type: skill
description: "Renders the report-data.json produced by a Genesis audit agent (Sprint DoD, Project Audit, Release Notes review) into a self-contained, offline, intive | GENESIS branded HTML report. Use when: an agent has written a report-data.json and needs its HTML report, or a report-data.json must be validated against the report contract. Triggers: 'generar reporte html', 'render report', 'renderizar reporte', 'validar report-data.json', 'genesis report'."
argument-hint: 'Path to report-data.json'
---

# Genesis Report Renderer

Converts a `report-data.json` (the common report contract) into a single `report.html` file that opens with a double click, works offline and always looks the same for a given agent.

**The agent produces data; this skill produces the view.** The skill never queries Jira and never reinterprets results.

## Requirements

- Python 3.8+ (standard library only, nothing to install).
- Permission to run terminal commands.

## Input

Path to a `report-data.json` that follows the contract in [references/report-contract.md](references/report-contract.md).
Conventional path: `reports/<agent_id>/<scope-id>/report-data.json`.

## Procedure

0. **Run clock (agents only, first action of the run, before asking the user anything):**

   ```bash
   python "<skill-dir>/scripts/run_clock.py" start <agent_id>
   ```

   `build_report.py` uses it to report the real end-to-end time (agent start → HTML written) and overwrites the timing fields of `meta`. Never compute or estimate durations yourself.

1. Run the build script from the workspace root (use `python`, or `py -3` / `python3` if `python` is not found):

   ```bash
   python "<skill-dir>/scripts/build_report.py" "reports/<agent_id>/<scope-id>/report-data.json"
   ```

   `<skill-dir>` is the folder that contains this `SKILL.md` (e.g. `.agents/skills/genesis-report-renderer` or `.github/skills/genesis-report-renderer`).
2. Read the exit code:
   - `0` → the last line is `OK: <path to report.html> (skill build: ... | agent execution: <total> (end-to-end))`. Return that path and that total time. A `WARN ... no run clock` line means step 0 was skipped.
   - `1` → validation failed. Each `ERROR <json-path>: <message>` line points at the exact field. Fix **only the JSON** and run again (max 2 retries). `WARN` lines do not block the build.
   - `2` → file or environment problem (missing file, Python, assets). Report it to the user; the JSON is still valid evidence.
3. Optional: `--validate-only` checks the contract without writing HTML. `--out <path>` changes the output path. `--profile <path>` forces a profile.

## Rules

- Never write, edit or "improve" HTML by hand, and never modify `assets/` while rendering a report.
- Never change the values in the JSON to make it pass validation; fix structure only. If data is missing, say so.
- Do not paste the HTML or the full JSON into the chat. Reply with the path.
- Do not read `assets/` or the scripts' source to "understand" them: just run the commands (saves tokens).
- Do not generate a `.md` version of the report.

## Output

`report.html` next to the input JSON (unless `--out` is used). It embeds CSS, JS, icons and data; it makes no network calls.

## Adding a new agent (PA, RN, ...)

1. Emit `report-data.json` with a new `meta.agent_id` (kebab-case) using the section types in [references/components.md](references/components.md).
2. It renders immediately with the generic layout (sections in declared order).
3. To fix its layout, add `assets/profiles/<agent_id>.profile.json`. No change to the renderer is needed unless a new section type is required.

## References

- [references/report-contract.md](references/report-contract.md): JSON contract (envelope, statuses, actions, sources).
- [references/components.md](references/components.md): section types catalog and profile format.
- [references/branding.md](references/branding.md): intive | GENESIS tokens and visual rules.
