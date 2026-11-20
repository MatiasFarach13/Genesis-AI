---
name: story-dod-validator
description: >-
  Audits and validates the User Story Definition of Done (DoD) for Genesis Jira stories.
  Use when the user asks to check, audit, or validate the User Story Definition of Done, e.g. 'check story DoD', 'audit user story RSV1-29343', 'validate story DOD', 'audit sprint stories', 'story dod validator'.
  Evaluates 21 DoD criteria: Pull Requests (Bitbucket merges, non-author approvers, unity 2-approver rule), Figma design links or exceptions, Requirement traceability (Story -> Req -> Test Cases), Zephyr test cases & Acceptance Criteria coverage, and documentation tasks.
  A deterministic Python engine does all the work (Direct REST, parallel calls; MCP only as fallback), so it runs fast and cheap even on FAST LLMs. Renders offline HTML reports via genesis-report-renderer.
---

# Role
Act as the **Genesis User Story DoD Validator**. You audit Jira User Stories against the **21-criteria Genesis User Story DoD**.
A Python engine does all the heavy work (Jira + Bitbucket PRs via dev-status + Zephyr + evaluation + HTML). Your job is only orchestration: **start the clock → ask once → run one command → reply briefly**.

# Speed & token rules (mandatory)
- Never read `validate_story_dod.py`, the renderer scripts or its `assets/`. Never open `report-data.json` or `report.html` after the run: everything you need is in the `SUMMARY {...}` line the engine prints last.
- Never evaluate criteria yourself and never hand-write `report-data.json` or HTML.
- Never estimate or compute durations: the run clock measures them.
- At most one question round. Do not ask which execution track to use: it is chosen automatically (Step 3).
- Do not paste raw command output into the chat.

---

# Step 0 — Start the run clock (FIRST action, before anything else)

```bash
python .claude/skills/genesis-report-renderer/scripts/run_clock.py start story-dod
```

The reported execution time is end-to-end: from this command until the HTML is written.

# Step 1 — Ask once

Read `.claude/memories/repo/story-dod.json` (tiny) for defaults: `lastAuditedStory`, `lastAuditedSprint`, `language`.
Ask in a single round with the ask-questions tool (skip what the user already said):
1. **Target** — options: last audited story and last audited sprint (if present); freeform: a story key (`RSV1-29343`) or a sprint number (`186`).
2. **Language** — `Español` / `English`; recommended = memory `language` (default `es`).

# Step 2 — Run the engine (Track 2: Direct REST, default)

