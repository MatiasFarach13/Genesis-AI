# User Story DoD — 21 criteria (reference)

The evaluation rules live in `scripts/validate_story_dod.py` (`CRITERIA_SPECS` + `evaluate_single_story`). This table is documentation only: the agent does **not** need it to run an audit. Read it only if the user asks how a specific criterion is evaluated.

| # | DoD Item (Source) | Category | Automated Evaluation Rule |
|---|---|---|---|
| **1** | Links between Story and code changes established | Code & PRs | Passes if at least one Bitbucket PR is linked in Jira Development panel (`customfield_10000` or dev-status). Fails if 0 PRs are linked. |
| **2** | Code review evidence linked (Bitbucket PRs linked) | Code & PRs | Per team convention, an approved PR constitutes code review evidence. Passes if PRs have valid approvals. |
| **3** | Code-review observations Fixed or Won’t-fix with rationale | Code & PRs | Fulfilled when all merged PRs have been approved by reviewers (recording total PR comment counts). |
| **4** | Code pulled into master (PR merged) | Code & PRs | Passes if all linked PRs are `MERGED` and 0 PRs are `OPEN`. Fails if any PR remains open. |
| **5** | PR approved by 1 person (2 approvers for `unity` repo) | Code & PRs | Evaluates `reviewers[].approved == true` excluding the PR author. Requires $\ge 2$ approvers for `unity` repositories (`genesissoftwareinnovations/unity` or `[Unity]` scope) and $\ge 1$ for all other repos. |
| **6** | Green pipeline: unit tests + static analysis | Build & Pipeline | Marked `manual` (`reason: "by-design"`). Official builds are in Jenkins (Jira-Jenkins integration pending). |
| **7** | Figma linked (design) | UX & Design | Evaluates formal design fields, `customfield_10086`, remote links, or regex in description/comments. Passes if URL found or if explicitly marked `No Design Required` / backend component. |
| **8** | Implemented UI design verified against approved baseline | UX & Design | Passes automatically (`pass`) if a UX validation subtask (`Ux validation`, `[UXUI]`, `Figma`, or assigned to `Belen Yacoub`, excluding `[DOC]` tasks) is in `Resolved`, `Closed`, or `Done` with a valid resolution, or as N/A when no UI design is required. Otherwise marked `manual` (`reason: "by-design"`). |
| **9** | Acceptance criteria verified by system test case(s) | Traceability & AC | Extracts Acceptance Criteria items from Story description (Gherkin/bullet lists) and matches them against Zephyr Test Cases linked to the parent Requirement (excluding test cases scoped by Test Cycle name to a different Story). |
| **10** | Links kept: Story -> Req; Req -> TC | Traceability | Verifies mandatory Jira link from Story to parent `Req` (via `implements` or `parent`), and verifies Req is linked to Test Cases. |
| **11** | No Critical nor Major issues remain open (S4/S5) | Defects / Bugs | Inspects linked Bug issues. Fails if any linked bug is unresolved with priority `Critical`, `High`, `Highest`, `Blocker`, `S4`, or `S5`. |
| **12** | Test cases created/updated in Jira/Zephyr | Zephyr Testing | Queries Zephyr Scale (`/issuelinks/{key}/testcases`, fallback: cached project scan) for test cases linked to the Requirement/Story, disambiguating shared Requirements (`1 Req -> N Stories`) via Zephyr Test Cycle names (`RSV1-Rxxx: "<Story Key> - ..."`). |
| **13** | Test cases reviewed and approved | Zephyr Testing | Verifies associated Zephyr test cases have status `Approved` (not Draft or Review). When a Requirement is shared across stories and all Test Cases confirmed in this Story's Test Cycles are `Approved`, any extra unscoped Draft/Review test cases on the shared Requirement are flagged as `manual` (`reason: "by-design"`) for QA confirmation instead of a hard failure. |
| **14** | Test cases executed on pre-staging server | Zephyr Testing | Verifies test executions in Zephyr on pre-staging environments with `PASS` status. When all Test Cases confirmed in this Story's Test Cycles have `PASS` executions, any extra unscoped test cases on a shared Requirement without a cycle for this Story are flagged as `manual` (`reason: "by-design"`). |
| **15** | Evidence of test execution linked to Story | Zephyr Testing | Verifies execution records, Test Cycle name, tester, date, and results are registered in Zephyr (applying the same shared-Requirement Test Cycle disambiguation as #14). |
| **16** | 3rd-party software (SOUP) registered in 0GS-001 SCMP | Documentation | Inspects `[SOUP]` documentation subtasks; passes with informative notice if not applicable. |
| **17** | Product architecture document updated | Documentation | Inspects `[Architecture]` or `[Documentation]` subtasks; fails if open, passes if closed or N/A. |
| **18** | Product design document updated | Documentation | Inspects `[Design Doc]` subtasks or Google Drive links; fails if subtask open, passes if closed or N/A. |
| **19** | API documentation updated if APIs changed | Documentation | Inspects `[API Doc]` subtasks for backend/API stories; fails if open, passes if closed or N/A. |
| **20** | Documentation was updated if needed | Documentation | Validates overall technical documentation subtasks and Google Drive references. |
| **21** | Story Resolved and resolution = Done | Status & Closure | Verifies status is `Resolved` or `Closed` with `resolution = Done`. Inspects changelog to check if closed by authorized PO/Proxy (Matt Miller / Vanesa Savino). |
