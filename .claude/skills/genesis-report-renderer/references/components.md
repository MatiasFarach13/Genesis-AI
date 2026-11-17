# Section types and profiles

Every entry of `sections` has `id` (unique), `type`, optional `title`, and the fields below.
Unknown `type` → rendered by the generic fallback (table if it has `rows`/`items`, otherwise key-value). Data is never lost.

## Catalog

### `criteria`
Rules with status, a visible one-line result and a collapsible detail. This is the most valuable block: write it for a non-technical reader, complete but without filler.

```jsonc
{ "id": "dod", "type": "criteria", "title": "Evaluación de criterios",
  "items": [
    { "id": "3", "title": "Las historias aceptadas en la review están cerradas", "status": "fail",
      "note": "Solo 9 de 124 issues están en Closed; 61 quedaron en Resolved.",   // always visible, full sentence
      "description": "Toda historia aceptada en la sprint review debe quedar en Closed en Jira, cerrada por el PO o PO Proxy.",  // what the DoD asks
      "checks": ["Se revisó el estado de las 124 issues del sprint.", "Se revisó en el historial quién pasó cada issue a Closed."],  // what the agent verified
      "evidence": ["JQL: sprint = 1234 AND status != Closed"],   // optional, technical detail (monospace)
      "issues": ["RSV1-28406"],                                   // optional: "Ver issues afectadas" filters the issue table
      "warning": "56 tareas postergadas al Sprint 184",           // optional: warning pill with small icon (e.g. rollover to future sprint)
      "group": "Stories",                                         // optional: groups criteria (PA, RN)
      "reason": "tool-unavailable" }                              // required when status = manual
  ] }
```

### `issue-table`
Jira issues with search, status/type filters, pagination and CSV export. **Collapsed by default**; set `"collapsed": false` to start expanded. "Ver issues afectadas" expands it automatically.

```jsonc
{ "id": "issues", "type": "issue-table", "title": "Issues revisados",
  "jql": "sprint = 1234",                         // optional: "Copiar JQL" button
  "rows": [
    { "key": "RSV1-28406", "type": "Story", "summary": "…", "status": "Resolved",
      "status_category": "info",                   // pass | fail | manual | info | gap | na ... (badge color)
      "assignee": "Marta S.", "parent": null, "flags": ["closed-by-non-po"] }
  ] }
```

### `distribution`
Count per category, stacked bar + legend.

```jsonc
{ "id": "status-dist", "type": "distribution", "title": "Distribución de estados",
  "items": [ { "label": "Closed", "value": 9, "status": "pass" },
             { "label": "Resolved", "value": 61, "status": "info" } ] }
```

### `metric-gate`
Metric vs threshold (PA, RN).

```jsonc
{ "id": "gates", "type": "metric-gate",
  "items": [ { "label": "Pass rate", "value": 92.5, "unit": "%", "operator": ">=", "threshold": 90, "status": "pass" } ] }
```

### `key-value`
Run parameters.

```jsonc
{ "id": "params", "type": "key-value", "items": [ { "key": "Versión", "value": "2.174.0" } ] }
```

### `generic-table`
Any tabular data.

```jsonc
{ "id": "t1", "type": "generic-table", "columns": ["Req", "TC", "Estado"],
  "rows": [ ["REQ-1", "TC-9", "pass"] ] }
```

`rows` can also be an array of objects; columns are then taken from `columns` or from the first row keys.

### Planned (render with the generic fallback until implemented)

`coverage-matrix`, `deliverables`, `set-diff`, `doc-score`. Use the field names from the analysis document so the dedicated component can be added later without changing agents.

## Profiles

`assets/profiles/<agent_id>.profile.json`:

```jsonc
{
  "agent_id": "sprint-dod",
  "title": "Sprint DoD · {meta.scope.label}",  // {dotted.path} resolved against the report JSON
  "layout": {
    "main":  ["criteria", "issue-table"],       // section ids OR section types, in order
    "aside": ["distribution", "@actions", "@sources"]
  },
  "action_groups": { "manual": "Verificación manual", "fix": "Correcciones del agente" }
}
```

- `@actions`, `@sources` are the envelope blocks (`actions`, `meta.sources` + `limitations`).
- Sections not listed in the layout are appended to `main`, so nothing is hidden by mistake.
- No profile → generic layout: all sections in `main` in declared order, `@actions` and `@sources` in `aside`.
