/* genesis-report-renderer: draws report-data.json using the agent profile. DOM built with textContent only. */
(() => {
  'use strict';

  const readJson = (id) => {
    const node = document.getElementById(id);
    const text = node ? node.textContent.trim() : '';
    return text ? JSON.parse(text) : null;
  };

  const STATUSES = ['pass', 'fail', 'manual', 'info', 'gap', 'contradiction', 'discrepancy', 'na'];
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const PAGE_SIZE = 25;

  const I18N = {
    es: {
      status_pass: 'Cumple', status_fail: 'No cumple', status_manual: 'Verificación manual', status_info: 'Info',
      status_gap: 'Gap', status_contradiction: 'Contradicción', status_discrepancy: 'Discrepancia', status_na: 'N/A', execution_time: 'Tiempo de ejecución', footer_execution_time: 'Duración', warning_badge_default: 'Aviso',
      timer_e2e: 'Desde que arrancó el agente hasta que se generó el HTML', engine_time: 'motor',
      summary_pass: 'Aprobado', summary_warning: 'Pendiente', summary_fail: 'No cumple',
      team: 'AI Governance', notice_info: 'Aviso', notice_warning: 'Atención',
      dod_requirement: 'Qué pide la DoD', checks: 'Qué verificó el agente',
      show_issues: 'Mostrar issues', hide_issues: 'Ocultar',
      board: 'Tablero', run_id: 'ID de ejecución', score: 'Score',
      criteria_title: 'Evaluación de criterios', issues_title: 'Issues revisados',
      distribution_title: 'Distribución', gates_title: 'Umbrales', kv_title: 'Parámetros', table_title: 'Detalle',
      note: 'Nota', evidence: 'Evidencia', reason_by_design: 'Manual por diseño del proceso',
      reason_tool_unavailable: 'Manual porque la herramienta/integración no está disponible',
      affected_issues: 'Ver issues afectadas', search: 'Buscar KEY, resumen, resolución o asignado…',
      all_statuses: 'Todos los estados', all_types: 'Todos los tipos', all_resolutions: 'Todas las resoluciones', export_csv: 'CSV', copy_jql: 'Copiar JQL',
      copied: 'Copiado', showing: '{from}–{to} de {total}', prev: 'Anterior', next: 'Siguiente',
      no_results: 'Sin resultados', filtered_by: 'Filtrado por', clear_filter: 'Quitar filtro',
      col_key: 'KEY', col_type: 'Tipo', col_summary: 'Resumen', col_status: 'Estado', col_resolution: 'Resolución', col_assignee: 'Asignado',
      actions_title: 'Plan de acción', completed: '{p}% completado ({n}/{total})', issues_count: '{n} issues',
      criterion_ref: 'Criterio {id}', sources_title: 'Cobertura y fuentes', limitations: 'Limitaciones',
      source_ok: 'OK', source_partial: 'Parcial', source_unavailable: 'No disp.', source_error: 'Error',
      manual_note: '{n} control(es) quedaron como {label}; {m} por falta de integración de herramientas.',
      manual_word: 'Manuales', footer_agent: 'Agente', footer_generated: 'Generado', footer_schema: 'Contrato',
      footer_renderer: 'Renderer', unsupported_type: 'Tipo de sección "{type}" sin componente dedicado: vista genérica.',
      render_error: 'No se pudo renderizar el reporte'
    },
    en: {
      status_pass: 'Pass', status_fail: 'Fail', status_manual: 'Manual check', status_info: 'Info',
      status_gap: 'Gap', status_contradiction: 'Contradiction', status_discrepancy: 'Discrepancy', status_na: 'N/A', execution_time: 'Execution time', footer_execution_time: 'Duration', warning_badge_default: 'Notice',
      timer_e2e: 'From agent start until the HTML was generated', engine_time: 'engine',
      summary_pass: 'Passed', summary_warning: 'Pending', summary_fail: 'Failed',
      team: 'AI Governance', notice_info: 'Notice', notice_warning: 'Attention',
      dod_requirement: 'What the DoD requires', checks: 'What the agent checked',
      show_issues: 'Show issues', hide_issues: 'Hide',
      board: 'Board', run_id: 'Run ID', score: 'Score',
      criteria_title: 'Criteria evaluation', issues_title: 'Reviewed issues',
      distribution_title: 'Distribution', gates_title: 'Thresholds', kv_title: 'Parameters', table_title: 'Details',
      note: 'Note', evidence: 'Evidence', reason_by_design: 'Manual by process design',
      reason_tool_unavailable: 'Manual because the tool/integration is unavailable',
      affected_issues: 'Show affected issues', search: 'Search KEY, summary, resolution or assignee…',
      all_statuses: 'All statuses', all_types: 'All types', all_resolutions: 'All resolutions', export_csv: 'CSV', copy_jql: 'Copy JQL',
      copied: 'Copied', showing: '{from}–{to} of {total}', prev: 'Previous', next: 'Next',
      no_results: 'No results', filtered_by: 'Filtered by', clear_filter: 'Clear filter',
      col_key: 'KEY', col_type: 'Type', col_summary: 'Summary', col_status: 'Status', col_resolution: 'Resolution', col_assignee: 'Assignee',
      actions_title: 'Action plan', completed: '{p}% completed ({n}/{total})', issues_count: '{n} issues',
      criterion_ref: 'Criterion {id}', sources_title: 'Coverage and sources', limitations: 'Limitations',
      source_ok: 'OK', source_partial: 'Partial', source_unavailable: 'N/A', source_error: 'Error',
      manual_note: '{n} control(s) were left as {label}; {m} due to missing tool integrations.',
      manual_word: 'Manual', footer_agent: 'Agent', footer_generated: 'Generated', footer_schema: 'Contract',
      footer_renderer: 'Renderer', unsupported_type: 'Section type "{type}" has no dedicated component: generic view.',
      render_error: 'The report could not be rendered'
    }
  };

  const ICONS = {
    pass: 'M9 12l2 2 4-4M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
    fail: 'M15 9l-6 6M9 9l6 6M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
    manual: 'M12 9v4M12 17h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z',
    info: 'M12 16v-4M12 8h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
    gap: 'M8 12h8M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
    contradiction: 'M7 16l-4-4 4-4M17 8l4 4-4 4M3 12h18',
    discrepancy: 'M5 9h14M5 15h14M15 5L9 19',
    na: 'M5 12h14',
    chevron: 'M6 9l6 6 6-6',
    download: 'M12 3v12m0 0l-4-4m4 4l4-4M4 21h16',
    clock: 'M12 7v5l3 3M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
    agent: 'M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3z',
    search: 'M21 21l-4.35-4.35M17 11a6 6 0 11-12 0 6 6 0 0112 0z',
    external: 'M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 01-1 1H5a1 1 0 01-1-1V7a1 1 0 011-1h5',
    list: 'M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01',
    ticket: 'M3 7h18v3a2 2 0 000 4v3H3v-3a2 2 0 000-4z',
    chart: 'M4 20V10M10 20V4M16 20v-7M2 20h20',
    check: 'M9 11l3 3 8-8M20 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11',
    plug: 'M9 2v6M15 2v6M6 8h12v4a6 6 0 01-12 0zM12 18v4',
    shield: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z',
    copy: 'M9 9h11v11H9zM5 15V4h11',
    x: 'M6 6l12 12M6 18L18 6',
    gauge: 'M12 14l4-4M3.5 18a9 9 0 1117 0'
  };

  // ---------- helpers ----------
  const data = readJson('report-data');
  const profile = readJson('report-profile') || null;
  const meta = data.meta || {};
  const summary = data.summary || {};
  const lang = meta.language === 'en' ? 'en' : 'es';
  const locale = lang === 'en' ? 'en-US' : 'es-AR';
  const t = (key) => I18N[lang][key] || I18N.es[key] || key;
  const fmt = (str, vars) => str.replace(/\{(\w+)\}/g, (m, k) => (vars[k] != null ? String(vars[k]) : m));
  const normStatus = (s) => (STATUSES.includes(String(s || '').toLowerCase()) ? String(s).toLowerCase() : 'na');
  const fmtNum = (n) => (typeof n === 'number' ? n.toLocaleString(locale, { maximumFractionDigits: 2 }) : String(n ?? ''));
  const fmtDate = (s) => {
    const d = new Date(s);
    return Number.isNaN(d.getTime()) ? String(s || '') : d.toLocaleString(locale, { dateStyle: 'medium', timeStyle: 'short' });
  };
  const groupBy = (list, fn) => {
    const map = new Map();
    list.forEach((item) => {
      const k = fn(item);
      if (!map.has(k)) map.set(k, []);
      map.get(k).push(item);
    });
    return map;
  };
  const lookup = (obj, path) => path.split('.').reduce((o, k) => (o && typeof o === 'object' ? o[k] : undefined), obj);
  const initials = (name) => String(name).split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join('');
  const slug = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

  const append = (node, children) => {
    children.flat(Infinity).forEach((c) => {
      if (c == null || c === false || c === '') return;
      node.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
    });
    return node;
  };

  const el = (tag, props, ...children) => {
    const node = document.createElement(tag);
    Object.entries(props || {}).forEach(([k, v]) => {
      if (v == null || v === false) return;
      if (k === 'class') node.className = v;
      else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? '' : String(v));
    });
    return append(node, children);
  };

  const icon = (name, cls) => {
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '2');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('class', `icon ${cls || ''}`.trim());
    const path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', ICONS[name] || ICONS.na);
    svg.appendChild(path);
    return svg;
  };

  const badge = (status, text) => {
    const st = normStatus(status);
    return el('span', { class: `badge s-${st}` }, text || t(`status_${st}`));
  };

  const jiraBase = /^https:\/\//i.test(meta.jira_base_url || '') ? meta.jira_base_url : null;
  const issueLink = (key) => (jiraBase
    ? el('a', { class: 'issue-key', href: jiraBase + encodeURIComponent(key), target: '_blank', rel: 'noopener noreferrer' }, key, icon('external', 'icon-xs'))
    : el('span', { class: 'issue-key' }, key));

  const card = (title, iconName, body, headerExtra, cls) => el('section', { class: `card ${cls || ''}`.trim() },
    el('div', { class: 'card-header' }, el('h2', null, icon(iconName), title), headerExtra),
    body);

  const copyText = async (text, button) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch (e) {
      const area = el('textarea', { readonly: true, style: 'position:fixed;opacity:0' });
      area.value = text;
      document.body.appendChild(area);
      area.select();
      document.execCommand('copy');
      area.remove();
    }
    const label = button.lastChild;
    const prev = label.textContent;
    label.textContent = t('copied');
    setTimeout(() => { label.textContent = prev; }, 1500);
  };

  const csvCell = (v) => {
    let s = v == null ? '' : String(v);
    if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`; // neutralize spreadsheet formula injection
    return /[",\n;]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };

  const downloadCsv = (filename, header, rows) => {
    const content = [header, ...rows].map((r) => r.map(csvCell).join(',')).join('\r\n');
    const url = URL.createObjectURL(new Blob(['\ufeff' + content], { type: 'text/csv;charset=utf-8' }));
    const a = el('a', { href: url, download: filename });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  // ---------- state shared between components ----------
  const sections = (Array.isArray(data.sections) ? data.sections : []).filter((s) => s && typeof s === 'object');
  const hasIssueTable = sections.some((s) => s.type === 'issue-table');
  const issueTables = [];
  const printHooks = [];

  const focusIssues = (keys, label) => {
    const target = issueTables[0];
    if (target) target.focus(keys, label);
  };

  // ---------- header / verdict / footer ----------
  const timing = meta.timing && typeof meta.timing === 'object' ? meta.timing : {};
  const execTime = meta.execution_time || (meta.execution_time_seconds != null ? `${meta.execution_time_seconds}s` : null) || meta.duration;
  const engineNote = typeof timing.engine_seconds === 'number' ? ` (${t('engine_time')}: ${fmtNum(timing.engine_seconds)}s)` : '';
  const timerTitle = [
    timing.source === 'run-clock' ? t('timer_e2e') : t('execution_time'),
    meta.started_at ? `${fmtDate(meta.started_at)} → ${fmtDate(meta.generated_at)}` : null
  ].filter(Boolean).join('\n') + engineNote;

  const renderHeader = () => el('header', { class: 'topbar' },
    el('div', { class: 'topbar-inner' },
      el('div', { class: 'brand-block' },
        el('div', { class: 'brand', 'aria-label': 'intive GENESIS' },
          el('span', { class: 'brand-intive' }, 'intive'),
          el('span', { class: 'brand-sep', 'aria-hidden': 'true' }, '|'),
          el('span', { class: 'brand-genesis' }, 'GENESIS')),
        el('span', { class: 'brand-team' }, meta.team || t('team'))),
      el('div', { class: 'topbar-meta' },
        el('span', { class: 'meta-item' }, icon('agent'),
          `${meta.agent_name || meta.agent_id || ''}${meta.agent_version ? ` v${meta.agent_version}` : ''}`),
        el('span', { class: 'meta-item' }, icon('clock'), fmtDate(meta.generated_at)),
        execTime ? el('span', { class: 'meta-item meta-timer', title: timerTitle }, icon('gauge'), `${t('execution_time')}: ${execTime}`) : null)));

  const renderNotices = () => {
    const notices = (Array.isArray(data.notices) ? data.notices : []).filter((n) => n && n.text);
    if (!notices.length) return null;
    return el('div', { class: 'notices' }, notices.map((n) => {
      const lvl = n.level === 'warning' ? 'warning' : 'info';
      return el('div', { class: `notice notice-${lvl}`, role: 'note' },
        icon(lvl === 'warning' ? 'manual' : 'info', 'icon-lg'),
        el('div', null, el('strong', null, n.title || t(`notice_${lvl}`)), el('p', null, n.text)));
    }));
  };

  const kpi = (k) => el('div', { class: `kpi${k.status ? ` s-${normStatus(k.status)}` : ''}` },
    el('div', { class: 'kpi-value' }, fmtNum(k.value), k.total != null ? el('span', { class: 'kpi-total' }, `/${fmtNum(k.total)}`) : null),
    el('div', { class: 'kpi-label' }, k.label));

  const resolveTitle = () => {
    const tpl = profile && typeof profile.title === 'string' ? profile.title : '';
    return tpl.replace(/\{([\w.]+)\}/g, (m, p) => String(lookup(data, p) ?? '')).replace(/^[\s·-]+|[\s·-]+$/g, '');
  };

  const renderVerdict = () => {
    const st = ['pass', 'warning', 'fail'].includes(summary.status) ? summary.status : 'warning';
    const scope = meta.scope || {};
    const sub = [
      resolveTitle(),
      scope.board ? `${t('board')}: ${scope.board}` : null,
      meta.run_id ? `${t('run_id')}: ${meta.run_id}` : null
    ].filter(Boolean).join(' · ');
    const kpis = (summary.kpis || []).map(kpi);
    if (typeof summary.score === 'number') kpis.unshift(kpi({ label: t('score'), value: summary.score, total: 100 }));
    return el('section', { class: `card verdict v-${st}` },
      el('div', { class: 'verdict-main' },
        el('div', null,
          el('div', { class: 'verdict-title' },
            el('span', { class: 'badge-lg' }, t(`summary_${st}`)),
            el('h1', null, summary.headline || resolveTitle() || meta.agent_id)),
          sub ? el('p', { class: 'muted small' }, sub) : null),
        kpis.length ? el('div', { class: 'kpis' }, kpis) : null));
  };

  const renderFooter = () => el('footer', { class: 'footer' },
    el('span', null, `${t('run_id')}: ${meta.run_id || '—'}`),
    el('span', null, `${t('footer_agent')}: ${meta.agent_id || ''}${meta.agent_version ? ` v${meta.agent_version}` : ''}`),
    el('span', null, `${t('footer_generated')}: ${meta.generated_at || ''}`),
    execTime ? el('span', null, `${t('footer_execution_time')}: ${execTime}${engineNote}`) : null,
    el('span', null, `${t('footer_schema')}: ${data.schema_version || ''}`),
    el('span', null, `${t('footer_renderer')}: ${document.querySelector('meta[name="generator"]')?.content || ''}`));

  // ---------- sections ----------
  const criterionItem = (it) => {
    const st = normStatus(it.status);
    const evidence = Array.isArray(it.evidence) ? it.evidence : [];
    const checks = Array.isArray(it.checks) ? it.checks : [];
    const issues = Array.isArray(it.issues) ? it.issues : [];
    let issuesNode = null;
    if (issues.length && hasIssueTable) {
      issuesNode = el('button', { class: 'link-btn', type: 'button', onclick: () => focusIssues(issues, it.title || it.id) },
        icon('ticket'), `${t('affected_issues')} (${issues.length})`);
    } else if (issues.length) {
      issuesNode = el('div', { class: 'issue-list' }, issues.map(issueLink));
    }
    const warningBadge = it.warning
      ? el('span', { class: 'crit-warning-badge', title: typeof it.warning === 'string' ? it.warning : t('warning_badge_default') },
          icon('manual', 'icon-xs'),
          el('span', null, typeof it.warning === 'string' ? it.warning : t('warning_badge_default')))
      : null;

    return el('details', { class: `criterion s-${st}` },
      el('summary', null,
        el('span', { class: 'crit-icon' }, icon(st, 'icon-lg')),
        el('span', { class: 'crit-head' },
          el('span', { class: 'crit-title' },
            it.id != null ? `${it.id}. ` : '',
            it.title || '',
            warningBadge),
          it.note ? el('span', { class: 'crit-note' }, it.note) : null),
        el('span', { class: 'crit-status' }, t(`status_${st}`)),
        icon('chevron', 'chev')),
      el('div', { class: 'crit-body' },
        it.description ? el('p', null, el('strong', null, `${t('dod_requirement')}: `), it.description) : null,
        checks.length ? el('div', null, el('strong', null, `${t('checks')}:`),
          el('ul', { class: 'checks' }, checks.map((c) => el('li', null, String(c))))) : null,
        st === 'manual' && it.reason ? el('p', { class: 'muted small' }, t(`reason_${String(it.reason).replace(/-/g, '_')}`)) : null,
        evidence.length ? el('div', null, el('strong', null, `${t('evidence')}:`),
          el('ul', { class: 'evidence' }, evidence.map((e) => el('li', null, String(e))))) : null,
        issuesNode));
  };

  const renderCriteria = (sec) => {
    const items = (sec.items || []).filter((i) => i && typeof i === 'object');
    const body = el('div', { class: 'card-body' });
    groupBy(items, (i) => i.group || '').forEach((list, g) => {
      if (g) body.appendChild(el('h3', { class: 'group-title' }, g, el('span', { class: 'count' }, String(list.length))));
      list.forEach((it) => body.appendChild(criterionItem(it)));
    });
    const counts = groupBy(items, (i) => normStatus(i.status));
    const chips = el('div', { class: 'chips' },
      STATUSES.filter((s) => counts.has(s)).map((s) => badge(s, `${counts.get(s).length} ${t(`status_${s}`)}`)));
    return card(sec.title || t('criteria_title'), 'list', body, chips);
  };

  const select = (allLabel, options, onChange) => el('select', { class: 'select', 'aria-label': allLabel, onchange: (e) => onChange(e.target.value) },
    el('option', { value: '' }, allLabel), options.map((o) => el('option', { value: o }, o)));

  const issueRow = (r) => {
    const flags = Array.isArray(r.flags) ? r.flags.filter(Boolean) : [];
    const summaryTitle = [r.summary, ...flags].filter(Boolean).join(' · ');
    return el('tr', null,
      el('td', null, issueLink(r.key)),
      el('td', null, el('span', { class: 'type-cell' }, el('span', { class: 'type-dot', 'data-type': slug(r.type) }), r.type || '—')),
      el('td', { class: 'summary-cell', title: summaryTitle },
        r.summary ? el('div', { class: 'summary-text' }, r.summary) : el('span', { class: 'muted' }, '—'),
        flags.length ? el('div', { class: 'flag-list' }, flags.map((f) => el('span', { class: 'flag' }, String(f)))) : null),
      el('td', null, badge(r.status_category, r.status || t(`status_${normStatus(r.status_category)}`))),
      el('td', { class: 'resolution-cell' }, r.resolution
        ? el('span', { class: 'res-pill', 'data-res': slug(r.resolution) }, r.resolution)
        : el('span', { class: 'muted' }, '—')),
      el('td', null, r.assignee
        ? el('span', { class: 'assignee' }, el('span', { class: 'avatar' }, initials(r.assignee)), r.assignee)
        : el('span', { class: 'muted' }, '—')));
  };

  const renderIssueTable = (sec) => {
    const rows = (sec.rows || []).filter((r) => r && r.key);
    const uniqSorted = (key) => [...new Set(rows.map((r) => r[key]).filter(Boolean))].sort();
    const resolutions = uniqSorted('resolution');
    const state = { q: '', status: '', type: '', resolution: '', keys: null, keysLabel: '', page: 0, printAll: false };

    const tbody = el('tbody');
    const pager = el('div', { class: 'pager' });
    const chip = el('div', { class: 'filter-chip', hidden: true });
    const searchInput = el('input', {
      type: 'search', class: 'input', placeholder: t('search'), 'aria-label': t('search'),
      oninput: (e) => { state.q = e.target.value.trim().toLowerCase(); state.page = 0; update(); }
    });

    const filtered = () => rows.filter((r) =>
      (!state.q || [r.key, r.summary, r.resolution, r.assignee].some((v) => String(v || '').toLowerCase().includes(state.q)))
      && (!state.status || r.status === state.status)
      && (!state.type || r.type === state.type)
      && (!state.resolution || r.resolution === state.resolution)
      && (!state.keys || state.keys.has(r.key)));

    const update = () => {
      const list = filtered();
      const size = state.printAll ? Math.max(list.length, 1) : PAGE_SIZE;
      const pages = Math.max(1, Math.ceil(list.length / size));
      state.page = Math.min(state.page, pages - 1);
      const start = state.page * size;
      const slice = list.slice(start, start + size);
      tbody.replaceChildren(...(slice.length ? slice.map(issueRow)
        : [el('tr', null, el('td', { class: 'empty', colspan: 6 }, t('no_results')))]));
      chip.hidden = !state.keys;
      chip.replaceChildren(...(state.keys ? [
        `${t('filtered_by')}: ${state.keysLabel}`,
        el('button', { class: 'chip-x', type: 'button', 'aria-label': t('clear_filter'), onclick: () => { state.keys = null; state.page = 0; update(); } }, icon('x', 'icon-xs'))
      ] : []));
      pager.replaceChildren(
        el('span', { class: 'muted' }, fmt(t('showing'), { from: list.length ? start + 1 : 0, to: start + slice.length, total: list.length })),
        el('div', { class: 'pager-btns' },
          el('button', { class: 'btn btn-sm', type: 'button', disabled: state.page === 0, onclick: () => { state.page -= 1; update(); } }, t('prev')),
          el('button', { class: 'btn btn-sm', type: 'button', disabled: state.page >= pages - 1, onclick: () => { state.page += 1; update(); } }, t('next'))));
    };

    const tools = el('div', { class: 'tools' },
      el('label', { class: 'search' }, icon('search'), searchInput),
      select(t('all_statuses'), uniqSorted('status'), (v) => { state.status = v; state.page = 0; update(); }),
      select(t('all_types'), uniqSorted('type'), (v) => { state.type = v; state.page = 0; update(); }),
      resolutions.length ? select(t('all_resolutions'), resolutions, (v) => { state.resolution = v; state.page = 0; update(); }) : null,
      el('button', {
        class: 'btn btn-sm', type: 'button',
        onclick: () => downloadCsv(`${meta.run_id || 'report'}-${sec.id || 'issues'}.csv`,
          ['key', 'type', 'summary', 'status', 'resolution', 'assignee'],
          filtered().map((r) => [r.key, r.type, r.summary, r.status, r.resolution || '', r.assignee]))
      }, icon('download'), el('span', null, t('export_csv'))),
      sec.jql ? el('button', { class: 'btn btn-sm', type: 'button', onclick: (e) => copyText(sec.jql, e.currentTarget) }, icon('copy'), el('span', null, t('copy_jql'))) : null);

    const content = el('div', null, chip, el('div', { class: 'table-wrap' }, el('table', null,
      el('thead', null, el('tr', null, ['col_key', 'col_type', 'col_summary', 'col_status', 'col_resolution', 'col_assignee'].map((c) => el('th', null, t(c))))),
      tbody)), pager);
    const toggleLabel = el('span');
    const toggle = el('button', { class: 'btn btn-sm toggle', type: 'button', onclick: () => setCollapsed(!collapsed) }, icon('chevron', 'chev'), toggleLabel);
    let collapsed = sec.collapsed !== false;
    const setCollapsed = (value) => {
      collapsed = value;
      content.hidden = value;
      tools.hidden = value;
      toggle.setAttribute('aria-expanded', String(!value));
      toggle.classList.toggle('open', !value);
      toggleLabel.textContent = value ? t('show_issues') : t('hide_issues');
    };

    const node = card([sec.title || t('issues_title'), el('span', { class: 'count' }, String(rows.length))], 'ticket',
      content, el('div', { class: 'header-actions' }, tools, toggle), 'breakable');
    setCollapsed(collapsed);

    issueTables.push({
      focus: (keys, label) => {
        setCollapsed(false);
        state.keys = new Set(keys);
        state.keysLabel = label;
        state.q = '';
        searchInput.value = '';
        state.page = 0;
        update();
        node.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });
    printHooks.push((on) => { state.printAll = on; content.hidden = on ? false : collapsed; update(); });
    update();
    return node;
  };

  const renderDistribution = (sec) => {
    const items = (sec.items || []).filter((i) => i && typeof i.value === 'number' && i.value >= 0);
    const total = items.reduce((acc, i) => acc + i.value, 0);
    const colorOf = (i, idx) => (i.status ? `s-${normStatus(i.status)}` : `p${idx % 6}`);
    const bar = el('div', { class: 'stack-bar', role: 'img', 'aria-label': items.map((i) => `${i.label}: ${i.value}`).join(', ') });
    items.forEach((i, idx) => {
      if (!total || !i.value) return;
      const seg = el('div', { class: `seg ${colorOf(i, idx)}`, title: `${i.label}: ${i.value}` });
      seg.style.width = `${(i.value / total) * 100}%`;
      bar.appendChild(seg);
    });
    const legend = el('div', { class: 'legend' }, items.map((i, idx) => el('div', { class: 'legend-row' },
      el('span', { class: 'legend-label' }, el('span', { class: `swatch ${colorOf(i, idx)}` }), i.label),
      el('span', null, el('strong', null, fmtNum(i.value)),
        el('span', { class: 'pct' }, total ? `(${fmtNum(Math.round((i.value / total) * 1000) / 10)}%)` : '')))));
    return card(sec.title || t('distribution_title'), 'chart', el('div', { class: 'card-body' }, bar, legend));
  };

  const renderMetricGate = (sec) => card(sec.title || t('gates_title'), 'gauge', el('div', { class: 'card-body' },
    (sec.items || []).map((g) => el('div', { class: `gate s-${normStatus(g.status)}` },
      el('div', null,
        el('div', null, el('strong', null, g.label)),
        g.threshold != null ? el('div', { class: 'muted small' }, `${g.operator || '>='} ${fmtNum(g.threshold)}${g.unit || ''}`) : null),
      el('div', { class: 'tools' },
        el('span', { class: 'gate-value' }, `${fmtNum(g.value)}${g.unit || ''}`),
        badge(g.status))))));

  const renderKeyValue = (sec, items) => card(sec.title || t('kv_title'), 'list', el('div', { class: 'card-body' },
    el('dl', { class: 'kv' }, (items || sec.items || []).map((i) => [
      el('dt', null, String(i.key)),
      el('dd', null, typeof i.value === 'object' && i.value !== null ? JSON.stringify(i.value) : String(i.value ?? ''))
    ]))));

  const cellNode = (v) => {
    if (v == null) return el('span', { class: 'muted' }, '—');
    if (typeof v === 'string' && STATUSES.includes(v)) return badge(v);
    if (typeof v === 'object') return JSON.stringify(v);
    return String(v);
  };

  const renderGenericTable = (sec, rowsOverride, note) => {
    const rows = (rowsOverride || sec.rows || []).filter((r) => r != null);
    let columns = Array.isArray(sec.columns) ? sec.columns : null;
    if (!columns) {
      const keys = new Set();
      rows.forEach((r) => { if (r && typeof r === 'object' && !Array.isArray(r)) Object.keys(r).forEach((k) => keys.add(k)); });
      columns = [...keys];
    }
    const valuesOf = (r) => (Array.isArray(r) ? r : columns.map((c) => (r && typeof r === 'object' ? r[c] : r)));
    const tools = rows.length ? el('div', { class: 'tools' }, el('button', {
      class: 'btn btn-sm', type: 'button',
      onclick: () => downloadCsv(`${meta.run_id || 'report'}-${sec.id || 'table'}.csv`, columns,
        rows.map((r) => valuesOf(r).map((v) => (typeof v === 'object' && v !== null ? JSON.stringify(v) : v))))
    }, icon('download'), el('span', null, t('export_csv')))) : null;
    return card(sec.title || t('table_title'), 'list', [
      note ? el('p', { class: 'fallback-note', style: 'padding:10px 16px 0' }, note) : null,
      el('div', { class: 'table-wrap' }, el('table', null,
        columns.length ? el('thead', null, el('tr', null, columns.map((c) => el('th', null, c)))) : null,
        el('tbody', null, rows.length
          ? rows.map((r) => el('tr', null, valuesOf(r).map((v) => el('td', null, cellNode(v)))))
          : el('tr', null, el('td', { class: 'empty', colspan: Math.max(columns.length, 1) }, t('no_results'))))))
    ], tools, 'breakable');
  };

  const renderFallback = (sec) => {
    const note = fmt(t('unsupported_type'), { type: sec.type });
    const list = Array.isArray(sec.rows) ? sec.rows : Array.isArray(sec.items) ? sec.items : null;
    if (list) return renderGenericTable(sec, list, note);
    const items = Object.entries(sec).filter(([k]) => !['id', 'type', 'title'].includes(k)).map(([key, value]) => ({ key, value }));
    const node = renderKeyValue(sec, items);
    node.querySelector('.card-body').prepend(el('p', { class: 'fallback-note' }, note));
    return node;
  };

  const RENDERERS = {
    criteria: renderCriteria,
    'issue-table': renderIssueTable,
    distribution: renderDistribution,
    'metric-gate': renderMetricGate,
    'key-value': (sec) => renderKeyValue(sec),
    'generic-table': (sec) => renderGenericTable(sec)
  };

  const renderSection = (sec) => {
    const fn = RENDERERS[sec.type] || renderFallback;
    const node = fn(sec);
    if (sec.id) node.id = `section-${slug(sec.id)}`;
    return node;
  };

  // ---------- envelope blocks ----------
  const loadDone = (key) => {
    try { return JSON.parse(localStorage.getItem(key) || '[]'); } catch (e) { return []; }
  };
  const saveDone = (key, ids) => {
    try { localStorage.setItem(key, JSON.stringify(ids)); } catch (e) { /* storage unavailable (e.g. file:// restrictions) */ }
  };

  const renderActions = () => {
    const actions = (Array.isArray(data.actions) ? data.actions : []).filter((a) => a && a.text);
    if (!actions.length) return null;
    const storeKey = `genesis-report:${meta.run_id || `${meta.agent_id}:${meta.generated_at}`}`;
    const done = new Set(loadDone(storeKey));
    const rawLabels = (profile && profile.action_groups) || {};
    const labels = (rawLabels[lang] && typeof rawLabels[lang] === 'object') ? rawLabels[lang] : rawLabels;
    const pct = el('span', { class: 'progress-text' });
    const fill = el('div', { class: 'progress-fill' });
    const refresh = () => {
      const n = actions.filter((a) => done.has(a.id)).length;
      const p = Math.round((n / actions.length) * 100);
      fill.style.width = `${p}%`;
      fill.classList.toggle('complete', p === 100);
      pct.textContent = fmt(t('completed'), { p, n, total: actions.length });
      saveDone(storeKey, [...done]);
    };

    const body = el('div', { class: 'card-body' });
    groupBy(actions, (a) => a.group || '').forEach((list, g) => {
      if (g) body.appendChild(el('h3', { class: 'group-title' }, labels[g] || g, el('span', { class: 'count' }, String(list.length))));
      list.forEach((a) => {
        const cb = el('input', { type: 'checkbox', class: 'check' });
        cb.checked = done.has(a.id);
        const item = el('div', { class: `action${cb.checked ? ' done' : ''}` });
        cb.addEventListener('change', () => {
          if (cb.checked) done.add(a.id); else done.delete(a.id);
          item.classList.toggle('done', cb.checked);
          refresh();
        });
        const issues = Array.isArray(a.issues) ? a.issues : [];
        append(item, [
          el('label', { class: 'action-label' }, cb, el('span', { class: 'action-text' }, a.text)),
          a.criterion != null ? el('div', { class: 'action-meta' }, fmt(t('criterion_ref'), { id: a.criterion })) : null,
          issues.length ? el('details', { class: 'action-issues' },
            el('summary', null, fmt(t('issues_count'), { n: issues.length })),
            el('div', { class: 'issue-list' }, issues.map(issueLink))) : null
        ]);
        body.appendChild(item);
      });
    });
    refresh();
    return el('section', { class: 'card breakable', id: 'section-actions' },
      el('div', { class: 'card-header col' },
        el('div', { class: 'row' }, el('h2', null, icon('check'), t('actions_title')), pct),
        el('div', { class: 'progress' }, fill)),
      body);
  };

  const renderSources = () => {
    const sources = Array.isArray(meta.sources) ? meta.sources : [];
    const limitations = (Array.isArray(data.limitations) ? data.limitations : []).filter((l) => l && l.text);
    if (!sources.length && !limitations.length) return null;
    const manual = sections.filter((s) => s.type === 'criteria')
      .flatMap((s) => s.items || []).filter((i) => i && normStatus(i.status) === 'manual');
    const toolManual = manual.filter((i) => i.reason === 'tool-unavailable').length;
    const body = el('div', { class: 'card-body' },
      sources.length ? el('ul', { class: 'sources' }, sources.map((s) => {
        const st = ['ok', 'partial', 'unavailable', 'error'].includes(s.status) ? s.status : 'unavailable';
        return el('li', { class: st === 'ok' ? '' : 'off' },
          el('div', { class: 'source' },
            el('span', { class: 'source-name' }, icon(s.kind === 'jira' ? 'ticket' : 'plug'), s.name),
            el('span', { class: `pill ${st}` }, t(`source_${st}`))),
          s.reason ? el('div', { class: 'source-reason' }, s.reason) : null);
      })) : null,
      limitations.length ? el('ul', { class: 'limitations' }, limitations.map((l) => el('li', null, l.text))) : null,
      manual.length ? el('p', { class: 'note' },
        ...fmt(t('manual_note'), { n: manual.length, m: toolManual, label: '\u0000' }).split('\u0000')
          .flatMap((part, i) => (i ? [el('strong', null, t('manual_word')), part] : [part]))) : null);
    return card(t('sources_title'), 'shield', body, null, 'sources-card');
  };

  // ---------- layout ----------
  const render = () => {
    const used = new Set();
    const placed = new Set();
    const pick = (entry) => {
      if (entry === '@actions' || entry === '@sources') {
        placed.add(entry);
        return [entry === '@actions' ? renderActions() : renderSources()];
      }
      return sections.filter((s) => !used.has(s) && (s.id === entry || s.type === entry)).map((s) => {
        used.add(s);
        return renderSection(s);
      });
    };
    const layout = (profile && profile.layout) || {};
    const main = (Array.isArray(layout.main) ? layout.main : []).flatMap(pick);
    const aside = (Array.isArray(layout.aside) ? layout.aside : []).flatMap(pick);
    sections.filter((s) => !used.has(s)).forEach((s) => { used.add(s); main.push(renderSection(s)); });
    ['@actions', '@sources'].filter((e) => !placed.has(e)).forEach((e) => aside.push(...pick(e)));

    const mainNodes = main.filter(Boolean);
    const asideNodes = aside.filter(Boolean);
    const grid = el('div', { class: `grid${asideNodes.length && mainNodes.length ? '' : ' single'}` },
      mainNodes.length ? el('div', { class: 'stack' }, mainNodes) : null,
      asideNodes.length ? el('aside', { class: 'stack' }, asideNodes) : null);

    const title = resolveTitle() || summary.headline;
    if (title) document.title = `${title} | intive GENESIS`;

    document.getElementById('app').replaceChildren(
      renderHeader(),
      el('main', null, el('div', { class: 'container' }, renderVerdict(), renderNotices(), grid, renderFooter())));
  };

  // ---------- print ----------
  let reopen = [];
  window.addEventListener('beforeprint', () => {
    reopen = [...document.querySelectorAll('details:not([open])')];
    reopen.forEach((d) => { d.open = true; });
    printHooks.forEach((h) => h(true));
  });
  window.addEventListener('afterprint', () => {
    reopen.forEach((d) => { d.open = false; });
    printHooks.forEach((h) => h(false));
  });

  try {
    render();
  } catch (err) {
    document.getElementById('app').replaceChildren(
      el('div', { class: 'render-error' }, el('strong', null, `${t('render_error')}: `), String(err && err.message ? err.message : err)));
  }
})();
