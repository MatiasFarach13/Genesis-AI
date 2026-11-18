# Report contract (`schema_version` 1.0)

Single source of truth for one agent run. It carries **data only**, no presentation.
Unknown fields are ignored by the renderer, so agents can add fields without breaking anything.

## Required fields

| Field | Type | Notes |
|---|---|---|
| `schema_version` | string | `"1.0"` (major must be 1) |
| `meta.agent_id` | string | kebab-case. Selects the profile: `sprint-dod`, `project-audit`, `release-notes` |
| `meta.generated_at` | string | ISO 8601 with offset, e.g. `2026-10-08T10:27:00-03:00` |
| `summary.status` | string | `pass` \| `warning` \| `fail` |
| `sections` | array | May be empty. See [components.md](components.md) |

## Full envelope

```jsonc
{
  "schema_version": "1.0",
  "meta": {
    "agent_id": "sprint-dod",
    "agent_name": "Sprint DOD Validator",       // optional, shown in the header
    "team": "AI Governance",                    // optional, shown under the brand (default "AI Governance")
    "agent_version": "2.6",
    "run_id": "sprint-dod-183-20261008T1027",   // used as localStorage key for the checklist
    "started_at": "2026-10-08T10:26:35-03:00",  // optional; overwritten by the run clock (agent start)
    "generated_at": "2026-10-08T10:27:00-03:00",
    "execution_time": "19m 42s",                // optional; overwritten by the run clock (end-to-end)
    "execution_time_seconds": 1182.0,           // optional; overwritten by the run clock (end-to-end)
    "timing": { "source": "run-clock", "engine_seconds": 18.4 },  // written by build_report.py
    "language": "es",                            // es | en (UI labels)
    "scope": { "type": "sprint", "id": "183", "label": "Sprint 183", "board": "RSV1 (id 5)" },
    "jira_base_url": "https://<site>.atlassian.net/browse/",   // must be https; issue links = base + key
    "sources": [
      { "name": "Jira", "kind": "jira", "status": "ok" },
      { "name": "Bitbucket PRs", "kind": "bitbucket", "status": "unavailable", "reason": "MCP does not expose dev-info" }
    ]
  },
  "summary": {
    "status": "fail",
    "headline": "Sprint 183 no cumple la DoD",
    "score": null,                               // optional 0-100 (PA, RN)
    "kpis": [
      { "label": "Ítems aprobados", "value": 1, "total": 7 },
      { "label": "Acciones pendientes", "value": 9, "status": "fail" }
    ]
  },
  "sections": [],
  "notices": [],
  "actions": [],
  "limitations": []
}
```

## Notices (optional)

Informative banners shown right below the verdict. Use them for context the reader should know but **must not act on now** (e.g. a process definition still under discussion with the client). Never duplicate an action here.

```jsonc
"notices": [
  { "level": "info", "title": "Ubicación de los builds en definición",
    "text": "La DoD menciona Artifactory, pero hoy los builds se guardan en Jenkins. Lo estamos definiendo con los PO de Genesis; no requiere acción." }
]
```

`level`: `info` (default) | `warning`. Plain, user-friendly language.

## Execution time (run clock)

LLMs cannot measure wall-clock time, so the duration is never computed by the agent:

1. First action of every agent run (before asking anything): `python <skill-dir>/scripts/run_clock.py start <agent_id>` → writes `reports/<agent_id>/.run-clock.json`.
2. `build_report.py` reads that clock and overwrites `meta.started_at`, `meta.generated_at`, `meta.execution_time_seconds` and `meta.execution_time` (e.g. `"19m 42s"`) with the **end-to-end** time (agent start → HTML written), stores the previous value as `meta.timing.engine_seconds`, writes the same values back into `report-data.json` and deletes the clock.
3. Without a clock (e.g. re-rendering an old JSON) the values in the JSON are kept and a `WARN` is printed. Clocks older than 6 h are ignored.

## Status vocabulary

Every `status` field (criteria, items, sources excluded) uses one of:

| Status | Meaning | DoD | PA | RN review |
|---|---|---|---|---|
| `pass` | Meets the rule | ✅ | meets | Ready |
| `fail` | Does not meet the rule | ❌ | fails | Fail |
| `manual` | Needs human verification | ⚠️ Manual | manual | — |
| `info` | Informative only | — | informative | — |
| `gap` | Required data missing | — | missing data | Gap |
| `contradiction` | Two sources contradict | — | — | Contradiction |
| `discrepancy` | Values differ, not blocking | — | — | Discrepancy |
| `na` | Not applicable | — | n/a | MAY absent |

`summary.status` only uses `pass | warning | fail`.

### `manual` needs a `reason`

| `reason` | Use when |
|---|---|
| `by-design` | The control is manual by process (e.g. retro on a Confluence whiteboard) |
| `tool-unavailable` | It would be automatable but the integration/tool failed or does not exist yet |


### Criterion `warning` (optional)
`warning` (string or boolean): Adds an informative warning pill with a small warning icon (`⚠️`) alongside the criterion title, even when `status: "pass"`. Specifically used in Sprint DoD Criterion 4 when uncompleted tasks have been deferred / rolled over to a future sprint (> analyzed sprint) so the item passes in green (`pass`) without failing the sprint, while preserving visual visibility of the rollover.

## Sources

`meta.sources[].status`: `ok` \| `partial` \| `unavailable` \| `error`. Include a `reason` for anything other than `ok`.
`kind` (optional) picks the icon: `jira`, `bitbucket`, `jenkins`, `confluence`, `teams`, `file`, `other`.

## Actions (optional checklist)

```jsonc
"actions": [
  { "id": "a1", "group": "manual", "text": "Confirmar build en Jenkins etiquetada 183", "criterion": "2" },
  { "id": "a2", "group": "fix", "text": "Pasar a Closed o mover a backlog", "criterion": "3",
    "issues": ["RSV1-28406", "RSV1-28704"] }
]
```

- `id` unique within the report. `text` imperative, one line.
- `group`: free string; the profile can map it to a label (`manual` → "Verificación manual", `fix` → "Correcciones", `practice` → "Buenas prácticas (opcional)"). Use `practice` for optional, non-blocking suggestions (e.g. recommending a complementary quality check) — they still render as checkable items, but the agent must exclude them from any "pending actions" KPI and from what is required to reach DONE.
- **One action with N issues, never N actions with the same text.**
- `criterion` (optional) links the action to a criterion `id`.
- Empty array → the section is hidden.

## Limitations (optional)

```jsonc
"limitations": [
  { "text": "Los PRs vinculados no se pueden leer vía MCP", "criterion": "6", "reason": "tool-unavailable" }
]
```

## Size and privacy

- Include only Jira metadata (key, type, summary, status, resolution, assignee). Never full descriptions or comments.
- Keep `summary` texts short. The HTML embeds the whole JSON.