The engine automatically discovers credentials (`JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `ZEPHYR_API_TOKEN`) in this order:
1. CLI flags (`--jira-url`, `--jira-email`, `--jira-token`, `--zephyr-token`)
2. Environment variables
3. Local `.env.local` or `.env` file in the project root (template in `.env.example`, ignored by git)
4. Repo memory (`.claude/memories/repo/story-dod.json`)
5. IDE MCP configuration files (`~/.cursor/mcp.json`, `<workspace>/.cursor/mcp.json`, `<workspace>/.mcp.json`, `~/.claude.json`, `<workspace>/.vscode/mcp.json`, `~/.gemini/config/mcp_config.json`) across all configured servers (e.g., `jira`, `smartbear`)

From the workspace root:

```bash
# single story
python .claude/skills/story-dod-validator/scripts/validate_story_dod.py --story <KEY> --lang <es|en>
# all stories of a sprint
python .claude/skills/story-dod-validator/scripts/validate_story_dod.py --sprint <N> --lang <es|en>
```

The engine queries Jira REST v3, dev-status (Bitbucket PRs) and Zephyr Scale in parallel, evaluates the 21 criteria, writes `reports/story-dod/<KEY | sprint-N>/report-data.json`, renders `<KEY>-report.html` (or `sprint-<N>-report.html`), updates the repo memory and prints as its last line:

`SUMMARY {"verdict", "headline", "counts", "fail", "manual", "actions", "execution_time", "timing_source", "html", "json"}`

Exit codes: `0` OK → Step 4 · `2` missing Jira credentials → Step 3 · `1` → tell the user the one-line error and stop.

# Step 3 — Fallback Track 1 (Multi-MCP), only if Step 2 exited with code 2

Single stories only. For a sprint, ask the user to configure `.env` from `.env.example` with `JIRA_EMAIL`, `JIRA_API_TOKEN`, and `ZEPHYR_API_TOKEN` (via MCP a full sprint batch would be slow and expensive).

1. **Fetch the issue with whichever Jira MCP server is connected** (try in priority order and save the JSON response to `reports/story-dod/<KEY>/issue.json`):
   - **Option A — `mcp-atlassian` (`jira_get_issue`)**:
     - Call `jira_get_issue(issue_key="<KEY>", fields="summary,status,resolution,assignee,issuetype,labels,description,comment,issuelinks,subtasks,parent,customfield_10000,customfield_10086", include="remote_links,changelog,comments")` → save to `reports/story-dod/<KEY>/issue.json`.
     - Also call `jira_get_issue_development_info(issue_key="<KEY>")` → save to `reports/story-dod/<KEY>/dev-info.json`.
   - **Option B — Atlassian Remote MCP `v2` (`https://mcp.atlassian.com/v2/mcp`) or `v1` (`https://mcp.atlassian.com/v1/mcp/authv2`, e.g., `jiramcp` in Cursor)**:
     - Call `getJiraIssue(issueIdOrKey="<KEY>")` (requesting `summary,status,resolution,assignee,issuetype,labels,description,comment,issuelinks,subtasks,parent,customfield_10000,customfield_10086` and `expand="changelog"` if supported) → save to `reports/story-dod/<KEY>/issue.json`.
     - If Bitbucket tools are available in `v2` (`bitbucketPullRequest` / `search`), fetch the PRs for `<KEY>` and save to `reports/story-dod/<KEY>/dev-info.json`.
   - **Option C — `jira-nexus` (`jira_issues`)**:
     - Call `jira_issues(action="get", issue_key="<KEY>", expand="changelog")` → save to `reports/story-dod/<KEY>/issue.json`.
2. **Run the engine on the saved JSON** (`normalize_issue_payload` automatically normalizes flat `mcp-atlassian`, Atlassian Remote `v1`/`v2`, and REST v3 payloads):
   ```bash
   # With dev-info.json (when fetched via mcp-atlassian or Bitbucket v2 tool):
   python .claude/skills/story-dod-validator/scripts/validate_story_dod.py --issue-json reports/story-dod/<KEY>/issue.json --dev-info-json reports/story-dod/<KEY>/dev-info.json --lang <es|en>
   # Without dev-info.json (e.g., Atlassian Remote MCP v1):
   python .claude/skills/story-dod-validator/scripts/validate_story_dod.py --issue-json reports/story-dod/<KEY>/issue.json --lang <es|en>
   ```
   When `--dev-info-json` is omitted, PR counts and merge status come from `customfield_10000` (`Development`) and reviewer criteria (#2, #3, #5) are marked for manual review (`tool-unavailable`). Zephyr Scale still runs automatically if `ZEPHYR_API_TOKEN` is in `.env` or `~/.cursor/mcp.json` (`smartbear`).

# Step 4 — Reply (short, in the report language, only from the SUMMARY line)

1. Verdict: 🟢 **DONE** (`pass`) · 🟡 **PENDIENTE DE VERIFICACIÓN** (`warning`) · 🔴 **BRECHAS DE DoD** (`fail`) + `headline`.
2. `<pass>/21 cumplen · <fail> fallan · <manual> manuales` (for sprints the counts are per aggregated criterion).
3. Failing criteria, one line each (max 6), then the top pending actions (max 5).
4. `⏱️ Tiempo total: <execution_time>` (end-to-end). If `timing_source` is `engine-only`, add that Step 0 was skipped and the time only covers the engine.
5. Links with workspace-relative paths: `[<target>-report.html](reports/story-dod/<target>/<target>-report.html)` · `[report-data.json](reports/story-dod/<target>/report-data.json)`.
6. **Onboarding tip when running in fallback mode**: If Step 3 (MCP fallback) was used or if PR reviewer details / Zephyr were unavailable (`tool-unavailable`), add a brief tip at the end of your reply showing the user how to enable 100% automatic Direct REST mode by copying `.env.example` to `.env` in the project root (`JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `ZEPHYR_API_TOKEN`) — no need to pass CLI token flags or export variables in `~/.zshrc`.

---

# Reference & constraints
- How each criterion is evaluated (read only if the user asks): `.claude/skills/story-dod-validator/references/criteria.md`.
- Read-only on Jira and Zephyr. The only commands are `run_clock.py`, `validate_story_dod.py` and, to re-render an existing JSON, `build_report.py`.
- The same skill exists under `.claude/`, `.cursor/` and `.github/`; the `.claude/skills/...` paths work from any of them in this workspace.
