# Genesis Governance & Customizations

This workspace contains customizations for Genesis project audits and reporting:

- **Skills**:
  - `sprint-dod-validator` (`.claude/skills/sprint-dod-validator/SKILL.md`): Audits and validates the Definition of Done for a sprint in Jira.
  - `story-dod-validator` (`.claude/skills/story-dod-validator/SKILL.md`): Audits and validates the 21 criteria of the User Story Definition of Done in Jira (PRs, unity 2-approvers rule, Figma, Requirement traceability, Zephyr AC coverage).
  - `genesis-report-renderer` (`.claude/skills/genesis-report-renderer/SKILL.md`): Renders `report-data.json` files into self-contained intive | GENESIS HTML reports via Python (`scripts/build_report.py`).

- **Report rules**:
  - Always generate the `report-data.json` data structure under `reports/<agent_id>/<scope-id>/report-data.json`.
  - Never hand-write or hand-edit HTML files; always use the script `python .claude/skills/genesis-report-renderer/scripts/build_report.py <path-to-json>`.
  - First action of every audit agent run: `python .claude/skills/genesis-report-renderer/scripts/run_clock.py start <agent_id>` (end-to-end execution time is measured by the renderer, never estimated by the LLM).
  - Keep the 7 canonical criteria of the Sprint Definition of Done.
