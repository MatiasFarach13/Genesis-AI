#!/usr/bin/env python3
"""Genesis User Story Definition of Done (DoD) Validator Engine.

Queries Jira Cloud REST API v3, Jira internal dev-status API (Bitbucket PRs,
merges, reviewers, repositories), and Zephyr Scale Cloud REST API v2 directly
to evaluate the 21 User Story DoD criteria without consuming LLM tokens on raw
API payloads.

Supports Dual Track:
  - Track 1: Offline / MCP Bridge Mode (--issue-json <path>)
  - Track 2: Direct REST Gateway Mode (--story <key> / --sprint <id>)

Supports Bilingual Report Generation:
  - English (--lang en)
  - Spanish (--lang es)

Standard library only (Python 3.8+).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

AGENT_ID = "story-dod"
AGENT_NAME = "User Story DoD Validator"
AGENT_VERSION = "1.3.0"

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
WORKSPACE_ROOT = SKILL_DIR.parent.parent.parent
RENDERER_SCRIPT = next(
    (p for p in (
        SKILL_DIR.parent / "genesis-report-renderer" / "scripts" / "build_report.py",
        WORKSPACE_ROOT / ".agents" / "skills" / "genesis-report-renderer" / "scripts" / "build_report.py",
    ) if p.is_file()),
    WORKSPACE_ROOT / ".agents" / "skills" / "genesis-report-renderer" / "scripts" / "build_report.py",
)
MEMORIES_DIR = next(
    (p for p in (
        SKILL_DIR.parent.parent / "memories" / "repo",
        WORKSPACE_ROOT / ".claude" / "memories" / "repo",
        WORKSPACE_ROOT / ".cursor" / "memories" / "repo",
        WORKSPACE_ROOT / ".agents" / "memories" / "repo",
    ) if p.is_dir()),
    SKILL_DIR.parent.parent / "memories" / "repo",
)
HTTP_WORKERS = 8
STORY_WORKERS = 4
RETRY_STATUSES = (429, 502, 503, 504)
# Fields the evaluator reads; also the minimal set to request from a Jira MCP tool in Track 1.
SEARCH_FIELDS = [
    "summary", "status", "resolution", "assignee", "issuetype", "labels", "description", "comment",
    "issuelinks", "subtasks", "parent", "customfield_10000", "customfield_10086",
]

FIGMA_URL_RE = re.compile(r"https?://(?:www\.)?figma\.com/(?:file|design|proto|board)/[^\s\"'<>)\]]+", re.IGNORECASE)
DRIVE_URL_RE = re.compile(r"https?://(?:docs|drive)\.google\.com/[^\s\"'<>)\]]+", re.IGNORECASE)
NO_FIGMA_RE = re.compile(
    r"\b(?:no\s+figma\s+required|no\s+design\s+required|figma\s*:\s*n/?a|design\s*:\s*n/?a|no\s+ui\s+changes?|backend\s+only)\b",
    re.IGNORECASE,
)
NON_UI_PREFIX_RE = re.compile(r"^\s*\[(?:BE|Backend|SC|SG|API|Cloud|Infra|DB|CLI)\]", re.IGNORECASE)

PO_NAMES = ["matt miller", "vanesa ines savino", "vanesa savino"]

CRITERIA_SPECS: Dict[str, Dict[str, Dict[str, str]]] = {
    "1": {
        "en": {
            "group": "Code & Pull Requests",
            "title": "Links between Story and code changes established (Bitbucket PR)",
            "description": "At least one Bitbucket Pull Request must be linked to the User Story in the Jira Development panel.",
        },
        "es": {
            "group": "Código y Pull Requests",
            "title": "Vínculos entre Story y cambios de código establecidos (Bitbucket PR)",
            "description": "Al menos un Pull Request de Bitbucket debe estar vinculado a la User Story en el panel de desarrollo de Jira.",
        },
    },
    "2": {
        "en": {
            "group": "Code & Pull Requests",
            "title": "Code review evidence linked (Bitbucket PRs linked)",
            "description": "Code review evidence is established through linked and approved Bitbucket Pull Requests (Approved PR = Code Review completed).",
        },
        "es": {
            "group": "Código y Pull Requests",
            "title": "Evidencia de code review vinculada (Bitbucket PRs vinculados)",
            "description": "La evidencia de code review se establece a través de los Pull Requests vinculados y aprobados (PR aprobado = Code Review completado).",
        },
    },
    "3": {
        "en": {
            "group": "Code & Pull Requests",
            "title": "Code-review observations Fixed or Won’t-fix with rationale",
            "description": "Review observations in Bitbucket PRs must be resolved. Per agreement, an approved and merged PR satisfies this check.",
        },
        "es": {
            "group": "Código y Pull Requests",
            "title": "Observaciones del code review resueltas o justificadas",
            "description": "Las observaciones de revisión en los PRs de Bitbucket deben estar resueltas. Por convención, un PR aprobado y mergeado cumple este criterio.",
        },
    },
    "4": {
        "en": {
            "group": "Code & Pull Requests",
            "title": "Code pulled into master (PR merged)",
            "description": "All linked Pull Requests must be merged into the target branch (no open PRs remaining).",
        },
        "es": {
            "group": "Código y Pull Requests",
            "title": "Código incorporado a la rama principal (PR mergeado)",
            "description": "Todos los Pull Requests vinculados deben estar mergeados en la rama de destino (sin PRs abiertos pendientes).",
        },
    },
    "5": {
        "en": {
            "group": "Code & Pull Requests",
            "title": "PR approved by required reviewers (1 general / 2 for unity repo)",
            "description": "Pull requests require at least 1 approver different from the author, or 2 different approvers for the unity repository.",
        },
        "es": {
            "group": "Código y Pull Requests",
            "title": "PR aprobado por los revisores requeridos (1 general / 2 para repo unity)",
            "description": "Los Pull Requests requieren al menos 1 aprobador diferente del autor, o 2 aprobadores diferentes para el repositorio unity.",
        },
    },
    "6": {
        "en": {
            "group": "Code & Pull Requests",
            "title": "Green pipeline: unit tests + static analysis (official Jenkins build)",
            "description": "The official release candidate build in Jenkins (including unit tests and static analysis) must pass.",
        },
        "es": {
            "group": "Código y Pull Requests",
            "title": "Pipeline en verde: pruebas unitarias + análisis estático (build oficial en Jenkins)",
            "description": "El build oficial en Jenkins (incluyendo pruebas unitarias y análisis estático) debe haber pasado con éxito.",
        },
    },
    "7": {
        "en": {
            "group": "UX & Design",
            "title": "Figma linked (design)",
            "description": "Stories requiring UI/UX changes must link the corresponding Figma design URL, or explicitly declare 'No Design Required'.",
        },
        "es": {
            "group": "UX y Diseño",
            "title": "Figma vinculado (diseño)",
            "description": "Las historias que requieran cambios de UI/UX deben vincular la URL del diseño en Figma o declarar explícitamente 'No Design Required'.",
        },
    },
    "8": {
        "en": {
            "group": "UX & Design",
            "title": "Implemented UI design verified against latest approved baseline",
            "description": "When UI changes apply, the implementation must be verified against the latest approved Figma baseline.",
        },
        "es": {
            "group": "UX y Diseño",
            "title": "Diseño de UI implementado verificado contra la última línea base aprobada",
            "description": "Cuando aplican cambios de interfaz, la implementación debe verificarse contra la última línea base aprobada en Figma.",
        },
    },
    "9": {
        "en": {
            "group": "Traceability & Requirements",
            "title": "Acceptance criteria in Story description verified by system test case(s)",
            "description": "Acceptance Criteria (AC) must be defined in the Story description and covered by at least one test case via the linked Requirement.",
        },
        "es": {
            "group": "Trazabilidad y Requerimientos",
            "title": "Criterios de aceptación en la descripción de la Story verificados por caso(s) de prueba",
            "description": "Los Criterios de Aceptación (AC) deben estar definidos en la descripción de la Story y cubiertos por al menos un caso de prueba mediante el Requerimiento vinculado.",
        },
    },
    "10": {
        "en": {
            "group": "Traceability & Requirements",
            "title": "Traceability links kept: Story -> Requirement -> Test Cases",
            "description": "Mandatory traceability in Genesis requires Story -> Requirement (Req) and Requirement -> Test Cases.",
        },
        "es": {
            "group": "Trazabilidad y Requerimientos",
            "title": "Enlaces de trazabilidad mantenidos: Story -> Requerimiento -> Casos de Prueba",
            "description": "La trazabilidad obligatoria en Genesis exige Story -> Requerimiento (Req) y Requerimiento -> Casos de Prueba.",
        },
    },
    "11": {
        "en": {
            "group": "Traceability & Requirements",
            "title": "No Critical or High (S5/S4) bugs related to the Story remain open",
            "description": "No Critical or High severity defects (S5/S4 in SDP) linked to the Story may remain open.",
        },
        "es": {
            "group": "Trazabilidad y Requerimientos",
            "title": "Sin bugs Críticos ni Mayores (S5/S4) relacionados a la Story abiertos",
            "description": "No deben quedar defectos abiertos de severidad Crítica o Alta (S5/S4 en el SDP) vinculados a la Story.",
        },
    },
    "12": {
        "en": {
            "group": "Testing & Zephyr",
            "title": "Test cases created/updated in Jira/Zephyr",
            "description": "System test cases must be created or updated in Zephyr and linked to the Requirement.",
        },
        "es": {
            "group": "Testing y Zephyr",
            "title": "Casos de prueba creados/actualizados en Jira/Zephyr",
            "description": "Los casos de prueba del sistema deben estar creados o actualizados en Zephyr y vinculados al Requerimiento.",
        },
    },
    "13": {
        "en": {
            "group": "Testing & Zephyr",
            "title": "Test cases reviewed and approved",
            "description": "All associated Zephyr test cases must be in 'Approved' status (not Draft or Review).",
        },
        "es": {
            "group": "Testing y Zephyr",
            "title": "Casos de prueba revisados y aprobados",
            "description": "Todos los casos de prueba asociados en Zephyr deben estar en estado 'Approved' (no Draft o Review).",
        },
    },
    "14": {
        "en": {
            "group": "Testing & Zephyr",
            "title": "Test cases executed on the pre-staging server",
            "description": "Associated test cases must have a passing test execution recorded in Zephyr.",
        },
        "es": {
            "group": "Testing y Zephyr",
            "title": "Casos de prueba ejecutados en el servidor de pre-staging",
            "description": "Los casos de prueba asociados deben tener una ejecución aprobada (PASS) registrada en Zephyr.",
        },
    },
    "15": {
        "en": {
            "group": "Testing & Zephyr",
            "title": "Evidence of test execution linked to the Story",
            "description": "Test execution records (status, date, environment) must be linked via Zephyr.",
        },
        "es": {
            "group": "Testing y Zephyr",
            "title": "Evidencia de ejecución de pruebas vinculada a la Story",
            "description": "Los registros de ejecución de pruebas (estado, fecha, entorno) deben estar vinculados mediante Zephyr.",
        },
    },
    "16": {
        "en": {
            "group": "Technical Documentation",
            "title": "When applicable: new 3rd-party software (SOUP) registered in 0GS-001 SCMP",
            "description": "If applicable to the story changes, 0GS-001 Software Configuration Management Plan must be updated before closing the story.",
        },
        "es": {
            "group": "Documentación Técnica",
            "title": "Cuando aplique: nuevo software de terceros (SOUP) registrado en 0GS-001 SCMP",
            "description": "Si aplica a los cambios de la historia, el plan 0GS-001 Software Configuration Management Plan debe actualizarse antes de cerrar la historia.",
        },
    },
    "17": {
        "en": {
            "group": "Technical Documentation",
            "title": "When applicable: product architecture document updated",
            "description": "If applicable to the story changes, Product Architecture Document must be updated before closing the story.",
        },
        "es": {
            "group": "Documentación Técnica",
            "title": "Cuando aplique: documento de arquitectura de producto actualizado",
            "description": "Si aplica a los cambios de la historia, el Documento de Arquitectura de Producto debe actualizarse antes de cerrar la historia.",
        },
    },
    "18": {
        "en": {
            "group": "Technical Documentation",
            "title": "When applicable: product design document updated",
            "description": "If applicable to the story changes, Product Design Document must be updated before closing the story.",
        },
        "es": {
            "group": "Documentación Técnica",
            "title": "Cuando aplique: documento de diseño de producto actualizado",
            "description": "Si aplica a los cambios de la historia, el Documento de Diseño de Producto debe actualizarse antes de cerrar la historia.",
        },
    },
    "19": {
        "en": {
            "group": "Technical Documentation",
            "title": "When applicable: API documentation updated if APIs changed",
            "description": "If applicable to the story changes, API Documentation must be updated before closing the story.",
        },
        "es": {
            "group": "Documentación Técnica",
            "title": "Cuando aplique: documentación de API actualizada si hubo cambios de API",
            "description": "Si aplica a los cambios de la historia, la Documentación de APIs debe actualizarse antes de cerrar la historia.",
        },
    },
    "20": {
        "en": {
            "group": "Technical Documentation",
            "title": "Documentation was updated if needed",
            "description": "If applicable to the story changes, General Technical/User Documentation in Google Drive must be updated.",
        },
        "es": {
            "group": "Documentación Técnica",
            "title": "Documentación actualizada si fue necesario",
            "description": "Si aplica a los cambios de la historia, la documentación técnica o de usuario en Google Drive debe actualizarse.",
        },
    },
    "21": {
        "en": {
            "group": "Resolution & Status",
            "title": "Story Resolved and resolution = Done",
            "description": "The Story must reach 'Resolved' (or 'Closed') status with resolution set to 'Done'.",
        },
        "es": {
            "group": "Estado y Cierre",
            "title": "Story en estado Resolved y resolution = Done",
            "description": "La Story debe alcanzar el estado 'Resolved' (o 'Closed') con resolución en 'Done'.",
        },
    },
}


def configure_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def log_step(start_time: float, msg: str) -> None:
    elapsed = time.perf_counter() - start_time
    print(f"[+{elapsed:.1f}s] {msg}", flush=True)


def pmap(fn: Callable[[Any], Any], items: List[Any], workers: int = HTTP_WORKERS) -> List[Any]:
    """Order-preserving parallel map for I/O-bound calls."""
    items = list(items)
    if len(items) <= 1:
        return [fn(i) for i in items]
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
        return list(pool.map(fn, items))


def retry_delay(exc: urllib.error.HTTPError, attempt: int) -> float:
    try:
        return min(10.0, float(exc.headers.get("Retry-After") or 0) or 1.5 * (attempt + 1))
    except (TypeError, ValueError):
        return 1.5 * (attempt + 1)


def discover_language(cli_lang: Optional[str] = None) -> str:
    """Resolve report language: CLI argument > Repo memory > Default ('en')."""
    if cli_lang and cli_lang.lower() in ("en", "es"):
        return cli_lang.lower()
    story_mem = MEMORIES_DIR / "story-dod.json"
    if story_mem.is_file():
        try:
            mem_data = json.loads(story_mem.read_text(encoding="utf-8-sig"))
            saved = str(mem_data.get("language") or "").lower()
            if saved in ("en", "es"):
                return saved
        except Exception:
            pass
    return "en"


def save_language_preference(lang: str) -> None:
    """Persist language choice in repo memory."""
    update_memory(language=lang)


def update_memory(**values: str) -> None:
    """Merge values into the repo memory file (language, lastAuditedStory, lastAuditedSprint)."""
    story_mem = MEMORIES_DIR / "story-dod.json"
    mem_data: Dict[str, Any] = {}
    if story_mem.is_file():
        try:
            mem_data = json.loads(story_mem.read_text(encoding="utf-8-sig"))
        except Exception:
            mem_data = {}
    mem_data.update(values)
    try:
        story_mem.parent.mkdir(parents=True, exist_ok=True)
        story_mem.write_text(json.dumps(mem_data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def normalize_person_name(name: str) -> Set[str]:
    """Normalize display names or dotted usernames into a set of lowercase words."""
    if not name:
        return set()
    cleaned = name.lower()
    for ch, rep in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n")):
        cleaned = cleaned.replace(ch, rep)
    cleaned = re.sub(r"[._\-]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return set(cleaned.split())


def is_same_person(name_a: str, name_b: str) -> bool:
    words_a = normalize_person_name(name_a)
    words_b = normalize_person_name(name_b)
    if not words_a or not words_b:
        return False
    return words_a == words_b or words_a.issubset(words_b) or words_b.issubset(words_a)


def is_po_or_proxy(name: str) -> bool:
    words = normalize_person_name(name)
    if not words:
        return False
    for po in PO_NAMES:
        po_words = normalize_person_name(po)
        if po_words and po_words.issubset(words):
            return True
    return False


# ---------------------------------------------------------------------------
# ADF (Atlassian Document Format) Parsers
# ---------------------------------------------------------------------------

def adf_to_text(node: Any) -> str:
    """Convert an Atlassian Document Format (ADF) JSON tree or string to plain text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(child) for child in node)
    if not isinstance(node, dict):
        return str(node)

    ntype = node.get("type", "")
    text_parts: List[str] = []

    if ntype == "text":
        txt = node.get("text", "")
        text_parts.append(txt)
        for mark in node.get("marks", []) or []:
            if isinstance(mark, dict) and mark.get("type") == "link":
                href = (mark.get("attrs") or {}).get("href", "")
                if href and href not in txt:
                    text_parts.append(f" ({href}) ")
    elif ntype in ("inlineCard", "blockCard", "embedCard"):
        url = (node.get("attrs") or {}).get("url", "")
        if url:
            text_parts.append(f" {url} ")
    elif ntype == "hardBreak":
        text_parts.append("\n")

    content = node.get("content")
    if isinstance(content, list):
        if ntype == "heading":
            level = int((node.get("attrs") or {}).get("level", 2) or 2)
            text_parts.append(f"{'#' * level} ")
        for child in content:
            child_txt = adf_to_text(child)
            if isinstance(child, dict) and child.get("type") in ("listItem", "taskItem"):
                text_parts.append(f"- {child_txt.strip()}\n")
            else:
                text_parts.append(child_txt)

    if ntype in ("paragraph", "heading", "blockquote", "codeBlock", "tableRow", "bulletList", "orderedList", "taskList", "taskItem"):
        text_parts.append("\n")

    return "".join(text_parts)


def extract_acceptance_criteria(desc_text: str) -> List[Dict[str, str]]:
    """Extract Acceptance Criteria items from a Story description."""
    if not desc_text:
        return []

    lines = [line.strip() for line in desc_text.splitlines() if line.strip()]
    header_re = re.compile(
        r"^(?:#+\s*|\*+\s*)?(?:acceptance\s+criteria|criterios\s+de\s+aceptaci[oó]n|ac\s*s?)\b[:\s]*$",
        re.IGNORECASE,
    )
    bullet_re = re.compile(r"^(?:[-*•]|\d+[.)])\s+(.*)$")
    stop_section_re = re.compile(
        r"^(?:#+\s*|\*+\s*)?(?:examples?\b|out\s+of\s+scope|fuera\s+de\s+alcance|notes?\b|notas?\b|definition\s+of\s+done|dod\b|figma\b)",
        re.IGNORECASE,
    )

    collecting = False
    collecting_level = 2
    ac_lines: List[str] = []

    for line in lines:
        if header_re.match(line):
            collecting = True
            m_hashes = re.match(r"^(#+)", line)
            collecting_level = len(m_hashes.group(1)) if m_hashes else 2
            continue
        if collecting:
            if stop_section_re.match(line):
                break
            m_hashes = re.match(r"^(#+)", line)
            if m_hashes and len(m_hashes.group(1)) <= collecting_level:
                break
            b_match = bullet_re.match(line)
            if b_match:
                ac_lines.append(b_match.group(1).strip())
            elif ac_lines and not line.startswith("---") and not line.startswith("#"):
                ac_lines[-1] = f"{ac_lines[-1]} {line}"

    if not ac_lines:
        for line in lines:
            if re.match(r"^(?:acceptance\s+criteria|criterios\s+de\s+aceptaci[oó]n)\b[:\s]+(.*)$", line, re.IGNORECASE):
                m = re.match(r"^(?:acceptance\s+criteria|criterios\s+de\s+aceptaci[oó]n)\b[:\s]+(.*)$", line, re.IGNORECASE)
                if m and m.group(1).strip():
                    ac_lines.append(m.group(1).strip())
            elif re.match(r"^(?:given\b|scenario\b|scenario\s+outline\b|escenario\b)", line, re.IGNORECASE):
                ac_lines.append(line)

    gherkin_step_re = re.compile(r"^(?:given|when|then|and|but|dado|cuando|entonces|y|pero)\b", re.IGNORECASE)
    grouped_lines: List[str] = []
    for line in ac_lines:
        if gherkin_step_re.match(line):
            if grouped_lines:
                grouped_lines[-1] = f"{grouped_lines[-1]} | {line}"
            else:
                grouped_lines.append(line)
        else:
            grouped_lines.append(line)

    results: List[Dict[str, str]] = []
    for idx, text in enumerate(grouped_lines, start=1):
        results.append({"id": f"AC{idx}", "text": text})
    return results


# ---------------------------------------------------------------------------
# Credentials, .env & Multi-IDE MCP Config Discovery
# ---------------------------------------------------------------------------

def _parse_dotenv(path: Path) -> Dict[str, str]:
    """Parse a standard .env file (KEY=VALUE, optional 'export ', quotes, # comments) using stdlib only."""
    env_map: Dict[str, str] = {}
    if not path.is_file():
        return env_map
    try:
        for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            if len(val) >= 2 and ((val[0] == '"' and val[-1] == '"') or (val[0] == "'" and val[-1] == "'")):
                val = val[1:-1]
            elif " #" in val:
                val = val.split(" #", 1)[0].strip()
            if key and val:
                env_map[key] = val
    except Exception:
        pass
    return env_map


def discover_credentials(
    cli_jira_url: Optional[str] = None,
    cli_jira_email: Optional[str] = None,
    cli_jira_token: Optional[str] = None,
    cli_zephyr_token: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    # 1. Load .env.local and .env from current working directory, workspace root, or parent workspace
    dotenv_vars: Dict[str, str] = {}
    candidate_roots: List[Path] = []
    for root in (Path.cwd(), WORKSPACE_ROOT, WORKSPACE_ROOT.parent):
        try:
            resolved = root.resolve()
            if resolved not in candidate_roots:
                candidate_roots.append(resolved)
        except Exception:
            pass
    for root in reversed(candidate_roots):
        for fname in (".env", ".env.local"):
            dotenv_vars.update(_parse_dotenv(root / fname))

    def _is_valid_cred(val: Any) -> bool:
        if not val or not isinstance(val, str):
            return False
        s = val.strip()
        if not s or "${" in s or "$(" in s or s.startswith("<") or "your_api_token" in s.lower() or "tu_token" in s.lower():
            return False
        return True

    def _get_var(*names: str) -> Optional[str]:
        for n in names:
            for v in (os.environ.get(n), dotenv_vars.get(n)):
                if _is_valid_cred(v):
                    return str(v).strip()
        return None

    creds: Dict[str, Optional[str]] = {
        "jira_base_url": cli_jira_url or _get_var("JIRA_BASE_URL", "JIRA_URL"),
        "jira_email": cli_jira_email or _get_var("JIRA_EMAIL", "JIRA_USERNAME"),
        "jira_api_token": cli_jira_token or _get_var("JIRA_API_TOKEN"),
        "zephyr_api_token": cli_zephyr_token or _get_var("ZEPHYR_API_TOKEN"),
        "zephyr_base_url": _get_var("ZEPHYR_BASE_URL") or "https://api.zephyrscale.smartbear.com/v2",
    }

    # 2. Check repo memory (story-dod.json)
    story_mem = MEMORIES_DIR / "story-dod.json"
    if story_mem.is_file():
        try:
            mem_data = json.loads(story_mem.read_text(encoding="utf-8-sig"))
            if not creds["zephyr_api_token"] and mem_data.get("zephyr_api_token"):
                creds["zephyr_api_token"] = mem_data.get("zephyr_api_token")
            if not creds["jira_base_url"] and mem_data.get("jira_base_url"):
                creds["jira_base_url"] = mem_data.get("jira_base_url")
        except Exception:
            pass

    # 3. Scan MCP config files across Cursor, Claude Code, VS Code, and Antigravity
    home = Path.home()
    mcp_config_paths: List[Path] = [
        home / ".cursor" / "mcp.json",
        WORKSPACE_ROOT / ".cursor" / "mcp.json",
        WORKSPACE_ROOT.parent / ".cursor" / "mcp.json",
        WORKSPACE_ROOT / ".mcp.json",
        WORKSPACE_ROOT.parent / ".mcp.json",
        home / ".claude.json",
        WORKSPACE_ROOT / ".vscode" / "mcp.json",
        WORKSPACE_ROOT.parent / ".vscode" / "mcp.json",
        home / ".gemini" / "config" / "mcp_config.json",
    ]
    seen_cfg: Set[Path] = set()
    for cfg_path in mcp_config_paths:
        try:
            resolved_cfg = cfg_path.resolve()
        except Exception:
            resolved_cfg = cfg_path
        if resolved_cfg in seen_cfg or not cfg_path.is_file():
            continue
        seen_cfg.add(resolved_cfg)
        try:
            mcp_data = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
            if not isinstance(mcp_data, dict):
                continue
            servers_map: Dict[str, Any] = {}
            for section_key in ("mcpServers", "servers"):
                sec = mcp_data.get(section_key)
                if isinstance(sec, dict):
                    servers_map.update(sec)
            for srv_cfg in servers_map.values():
                if not isinstance(srv_cfg, dict):
                    continue
                srv_env = srv_cfg.get("env")
                if not isinstance(srv_env, dict):
                    continue
                if not creds["jira_base_url"]:
                    cand_url = srv_env.get("JIRA_BASE_URL") or srv_env.get("JIRA_URL")
                    if _is_valid_cred(cand_url):
                        creds["jira_base_url"] = str(cand_url).strip()
                if not creds["jira_email"]:
                    cand_email = srv_env.get("JIRA_EMAIL") or srv_env.get("JIRA_USERNAME")
                    if _is_valid_cred(cand_email):
                        creds["jira_email"] = str(cand_email).strip()
                if not creds["jira_api_token"]:
                    cand_tok = srv_env.get("JIRA_API_TOKEN")
                    if _is_valid_cred(cand_tok):
                        creds["jira_api_token"] = str(cand_tok).strip()
                if not creds["zephyr_api_token"]:
                    cand_ztok = srv_env.get("ZEPHYR_API_TOKEN")
                    if _is_valid_cred(cand_ztok):
                        creds["zephyr_api_token"] = str(cand_ztok).strip()
                cand_zurl = srv_env.get("ZEPHYR_BASE_URL")
                if _is_valid_cred(cand_zurl) and creds["zephyr_base_url"] == "https://api.zephyrscale.smartbear.com/v2":
                    creds["zephyr_base_url"] = str(cand_zurl).strip()
        except Exception:
            pass

    if creds["jira_base_url"]:
        creds["jira_base_url"] = creds["jira_base_url"].rstrip("/")
    else:
        creds["jira_base_url"] = "https://genesissoftwareinnovations.atlassian.net"
    return creds


# ---------------------------------------------------------------------------
# Multi-MCP Issue & PR Payload Normalizers
# ---------------------------------------------------------------------------

def _unwrap_customfield_val(val: Any) -> Any:
    """Unwrap mcp-atlassian / Atlassian Remote MCP {'name': ..., 'value': ...} wrapper."""
    if isinstance(val, dict) and "value" in val and set(val.keys()).issubset({"name", "value", "id", "type"}):
        return val.get("value")
    return val


def normalize_dev_info_prs(dev_data: Any) -> List[dict]:
    """Normalize Bitbucket PR payloads from Jira dev-status REST, mcp-atlassian
    (jira_get_issue_development_info), or Atlassian Remote MCP v2 (bitbucketPullRequest)."""
    if not dev_data:
        return []
    raw_prs: List[dict] = []
    if isinstance(dev_data, list):
        raw_prs = [p for p in dev_data if isinstance(p, dict)]
    elif isinstance(dev_data, dict):
        if isinstance(dev_data.get("detail"), list):
            for det in dev_data["detail"]:
                if isinstance(det, dict):
                    for pr in det.get("pullRequests") or []:
                        if isinstance(pr, dict):
                            raw_prs.append(pr)
        for list_key in ("pullRequests", "pull_requests", "prs", "values", "data"):
            cand = dev_data.get(list_key)
            if isinstance(cand, list) and not raw_prs:
                raw_prs = [p for p in cand if isinstance(p, dict)]
        if not raw_prs and ("id" in dev_data or "state" in dev_data or "status" in dev_data):
            raw_prs = [dev_data]

    normalized: List[dict] = []
    for p in raw_prs:
        pr_id = p.get("id") or p.get("number") or p.get("pr_id") or "?"
        status = str(p.get("status") or p.get("state") or "UNKNOWN").upper()
        repo_obj = p.get("repository") or p.get("destination", {}).get("repository")
        repo_name = (
            p.get("repositoryName")
            or p.get("repository_name")
            or (repo_obj.get("name") or repo_obj.get("full_name") if isinstance(repo_obj, dict) else repo_obj)
            or "Bitbucket"
        )
        author_raw = p.get("author") or p.get("user") or {}
        if isinstance(author_raw, str):
            author_obj = {"name": author_raw}
        elif isinstance(author_raw, dict):
            author_obj = {
                "name": (
                    author_raw.get("name")
                    or author_raw.get("display_name")
                    or author_raw.get("displayName")
                    or author_raw.get("nickname")
                    or "Unknown"
                )
            }
        else:
            author_obj = {"name": "Unknown"}

        dest_raw = p.get("destination") or {}
        if isinstance(dest_raw, dict):
            branch_raw = dest_raw.get("branch")
            if isinstance(branch_raw, dict):
                dest_branch = str(branch_raw.get("name") or "master")
            else:
                dest_branch = str(branch_raw or p.get("destination_branch") or "master")
        else:
            dest_branch = str(p.get("destination_branch") or "master")

        reviewers: List[dict] = []
        raw_revs = p.get("reviewers") or p.get("participants") or []
        for r in raw_revs:
            if not isinstance(r, dict):
                continue
            user_sub = r.get("user") if isinstance(r.get("user"), dict) else {}
            r_name = (
                r.get("name")
                or r.get("display_name")
                or r.get("displayName")
                or user_sub.get("display_name")
                or user_sub.get("displayName")
                or user_sub.get("name")
                or user_sub.get("nickname")
                or ""
            )
            r_approved = bool(
                r.get("approved") is True
                or str(r.get("status") or r.get("state") or "").upper() == "APPROVED"
            )
            if r_name:
                reviewers.append({"name": str(r_name), "approved": r_approved})

        normalized.append({
            "id": str(pr_id).lstrip("#"),
            "status": status,
            "repositoryName": str(repo_name),
            "author": author_obj,
            "reviewers": reviewers,
            "destination": {"branch": dest_branch},
            "commentCount": int(p.get("commentCount") or p.get("comment_count") or 0),
            "url": str(p.get("url") or ((p.get("links") or {}).get("html") or {}).get("href") or ""),
        })
    return normalized


def parse_cf_10000_summary(cf_val: Any) -> Dict[str, Any]:
    """Parse Jira's customfield_10000 (Development summary string) to extract PR counts and state."""
    raw = str(_unwrap_customfield_val(cf_val) or "")
    info: Dict[str, Any] = {
        "has_prs": False,
        "count": 0,
        "state": None,
        "open_count": 0,
        "merged_count": 0,
        "declined_count": 0,
        "last_updated": None,
    }
    if "pullrequest=" not in raw and "pullrequest" not in raw.lower():
        return info

    # Look for pullrequest={dataType=pullrequest, state=MERGED, stateCount=1} or JSON summary inside cf_10000
    m_pr_block = re.search(r"pullrequest=\{([^{}]*)\}", raw)
    block = m_pr_block.group(1) if m_pr_block else raw
    m_count = re.search(r"stateCount=(\d+)", block)
    m_state = re.search(r"state=([A-Z_]+)", block)
    count = int(m_count.group(1)) if m_count else 0
    state = m_state.group(1).upper() if m_state else None

    # Also support JSON-encoded pullrequest summary inside customfield_10000
    if count == 0 and '"pullrequest"' in raw:
        m_json_count = re.search(r'"pullrequest"\s*:\s*\{[^}]*"count"\s*:\s*(\d+)', raw)
        m_json_state = re.search(r'"pullrequest"\s*:\s*\{[^}]*"state"\s*:\s*"([A-Za-z_]+)"', raw)
        if m_json_count:
            count = int(m_json_count.group(1))
        if m_json_state:
            state = m_json_state.group(1).upper()

    m_upd = re.search(r"lastUpdated=([0-9T:\-+.]+)", raw)
    if m_upd:
        info["last_updated"] = m_upd.group(1)[:10]

    if count > 0:
        info["has_prs"] = True
        info["count"] = count
        info["state"] = state or "UNKNOWN"
        if state == "MERGED":
            info["merged_count"] = count
        elif state == "OPEN":
            info["open_count"] = count
        elif state == "DECLINED":
            info["declined_count"] = count
    return info


def normalize_issue_payload(raw_issue: dict) -> dict:
    """Normalize Jira issue payloads across all 3 MCP schemas into Jira REST v3 shape:
      1. Standard Jira REST v3 / jira-nexus (`fields.*`, `changelog.histories`)
      2. mcp-atlassian (`jira_get_issue` flat root dict with `issue_type`, `comments`, `changelogs`, `inward_issue`/`outward_issue`, `customfield_*.value`)
      3. Atlassian Remote MCP v1/v2 (`getJiraIssue` with `fields` or `customFields.Development`/`Design`)
    """
    if not isinstance(raw_issue, dict):
        return {}

    # Unwrap common top-level envelopes if an MCP wraps the issue under {"issue": ...} or {"data": ...}
    if "key" not in raw_issue:
        for wrap_key in ("issue", "data", "result"):
            inner = raw_issue.get(wrap_key)
            if isinstance(inner, dict) and inner.get("key"):
                raw_issue = inner
                break

    raw_fields = raw_issue.get("fields")
    fields: Dict[str, Any] = dict(raw_fields) if isinstance(raw_fields, dict) else {}

    # Merge top-level flat fields from mcp-atlassian when missing in `fields`
    for k, v in raw_issue.items():
        if k in ("id", "key", "self", "expand", "fields", "changelog", "changelogs", "comments", "remote_links", "development_info", "prs"):
            continue
        if k not in fields or fields[k] is None:
            fields[k] = v

    # Normalize customFields dict from Atlassian Remote MCP v1/v2 if present
    for cf_container_key in ("customFields", "custom_fields", "names"):
        cf_container = raw_issue.get(cf_container_key) or fields.get(cf_container_key)
        if isinstance(cf_container, dict):
            for cf_k, cf_v in cf_container.items():
                cf_k_low = str(cf_k).strip().lower()
                unwrapped = _unwrap_customfield_val(cf_v)
                if cf_k_low in ("development", "customfield_10000") and not fields.get("customfield_10000"):
                    fields["customfield_10000"] = unwrapped
                elif cf_k_low in ("design", "customfield_10086") and not fields.get("customfield_10086"):
                    fields["customfield_10086"] = unwrapped
                elif str(cf_k).startswith("customfield_") and not fields.get(cf_k):
                    fields[cf_k] = unwrapped

    # Unwrap {"name": ..., "value": ...} on all customfield_* keys (mcp-atlassian format)
    for fk in list(fields.keys()):
        if str(fk).startswith("customfield_"):
            fields[fk] = _unwrap_customfield_val(fields[fk])

    # Normalize issuetype vs issue_type
    if not fields.get("issuetype") and fields.get("issue_type"):
        it_val = fields["issue_type"]
        fields["issuetype"] = {"name": it_val} if isinstance(it_val, str) else it_val
    elif isinstance(fields.get("issuetype"), str):
        fields["issuetype"] = {"name": fields["issuetype"]}

    # Normalize status & resolution if they are plain strings
    if isinstance(fields.get("status"), str):
        fields["status"] = {"name": fields["status"]}
    if isinstance(fields.get("resolution"), str):
        fields["resolution"] = {"name": fields["resolution"]}

    # Normalize assignee (`display_name` -> `displayName`)
    assignee = fields.get("assignee")
    if isinstance(assignee, str):
        fields["assignee"] = {"displayName": assignee}
    elif isinstance(assignee, dict) and not assignee.get("displayName"):
        disp = assignee.get("display_name") or assignee.get("name") or assignee.get("email")
        if disp:
            fields["assignee"] = dict(assignee, displayName=disp)

    # Normalize comments (`comments` list at root or `fields.comments` -> `fields.comment.comments`)
    raw_comments = raw_issue.get("comments") or fields.get("comments")
    if isinstance(raw_comments, list) and not ((fields.get("comment") or {}).get("comments")):
        fields["comment"] = {"comments": raw_comments}

    # Normalize issuelinks (`inward_issue`/`outward_issue` and flat linked issue fields -> Jira REST v3 shape)
    norm_links: List[dict] = []
    for link in fields.get("issuelinks") or []:
        if not isinstance(link, dict):
            continue
        link_copy = dict(link)
        for snake_dir, camel_dir in (("outward_issue", "outwardIssue"), ("inward_issue", "inwardIssue")):
            target = link_copy.get(camel_dir) or link_copy.get(snake_dir)
            if isinstance(target, dict):
                t_copy = dict(target)
                t_fields = dict(t_copy.get("fields") or {})
                for tk in ("summary", "status", "priority", "issuetype", "issue_type"):
                    if tk not in t_fields and tk in t_copy:
                        t_fields[tk] = t_copy[tk]
                if not t_fields.get("issuetype") and t_fields.get("issue_type"):
                    it_v = t_fields["issue_type"]
                    t_fields["issuetype"] = {"name": it_v} if isinstance(it_v, str) else it_v
                for obj_k in ("status", "priority", "issuetype"):
                    if isinstance(t_fields.get(obj_k), str):
                        t_fields[obj_k] = {"name": t_fields[obj_k]}
                t_copy["fields"] = t_fields
                link_copy[camel_dir] = t_copy
        norm_links.append(link_copy)
    if norm_links:
        fields["issuelinks"] = norm_links

    # Normalize subtasks (support flat subtasks or `fields` wrapper, plus assignee/resolution)
    norm_subtasks: List[dict] = []
    for st in fields.get("subtasks") or []:
        if not isinstance(st, dict):
            continue
        st_copy = dict(st)
        st_fields = dict(st_copy.get("fields") or {})
        for sk in ("summary", "status", "resolution", "assignee", "issuetype", "issue_type"):
            if sk not in st_fields and sk in st_copy:
                st_fields[sk] = st_copy[sk]
        for obj_k in ("status", "resolution", "issuetype"):
            if isinstance(st_fields.get(obj_k), str):
                st_fields[obj_k] = {"name": st_fields[obj_k]}
        st_ass = st_fields.get("assignee")
        if isinstance(st_ass, str):
            st_fields["assignee"] = {"displayName": st_ass}
        elif isinstance(st_ass, dict) and not st_ass.get("displayName"):
            st_disp = st_ass.get("display_name") or st_ass.get("name")
            if st_disp:
                st_fields["assignee"] = dict(st_ass, displayName=st_disp)
        st_copy["fields"] = st_fields
        norm_subtasks.append(st_copy)
    if norm_subtasks:
        fields["subtasks"] = norm_subtasks

    # Normalize changelog (`changelogs` list with `display_name` & `to_string` -> `changelog.histories`)
    changelog = raw_issue.get("changelog")
    raw_histories: List[dict] = []
    if isinstance(changelog, dict) and isinstance(changelog.get("histories"), list):
        raw_histories = changelog["histories"]
    elif isinstance(raw_issue.get("changelogs"), list):
        raw_histories = raw_issue["changelogs"]
    elif isinstance(fields.get("changelogs"), list):
        raw_histories = fields["changelogs"]

    norm_histories: List[dict] = []
    for h in raw_histories:
        if not isinstance(h, dict):
            continue
        h_copy = dict(h)
        h_author = h_copy.get("author")
        if isinstance(h_author, str):
            h_copy["author"] = {"displayName": h_author}
        elif isinstance(h_author, dict) and not h_author.get("displayName"):
            h_disp = h_author.get("display_name") or h_author.get("name") or h_author.get("email")
            if h_disp:
                h_copy["author"] = dict(h_author, displayName=h_disp)
        norm_items: List[dict] = []
        for item in h_copy.get("items") or []:
            if not isinstance(item, dict):
                continue
            item_copy = dict(item)
            if "toString" not in item_copy and "to_string" in item_copy:
                item_copy["toString"] = item_copy["to_string"]
            if "fromString" not in item_copy and "from_string" in item_copy:
                item_copy["fromString"] = item_copy["from_string"]
            norm_items.append(item_copy)
        h_copy["items"] = norm_items
        norm_histories.append(h_copy)

    # Normalize inlined remote_links if provided by mcp-atlassian (`include="remote_links,..."`)
    inlined_remote_links: List[dict] = []
    raw_rl = raw_issue.get("remote_links") or fields.get("remote_links") or fields.get("remotelinks")
    if isinstance(raw_rl, list):
        for rl in raw_rl:
            if not isinstance(rl, dict):
                continue
            if isinstance(rl.get("object"), dict) and rl["object"].get("url"):
                inlined_remote_links.append(rl)
            elif rl.get("url"):
                inlined_remote_links.append({"object": {"url": rl["url"], "title": rl.get("title", "")}})

    # Normalize inlined development_info / PRs if bundled inside the issue JSON
    inlined_prs = normalize_dev_info_prs(
        raw_issue.get("development_info") or raw_issue.get("prs") or raw_issue.get("pullRequests")
    )

    normalized_issue = dict(raw_issue)
    normalized_issue["fields"] = fields
    normalized_issue["changelog"] = {"histories": norm_histories}
    if inlined_remote_links:
        normalized_issue["_inlined_remote_links"] = inlined_remote_links
    if inlined_prs:
        normalized_issue["_inlined_prs"] = inlined_prs
    return normalized_issue


# ---------------------------------------------------------------------------
# REST Clients (Jira Cloud & Zephyr Scale)
# ---------------------------------------------------------------------------

class JiraRestClient:
    def __init__(self, base_url: str, email: str, api_token: str, offline: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.offline = offline
        raw_auth = f"{email}:{api_token}".encode("utf-8")
        b64_auth = base64.b64encode(raw_auth).decode("ascii")
        self.headers = {
            "Authorization": f"Basic {b64_auth}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self._fields_cache: Optional[Dict[str, str]] = None
        self._fields_lock = threading.Lock()

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        if self.offline:
            raise RuntimeError("Jira API not available in offline mode")
        url = f"{self.base_url}{path}" if path.startswith("/") else path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        for attempt in range(3):
            req = urllib.request.Request(url, data=data, headers=self.headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=25) as resp:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw) if raw.strip() else {}
            except urllib.error.HTTPError as exc:
                if exc.code in RETRY_STATUSES and attempt < 2:
                    time.sleep(retry_delay(exc, attempt))
                    continue
                err_body = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Jira API HTTP {exc.code} for {method} {url}: {err_body}") from exc
            except Exception as exc:
                raise RuntimeError(f"Jira API error for {method} {url}: {exc}") from exc
        raise RuntimeError(f"Jira API retries exhausted for {method} {url}")

    def get_issue(self, issue_key: str, expand_changelog: bool = True) -> dict:
        expand_clause = "?expand=changelog" if expand_changelog else ""
        return self._request("GET", f"/rest/api/3/issue/{issue_key}{expand_clause}")

    def search_jql(self, jql: str, fields: Optional[List[str]] = None, max_results: int = 50) -> List[dict]:
        payload = {
            "jql": jql,
            "maxResults": max_results,
            "fields": fields or ["*all"],
        }
        resp = self._request("POST", "/rest/api/3/search/jql", body=payload)
        return resp.get("issues", [])

    def get_dev_status_prs(self, numeric_issue_id: str) -> List[dict]:
        """Fetch linked Bitbucket Pull Requests from Jira internal dev-status endpoint."""
        url_path = f"/rest/dev-status/1.0/issue/detail?issueId={numeric_issue_id}&applicationType=bitbucket&dataType=pullrequest"
        try:
            resp = self._request("GET", url_path)
        except Exception:
            try:
                url_path_generic = f"/rest/dev-status/1.0/issue/detail?issueId={numeric_issue_id}&dataType=pullrequest"
                resp = self._request("GET", url_path_generic)
            except Exception:
                return []

        prs: List[dict] = []
        for detail in resp.get("detail", []):
            for pr in detail.get("pullRequests", []):
                prs.append(pr)
        return prs

    def get_remote_links(self, issue_key: str) -> List[dict]:
        try:
            res = self._request("GET", f"/rest/api/3/issue/{issue_key}/remotelink")
            return res if isinstance(res, list) else []
        except Exception:
            return []

    def get_fields_map(self) -> Dict[str, str]:
        with self._fields_lock:
            return self._load_fields_map()

    def _load_fields_map(self) -> Dict[str, str]:
        if self._fields_cache is not None:
            return self._fields_cache
        try:
            all_fields = self._request("GET", "/rest/api/3/field")
            mapping: Dict[str, str] = {}
            if isinstance(all_fields, list):
                for f in all_fields:
                    fname = str(f.get("name", "")).strip().lower()
                    fid = str(f.get("id", ""))
                    if fname and fid:
                        mapping[fname] = fid
            self._fields_cache = mapping
            return mapping
        except Exception:
            self._fields_cache = {}
            return {}


class ZephyrRestClient:
    """Zephyr Scale Cloud REST API v2 Client."""
    def __init__(self, api_token: Optional[str] = None, base_url: str = "https://api.zephyrscale.smartbear.com/v2") -> None:
        self.api_token = api_token
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {api_token}" if api_token else "",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self.last_error: Optional[str] = None
        self._statuses_cache: Optional[Dict[int, str]] = None
        self._environments_cache: Optional[Dict[int, str]] = None
        self._project_testcases_cache: Optional[List[Dict[str, Any]]] = None
        self._testcycles_cache: Dict[str, str] = {}
        self._lock = threading.RLock()

    @property
    def available(self) -> bool:
        return bool(self.api_token)

    def _request(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        if not self.available:
            return None
        qs = f"?{urllib.parse.urlencode(params)}" if params else ""
        url = f"{self.base_url}{path}{qs}"
        for attempt in range(3):
            req = urllib.request.Request(url, headers=self.headers, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw) if raw.strip() else {}
            except urllib.error.HTTPError as exc:
                if exc.code in RETRY_STATUSES and attempt < 2:
                    time.sleep(retry_delay(exc, attempt))
                    continue
                err_body = exc.read().decode("utf-8", errors="replace")
                self.last_error = f"HTTP {exc.code}: {err_body[:160]}"
                return None
            except Exception as exc:
                self.last_error = str(exc)
                return None
        return None

    def get_statuses(self, project_key: str = "RSV1") -> Dict[int, str]:
        with self._lock:
            if self._statuses_cache is not None:
                return self._statuses_cache
            res = self._request(f"/statuses?projectKey={project_key}&maxResults=50")
            mapping: Dict[int, str] = {}
            if res and isinstance(res, dict):
                for item in res.get("values", []):
                    if item.get("id") and item.get("name"):
                        mapping[int(item["id"])] = str(item["name"])
            self._statuses_cache = mapping
            return mapping

    def get_environments(self, project_key: str = "RSV1") -> Dict[int, str]:
        with self._lock:
            if self._environments_cache is not None:
                return self._environments_cache
            res = self._request(f"/environments?projectKey={project_key}&maxResults=50")
            mapping: Dict[int, str] = {}
            if res and isinstance(res, dict):
                for item in res.get("values", []):
                    if item.get("id") and item.get("name"):
                        mapping[int(item["id"])] = str(item["name"])
            self._environments_cache = mapping
            return mapping

    def get_all_project_testcases(self, project_key: str = "RSV1", max_age_seconds: int = 3600) -> List[Dict[str, Any]]:
        with self._lock:
            return self._load_project_testcases(project_key, max_age_seconds)

    def _load_project_testcases(self, project_key: str, max_age_seconds: int) -> List[Dict[str, Any]]:
        if self._project_testcases_cache is not None:
            return self._project_testcases_cache

        cache_file = MEMORIES_DIR / f"zephyr_{project_key}_cache.json"
        if cache_file.is_file():
            try:
                mtime = cache_file.stat().st_mtime
                if time.time() - mtime < max_age_seconds:
                    data = json.loads(cache_file.read_text(encoding="utf-8"))
                    if isinstance(data, list) and data:
                        self._project_testcases_cache = data
                        return data
            except Exception:
                pass

        if not self.available:
            return []

        all_tcs: List[Dict[str, Any]] = []
        start_at = 0
        limit = 100
        while True:
            res = self._request(f"/testcases?projectKey={project_key}&startAt={start_at}&maxResults={limit}")
            if not res or not isinstance(res, dict):
                break
            vals = res.get("values", [])
            all_tcs.extend(vals)
            if res.get("isLast", True) or not vals:
                break
            start_at += len(vals)

        if all_tcs:
            self._project_testcases_cache = all_tcs
            try:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps(all_tcs, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

        return all_tcs

    def _linked_testcases(self, issue_key: str) -> Optional[List[Dict[str, Any]]]:
        """Test cases linked to an issue via /issuelinks (real time, ~1 request + 1 per TC). None = use project scan."""
        res = self._request(f"/issuelinks/{urllib.parse.quote(issue_key)}/testcases")
        if not isinstance(res, list):
            return None
        keys = list(dict.fromkeys(str(i["key"]) for i in res if isinstance(i, dict) and i.get("key")))
        details = pmap(lambda k: self._request(f"/testcases/{urllib.parse.quote(k)}"), keys)
        if any(not isinstance(d, dict) or not d.get("key") for d in details):
            return None
        return details

    def _scan_testcases(self, issue_key: str, numeric_issue_id: Optional[str], project_key: str) -> List[Dict[str, Any]]:
        all_tcs = self.get_all_project_testcases(project_key)
        target_ids: Set[int] = set()
        if numeric_issue_id:
            try:
                target_ids.add(int(numeric_issue_id))
            except (ValueError, TypeError):
                pass
        matched: List[Dict[str, Any]] = []
        for tc in all_tcs:
            for iss in (tc.get("links") or {}).get("issues") or []:
                if (target_ids and iss.get("issueId") in target_ids) or (
                    issue_key and issue_key.lower() in str(iss.get("target") or "").lower()
                ):
                    matched.append(tc)
                    break
        return matched

    def get_testcases_for_issue(
        self,
        issue_key: str,
        numeric_issue_id: Optional[str] = None,
        project_key: str = "RSV1",
    ) -> List[Dict[str, Any]]:
        if not self.available:
            return []

        status_map = self.get_statuses(project_key)
        self.get_environments(project_key)
        raw_tcs = self._linked_testcases(issue_key)
        source_ok = raw_tcs is not None
        if raw_tcs is None:
            raw_tcs = self._scan_testcases(issue_key, numeric_issue_id, project_key)
            source_ok = bool(self._project_testcases_cache)

        def build(tc: Dict[str, Any]) -> Dict[str, Any]:
            tc_key = tc.get("key") or tc.get("testCaseKey")
            status_obj = tc.get("status")
            status_id = status_obj.get("id") if isinstance(status_obj, dict) else None
            status_str = status_map.get(status_id) if status_id else (
                status_obj.get("name") if isinstance(status_obj, dict) else str(status_obj or "Draft")
            )
            return {
                "key": tc_key,
                "name": tc.get("name") or tc.get("summary") or "Untitled Test Case",
                "status": status_str,
                "objective": tc.get("objective", ""),
                "steps_text": "",
                "executions": self.get_executions_for_testcase(tc_key, project_key) if tc_key else [],
            }

        results = pmap(build, raw_tcs)
        results.sort(key=lambda t: (len(str(t["key"] or "")), str(t["key"] or "")))
        if source_ok:
            self.last_error = None
        return results

    def get_testscript(self, testcase_key: str) -> str:
        if not self.available:
            return ""
        res = self._request(f"/testcases/{testcase_key}/testscript")
        if not res or not isinstance(res, dict):
            return ""
        return str(res.get("text") or res.get("script") or "")

    def get_teststeps(self, testcase_key: str) -> str:
        if not self.available:
            return ""
        res = self._request(f"/testcases/{testcase_key}/teststeps")
        if not res:
            return self.get_testscript(testcase_key)
        values = res.get("values") if isinstance(res, dict) else (res if isinstance(res, list) else [])
        texts: List[str] = []
        for step in values or []:
            desc = (step.get("inline") or {}).get("description") or step.get("description") or ""
            exp = (step.get("inline") or {}).get("expectedResult") or step.get("expectedResult") or ""
            texts.append(f"{desc} {exp}")
        return " ".join(texts)

    def get_testcycle_name(self, cycle_ref: Any) -> str:
        """Resolve and cache a Zephyr Test Cycle's name (and key) from an execution's testCycle object."""
        if not self.available or not isinstance(cycle_ref, dict):
            return ""
        inline_name = cycle_ref.get("name")
        inline_key = cycle_ref.get("key")
        if inline_name:
            return f"{inline_key}: {inline_name}" if inline_key else str(inline_name)
        cycle_id = str(cycle_ref.get("id") or "").strip()
        self_url = str(cycle_ref.get("self") or "").strip()
        if not cycle_id and "/testcycles/" in self_url:
            cycle_id = self_url.rsplit("/testcycles/", 1)[-1].split("?", 1)[0].strip("/")
        if not cycle_id:
            return ""
        with self._lock:
            if cycle_id in self._testcycles_cache:
                return self._testcycles_cache[cycle_id]
        res = self._request(f"/testcycles/{urllib.parse.quote(cycle_id)}")
        label = ""
        if isinstance(res, dict):
            c_key = str(res.get("key") or "")
            c_name = str(res.get("name") or "")
            label = f"{c_key}: {c_name}".strip(": ") if (c_key or c_name) else ""
        with self._lock:
            self._testcycles_cache[cycle_id] = label
        return label

    def get_executions_for_testcase(self, testcase_key: str, project_key: str = "RSV1") -> List[Dict[str, Any]]:
        if not self.available:
            return []
        res = self._request("/testexecutions", params={"testCase": testcase_key, "maxResults": 50})
        if not res:
            return []
        status_map = self.get_statuses(project_key)
        env_map = self.get_environments(project_key)
        values = res.get("values") if isinstance(res, dict) else (res if isinstance(res, list) else [])
        execs: List[Dict[str, Any]] = []
        for e in values or []:
            status_obj = e.get("testExecutionStatus") or e.get("status")
            status_id = status_obj.get("id") if isinstance(status_obj, dict) else None
            status_name = status_map.get(status_id) if status_id else (
                status_obj.get("name") if isinstance(status_obj, dict) else str(status_obj or "")
            )
            env_obj = e.get("environment")
            env_id = env_obj.get("id") if isinstance(env_obj, dict) else None
            env_name = env_map.get(env_id) if env_id else (
                env_obj.get("name") if isinstance(env_obj, dict) else str(env_obj or "")
            )
            cycle_name = self.get_testcycle_name(e.get("testCycle"))
            execs.append({
                "id": str(e.get("id", "")),
                "key": e.get("key"),
                "status": status_name,
                "environment": env_name,
                "test_cycle_name": cycle_name,
                "executed_by": (e.get("executedBy") or {}).get("name") or (e.get("executedBy") or {}).get("displayName") or "",
                "date": str(e.get("actualEndDate") or e.get("executionDate") or e.get("createdOn") or "")[:10],
            })
        return execs


# ---------------------------------------------------------------------------
# NLP & Acceptance Criteria Keyword Matching
# ---------------------------------------------------------------------------

STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "user", "when", "then",
    "should", "must", "able", "system", "genesis", "jira", "story", "into",
    "have", "been", "will", "would", "could", "also", "each", "both", "such",
    "shall", "only", "are", "was", "were", "not", "can", "all",
    "que", "los", "las", "una", "uno", "para", "por", "con", "del", "como",
    "este", "esta", "estos", "estas", "debe", "puede", "sistema", "usuario",
}


def tokenize_keywords(text: str) -> Set[str]:
    words = re.findall(r"[a-zA-Z0-9]{3,}", text.lower())
    return {w for w in words if w not in STOPWORDS}


def is_word_match(w1: str, w2: str) -> bool:
    if w1 == w2:
        return True
    if len(w1) >= 4 and len(w2) >= 4:
        if w1.startswith(w2[:4]) or w2.startswith(w1[:4]):
            return True
        if w1 in w2 or w2 in w1:
            return True
    return False


def match_ac_to_testcases(
    ac_list: List[Dict[str, str]],
    test_cases: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Map each extracted Acceptance Criterion to matching Test Cases."""
    coverage: List[Dict[str, Any]] = []
    for ac in ac_list:
        ac_id = ac["id"]
        ac_text = ac["text"]
        ac_tokens = tokenize_keywords(ac_text)
        matched_tcs: List[str] = []

        for tc in test_cases:
            tc_key = tc["key"]
            combined = f"{tc.get('name', '')} {tc.get('objective', '')} {tc.get('steps_text', '')}"
            if ac_id.lower() in combined.lower():
                matched_tcs.append(tc_key)
                continue
            tc_tokens = tokenize_keywords(combined)
            if not ac_tokens or not tc_tokens:
                continue
            overlap = {a for a in ac_tokens for t in tc_tokens if is_word_match(a, t)}
            ratio = len(overlap) / max(1, min(len(ac_tokens), 5))
            if len(overlap) >= 2 or ratio >= 0.4:
                matched_tcs.append(tc_key)

        coverage.append({
            "ac_id": ac_id,
            "ac_text": ac_text,
            "matched_tcs": matched_tcs,
            "covered": len(matched_tcs) > 0,
        })
    return coverage


# ---------------------------------------------------------------------------
# Core Story Evaluator (21 DoD Criteria)
# ---------------------------------------------------------------------------

def evaluate_single_story(
    issue: dict,
    jira: JiraRestClient,
    zephyr: ZephyrRestClient,
    lang: str = "en",
    pre_fetched_prs: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    """Evaluate all 21 User Story DoD criteria for a single Jira Story."""
    if lang not in ("en", "es"):
        lang = "en"

    issue = normalize_issue_payload(issue)
    key = issue.get("key", "UNKNOWN")
    numeric_id = str(issue.get("id", ""))
    fields = issue.get("fields") or {}
    summary = str(fields.get("summary") or "")
    status_name = str((fields.get("status") or {}).get("name") or "Unknown")
    resolution_obj = fields.get("resolution")
    default_unresolved = "Sin resolución" if lang == "es" else "Unresolved"
    resolution_name = str(resolution_obj.get("name") if isinstance(resolution_obj, dict) and resolution_obj.get("name") else default_unresolved)
    assignee_obj = fields.get("assignee")
    assignee_name = str(assignee_obj.get("displayName") if isinstance(assignee_obj, dict) else "Unassigned")
    issue_type = str((fields.get("issuetype") or {}).get("name") or "Story")
    labels = [str(l) for l in (fields.get("labels") or [])]

    desc_text = adf_to_text(fields.get("description"))
    comments_list = ((fields.get("comment") or {}).get("comments")) or []
    comments_text = "\n".join(adf_to_text(c.get("body")) for c in comments_list if isinstance(c, dict))
    all_text = f"{summary}\n{desc_text}\n{comments_text}"

    inlined_prs = pre_fetched_prs if pre_fetched_prs is not None else issue.get("_inlined_prs")
    inlined_remote_links = issue.get("_inlined_remote_links")
    has_raw_subtasks = bool(fields.get("subtasks"))

    # 1. Fire the independent Jira calls in parallel (PRs via dev-status, remote links, field map, subtasks details)
    io_pool = ThreadPoolExecutor(max_workers=6)
    prs_future = (
        io_pool.submit(jira.get_dev_status_prs, numeric_id)
        if (numeric_id and not inlined_prs and not jira.offline)
        else None
    )
    remote_future = (
        io_pool.submit(jira.get_remote_links, key)
        if (not inlined_remote_links and not jira.offline)
        else None
    )
    fields_future = io_pool.submit(jira.get_fields_map) if not jira.offline else None
    subtasks_future = (
        io_pool.submit(
            jira.search_jql,
            f'parent = "{key}" ORDER BY key ASC',
            ["summary", "status", "resolution", "assignee", "issuetype"],
            50,
        )
        if (has_raw_subtasks and not jira.offline)
        else None
    )

    # Also parse customfield_10000 summary as fallback
    cf_10000 = str(fields.get("customfield_10000") or "")
    cf_summary = parse_cf_10000_summary(cf_10000)

    # 2. Inspect Issue Links & Parent for Requirement, Bugs, and Jira-level Test links
    linked_reqs: List[Dict[str, str]] = []
    linked_bugs: List[Dict[str, str]] = []
    linked_tests_jira: List[Dict[str, str]] = []

    parent_obj = fields.get("parent")
    if isinstance(parent_obj, dict) and parent_obj.get("key"):
        p_fields = parent_obj.get("fields") or {}
        p_type = str((p_fields.get("issuetype") or {}).get("name") or "")
        p_summary = str(p_fields.get("summary") or "")
        if p_type.lower() in ("req", "requirement"):
            linked_reqs.append({
                "id": str(parent_obj.get("id") or ""),
                "key": str(parent_obj["key"]),
                "summary": p_summary,
                "status": str((p_fields.get("status") or {}).get("name") or ""),
                "relation": "parent",
            })

    for link in fields.get("issuelinks") or []:
        if not isinstance(link, dict):
            continue
        ltype = link.get("type") or {}
        target = link.get("outwardIssue") or link.get("inwardIssue")
        direction = ltype.get("outward") if link.get("outwardIssue") else ltype.get("inward")
        if not isinstance(target, dict) or not target.get("key"):
            continue
        t_key = str(target["key"])
        t_id = str(target.get("id") or "")
        t_fields = target.get("fields") or {}
        t_type = str((t_fields.get("issuetype") or {}).get("name") or "")
        t_status = str((t_fields.get("status") or {}).get("name") or "")
        t_prio = str((t_fields.get("priority") or {}).get("name") or "")
        t_summary = str(t_fields.get("summary") or "")

        if t_type.lower() in ("req", "requirement"):
            linked_reqs.append({
                "id": t_id,
                "key": t_key,
                "summary": t_summary,
                "status": t_status,
                "relation": str(direction or "linked"),
            })
        elif t_type.lower() in ("bug", "defect"):
            linked_bugs.append({
                "id": t_id,
                "key": t_key,
                "summary": t_summary,
                "status": t_status,
                "priority": t_prio,
            })
        elif "test" in t_type.lower():
            linked_tests_jira.append({
                "id": t_id,
                "key": t_key,
                "summary": t_summary,
                "status": t_status,
            })

    # 3. Subtasks inspection (Documentation, UX Validation, Testing, etc.)
    subtasks_by_key: Dict[str, Dict[str, str]] = {}
    for st in fields.get("subtasks") or []:
        if isinstance(st, dict) and st.get("key"):
            st_k = str(st["key"])
            st_f = st.get("fields") or {}
            st_res_obj = st_f.get("resolution")
            st_ass_obj = st_f.get("assignee")
            subtasks_by_key[st_k] = {
                "key": st_k,
                "summary": str(st_f.get("summary") or ""),
                "status": str((st_f.get("status") or {}).get("name") or "Unknown"),
                "resolution": str(st_res_obj.get("name") if isinstance(st_res_obj, dict) and st_res_obj.get("name") else ""),
                "assignee": str(st_ass_obj.get("displayName") if isinstance(st_ass_obj, dict) and st_ass_obj.get("displayName") else ""),
            }
    if subtasks_future is not None:
        try:
            for full_st in subtasks_future.result() or []:
                if isinstance(full_st, dict) and full_st.get("key"):
                    st_k = str(full_st["key"])
                    st_f = full_st.get("fields") or {}
                    st_res_obj = st_f.get("resolution")
                    st_ass_obj = st_f.get("assignee")
                    subtasks_by_key[st_k] = {
                        "key": st_k,
                        "summary": str(st_f.get("summary") or subtasks_by_key.get(st_k, {}).get("summary") or ""),
                        "status": str((st_f.get("status") or {}).get("name") or subtasks_by_key.get(st_k, {}).get("status") or "Unknown"),
                        "resolution": str(st_res_obj.get("name") if isinstance(st_res_obj, dict) and st_res_obj.get("name") else ""),
                        "assignee": str(st_ass_obj.get("displayName") if isinstance(st_ass_obj, dict) and st_ass_obj.get("displayName") else ""),
                    }
        except Exception:
            pass
    subtasks: List[Dict[str, str]] = list(subtasks_by_key.values())

    # 4. Acceptance Criteria & Zephyr Test Cases via Requirement (and Story directly)
    ac_items = extract_acceptance_criteria(desc_text)
    zephyr_tcs: List[Dict[str, Any]] = []
    seen_tc_keys: Set[str] = set()

    if zephyr.available:
        proj_key = key.split("-")[0] if "-" in key else "RSV1"
        targets = [(req["key"], req.get("id")) for req in linked_reqs] + [(key, numeric_id)]
        tc_futures = [
            (t_key, io_pool.submit(zephyr.get_testcases_for_issue, t_key, t_id, proj_key))
            for t_key, t_id in targets
        ]
        for t_key, fut in tc_futures:
            for tc in fut.result():
                if tc["key"] not in seen_tc_keys:
                    tc["linked_via"] = t_key
                    zephyr_tcs.append(tc)
                    seen_tc_keys.add(tc["key"])
                elif t_key == key:
                    # If also linked directly to the Story, prefer the direct Story link
                    for existing_tc in zephyr_tcs:
                        if existing_tc["key"] == tc["key"]:
                            existing_tc["linked_via"] = key

    # Classify Zephyr Test Cases by Story scope (handles 1 Requirement -> N Stories via Test Cycle names)
    req_keys_upper = {r["key"].upper() for r in linked_reqs if r.get("key")}
    story_key_upper = key.upper()
    issue_key_pattern = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")

    for tc in zephyr_tcs:
        execs = tc.get("executions") or []
        cycle_names = [str(e.get("test_cycle_name") or "") for e in execs if e.get("test_cycle_name")]
        tc["cycle_names"] = list(dict.fromkeys(cycle_names))
        linked_directly = str(tc.get("linked_via") or "").upper() == story_key_upper
        named_for_story = story_key_upper in str(tc.get("name") or "").upper()
        cycle_matches_story = any(story_key_upper in cn.upper() for cn in cycle_names)

        other_story_keys: List[str] = []
        for cn in cycle_names:
            for m_key in issue_key_pattern.findall(cn.upper()):
                if m_key != story_key_upper and m_key not in req_keys_upper and not m_key.startswith("RSV1-R") and not m_key.startswith("RSV1-T"):
                    if m_key not in other_story_keys:
                        other_story_keys.append(m_key)
        tc["other_story_keys"] = other_story_keys

        if linked_directly or named_for_story or cycle_matches_story:
            tc["story_scope"] = "confirmed"
        elif other_story_keys:
            tc["story_scope"] = "other_story"
        else:
            tc["story_scope"] = "unscoped"

    # For AC matching, exclude test cases explicitly scoped to a different story's test cycle
    ac_eval_tcs = [t for t in zephyr_tcs if t.get("story_scope") != "other_story"] or zephyr_tcs
    ac_coverage = match_ac_to_testcases(ac_items, ac_eval_tcs)

    # 5. Collect PRs, remote links & field map (Figma inspection)
    prs: List[dict] = list(inlined_prs) if inlined_prs else []
    if prs_future is not None:
        try:
            prs = prs_future.result()
        except Exception:
            prs = []
    if inlined_remote_links is not None:
        remote_links = list(inlined_remote_links)
    elif remote_future is not None:
        try:
            remote_links = remote_future.result()
        except Exception:
            remote_links = []
    else:
        remote_links = []
    fields_map = fields_future.result() if fields_future is not None else {}
    io_pool.shutdown(wait=False)

    criteria_items: List[Dict[str, Any]] = []
    actions: List[Dict[str, Any]] = []

    # -----------------------------------------------------------------------
    # Group A: Code, Pull Requests & Pipelines (Items 1 - 6)
    # -----------------------------------------------------------------------

    # Item 1: Links between the Story and the code changes established (Bitbucket PR)
    spec_1 = CRITERIA_SPECS["1"][lang]
    pr_count = len(prs)
    has_cf_prs = bool(cf_summary.get("has_prs"))
    has_any_pr = pr_count > 0 or has_cf_prs
    cf_only_mode = (pr_count == 0 and has_cf_prs)

    if pr_count > 0:
        pr_summaries = [f"PR #{p.get('id')} ({p.get('repositoryName', 'repo')}): {p.get('status')}" for p in prs]
        note_1 = (
            f"{pr_count} Pull Request(s) de Bitbucket vinculado(s) a {key}."
            if lang == "es" else
            f"{pr_count} Bitbucket Pull Request(s) linked to {key}."
        )
        checks_1 = [
            (f"Se encontraron {pr_count} Pull Request(s) vinculados en Jira: " if lang == "es" else f"Found {pr_count} linked Pull Request(s) in Jira: ")
            + "; ".join(pr_summaries)
        ]
        criteria_items.append({
            "id": "1",
            "group": spec_1["group"],
            "title": spec_1["title"],
            "description": spec_1["description"],
            "status": "pass",
            "note": note_1,
            "checks": checks_1,
            "evidence": [p.get("url", "") for p in prs if p.get("url")],
        })
    elif has_cf_prs:
        cf_cnt = cf_summary["count"]
        cf_st = cf_summary["state"] or "LINKED"
        note_1 = (
            f"{cf_cnt} Pull Request(s) vinculado(s) a {key} en el panel Development de Jira (estado general: {cf_st})."
            if lang == "es" else
            f"{cf_cnt} Pull Request(s) linked to {key} in Jira Development panel (overall state: {cf_st})."
        )
        checks_1 = [
            f"Confirmado en el panel Development de Jira: {cf_cnt} Pull Request(s) registrado(s) con estado {cf_st}."
            if lang == "es" else
            f"Confirmed via Jira Development panel: {cf_cnt} Pull Request(s) recorded with state {cf_st}."
        ]
        criteria_items.append({
            "id": "1",
            "group": spec_1["group"],
            "title": spec_1["title"],
            "description": spec_1["description"],
            "status": "pass",
            "note": note_1,
            "checks": checks_1,
        })
    else:
        note_1 = (
            f"No hay Pull Requests de Bitbucket vinculados a {key}."
            if lang == "es" else
            f"No Bitbucket Pull Requests linked to {key}."
        )
        checks_1 = [
            f"Se consultaron los registros de integración con Bitbucket en Jira para {key}: no se encontraron Pull Requests."
            if lang == "es" else
            f"Checked Jira Bitbucket integration and Development data for {key}: 0 Pull Requests found."
        ]
        criteria_items.append({
            "id": "1",
            "group": spec_1["group"],
            "title": spec_1["title"],
            "description": spec_1["description"],
            "status": "fail",
            "note": note_1,
            "checks": checks_1,
            "issues": [key],
        })
        act_text_1 = (
            f"Vincular el/los Pull Request(s) de Bitbucket correspondientes a {key} incluyendo la clave en la rama o título del PR."
            if lang == "es" else
            f"Link the corresponding Bitbucket Pull Request(s) to {key} by including the issue key in the branch name or PR title."
        )
        actions.append({
            "group": "fix",
            "criterion": "1",
            "text": act_text_1,
            "issues": [key],
        })

    # Analyze approvals and merge states across PRs
    open_prs = [p for p in prs if str(p.get("status", "")).upper() == "OPEN"]
    merged_prs = [p for p in prs if str(p.get("status", "")).upper() == "MERGED"]

    pr_approval_checks: List[str] = []
    pr_approval_failures: List[str] = []

    for p in prs:
        pr_id = p.get("id", "?")
        pr_status = str(p.get("status", "")).upper()
        if pr_status == "DECLINED" and len(merged_prs) > 0:
            declined_msg = (
                f"PR #{pr_id}: declinado (no bloqueante porque la historia cuenta con PR mergeado)."
                if lang == "es" else
                f"PR #{pr_id}: declined (non-blocking since the story has a merged PR)."
            )
            pr_approval_checks.append(declined_msg)
            continue
        repo_name = str(p.get("repositoryName") or "")
        author_name = str((p.get("author") or {}).get("name") or ("Desconocido" if lang == "es" else "Unknown"))
        is_unity = "unity" in repo_name.lower() or "[unity]" in summary.lower()
        req_approvals = 2 if is_unity else 1

        approvers: List[str] = []
        for rev in p.get("reviewers") or []:
            if isinstance(rev, dict) and rev.get("approved") is True:
                rev_name = str(rev.get("name") or "")
                if not is_same_person(rev_name, author_name):
                    approvers.append(rev_name)

        if len(approvers) >= req_approvals and pr_status != "DECLINED":
            msg_ok = (
                f"PR #{pr_id} ({repo_name or 'Bitbucket'}, autor: {author_name}): "
                f"aprobado por {', '.join(approvers)} ({len(approvers)}/{req_approvals} aprobaciones requeridas)."
                if lang == "es" else
                f"PR #{pr_id} ({repo_name or 'Bitbucket'}, author: {author_name}): "
                f"approved by {', '.join(approvers)} ({len(approvers)}/{req_approvals} required approvals)."
            )
            pr_approval_checks.append(msg_ok)
        else:
            msg_fail = (
                f"PR #{pr_id} ({repo_name or 'Bitbucket'}, autor: {author_name}, estado: {pr_status}): "
                f"registra {len(approvers)} de {req_approvals} aprobación(es) requerida(s) de revisores distintos al autor."
                if lang == "es" else
                f"PR #{pr_id} ({repo_name or 'Bitbucket'}, author: {author_name}, status: {pr_status}): "
                f"has {len(approvers)} of {req_approvals} required non-author approval(s)."
            )
            pr_approval_failures.append(msg_fail)

    all_prs_properly_approved = (
        pr_count > 0
        and (len(merged_prs) > 0 or len(open_prs) > 0)
        and len(pr_approval_failures) == 0
    )

    # Item 2: Code review evidence linked (bitbucket PRs linked)
    spec_2 = CRITERIA_SPECS["2"][lang]
    if pr_count > 0 and all_prs_properly_approved:
        note_2 = (
            "Evidencia de code review verificada mediante Pull Request(s) aprobado(s) en Bitbucket."
            if lang == "es" else
            "Code review evidence verified via approved Bitbucket Pull Request(s)."
        )
        criteria_items.append({
            "id": "2",
            "group": spec_2["group"],
            "title": spec_2["title"],
            "description": spec_2["description"],
            "status": "pass",
            "note": note_2,
            "checks": pr_approval_checks,
        })
    elif cf_only_mode:
        note_2 = (
            f"Se detectaron {cf_summary['count']} Pull Request(s) ({cf_summary['state']}); el detalle de revisores de Bitbucket requiere verificación manual en modo MCP."
            if lang == "es" else
            f"Detected {cf_summary['count']} Pull Request(s) ({cf_summary['state']}); Bitbucket reviewer detail requires manual verification in MCP mode."
        )
        checks_2 = [
            (
                f"El panel Development de Jira confirma {cf_summary['count']} Pull Request(s) en estado {cf_summary['state']}."
                if lang == "es" else
                f"Jira Development panel confirms {cf_summary['count']} Pull Request(s) in {cf_summary['state']} state."
            ),
            (
                "El conector MCP utilizado no expone la lista individual de revisores de Bitbucket; configurar credenciales en .env permite validar aprobaciones automáticamente."
                if lang == "es" else
                "The active MCP connector does not expose individual Bitbucket reviewers; configuring credentials in .env enables automatic approval validation."
            ),
        ]
        criteria_items.append({
            "id": "2",
            "group": spec_2["group"],
            "title": spec_2["title"],
            "description": spec_2["description"],
            "status": "manual",
            "reason": "tool-unavailable",
            "note": note_2,
            "checks": checks_2,
            "issues": [key],
        })
    else:
        note_2 = (
            "Falta evidencia de Pull Requests aprobados para dar por completado el code review."
            if lang == "es" else
            "Missing approved Pull Request evidence to confirm code review completion."
        )
        no_approved_msg_2 = (
            "No hay Pull Requests aprobados para verificar el code review."
            if lang == "es" else
            "No approved Pull Requests to verify code review."
        )
        if prs:
            checks_2 = ([no_approved_msg_2] if not pr_approval_checks else []) + pr_approval_checks + pr_approval_failures
        else:
            checks_2 = [no_approved_msg_2]
        criteria_items.append({
            "id": "2",
            "group": spec_2["group"],
            "title": spec_2["title"],
            "description": spec_2["description"],
            "status": "fail",
            "note": note_2,
            "checks": checks_2,
            "issues": [key],
        })

    # Item 3: Code-review observations Fixed or Won't fix with rationale in the review
    spec_3 = CRITERIA_SPECS["3"][lang]
    total_comments = sum(int(p.get("commentCount") or 0) for p in prs)
    if pr_count > 0 and len(merged_prs) > 0 and len(open_prs) == 0 and all_prs_properly_approved:
        note_3 = (
            f"Cumplido: todos los Pull Requests integrados fueron aprobados por sus revisores ({total_comments} comentario(s) registrados en la revisión)."
            if lang == "es" else
            f"Fulfilled: all merged Pull Requests were approved by reviewers ({total_comments} review comment(s) recorded)."
        )
        checks_3 = [
            (
                f"Todos los Pull Requests activos fueron integrados (MERGED) y aprobados ({total_comments} comentario(s) revisados)."
                if lang == "es" else
                f"All active Pull Requests are MERGED and approved by required reviewers ({total_comments} comment(s) across PRs)."
            ),
            (
                "Por acuerdo del equipo, la aprobación de los revisores y el merge confirman que las observaciones fueron resueltas."
                if lang == "es" else
                "Per team agreement, reviewer approval and merge confirm that review observations were resolved."
            ),
        ]
        criteria_items.append({
            "id": "3",
            "group": spec_3["group"],
            "title": spec_3["title"],
            "description": spec_3["description"],
            "status": "pass",
            "note": note_3,
            "checks": checks_3,
        })
    elif cf_only_mode and cf_summary["state"] == "MERGED":
        note_3 = (
            f"Los {cf_summary['count']} Pull Request(s) figuran integrados (MERGED); confirmar manualmente las aprobaciones de revisores en Bitbucket."
            if lang == "es" else
            f"All {cf_summary['count']} Pull Request(s) are MERGED; manually confirm reviewer approvals in Bitbucket."
        )
        checks_3 = [
            (
                f"El panel Development de Jira confirma {cf_summary['count']} Pull Request(s) en estado MERGED."
                if lang == "es" else
                f"Jira Development panel confirms {cf_summary['count']} Pull Request(s) in MERGED state."
            ),
            (
                "Verificar en Bitbucket que los revisores requeridos hayan aprobado el PR antes del merge."
                if lang == "es" else
                "Verify in Bitbucket that required reviewers approved the PR prior to merge."
            ),
        ]
        criteria_items.append({
            "id": "3",
            "group": spec_3["group"],
            "title": spec_3["title"],
            "description": spec_3["description"],
            "status": "manual",
            "reason": "tool-unavailable",
            "note": note_3,
            "checks": checks_3,
            "issues": [key],
        })
    else:
        note_3 = (
            "No se puede dar por cerrado el seguimiento de observaciones hasta que todos los Pull Requests estén aprobados y mergeados."
            if lang == "es" else
            "Review observations cannot be confirmed resolved until all Pull Requests are approved and merged."
        )
        if not has_any_pr:
            checks_3 = [
                "No hay Pull Requests aprobados y mergeados asociados a la historia para verificar el cierre de observaciones."
                if lang == "es" else
                "No approved and merged Pull Requests found on the story to verify observation resolution."
            ]
        elif open_prs and pr_approval_failures:
            checks_3 = [
                f"Existen {len(open_prs)} Pull Request(s) aún abiertos y pendientes de completar las aprobaciones requeridas."
                if lang == "es" else
                f"There are {len(open_prs)} open Pull Request(s) still awaiting required reviewer approvals."
            ]
        elif open_prs or (cf_only_mode and cf_summary["state"] == "OPEN"):
            open_cnt = len(open_prs) or cf_summary["open_count"]
            checks_3 = [
                f"Aún quedan {open_cnt} Pull Request(s) en estado OPEN pendientes de merge."
                if lang == "es" else
                f"{open_cnt} Pull Request(s) remain OPEN awaiting merge."
            ]
        else:
            checks_3 = [
                "Faltan las aprobaciones requeridas de revisores en los Pull Requests vinculados."
                if lang == "es" else
                "Linked Pull Requests are missing required reviewer approvals."
            ]
        criteria_items.append({
            "id": "3",
            "group": spec_3["group"],
            "title": spec_3["title"],
            "description": spec_3["description"],
            "status": "fail",
            "note": note_3,
            "checks": checks_3,
            "issues": [key],
        })

    # Item 4: Code pulled into master (PR merged)
    spec_4 = CRITERIA_SPECS["4"][lang]
    if pr_count > 0 and len(merged_prs) > 0 and len(open_prs) == 0:
        dest_branches = sorted({str((p.get("destination") or {}).get("branch") or "master") for p in merged_prs})
        note_4 = (
            f"{len(merged_prs)} Pull Request(s) integrados en {', '.join(dest_branches)}; sin Pull Requests abiertos."
            if lang == "es" else
            f"{len(merged_prs)} Pull Request(s) merged into {', '.join(dest_branches)}; 0 open PRs."
        )
        checks_4 = [
            (
                f"PR #{p.get('id')} ({p.get('repositoryName') or 'Bitbucket'}) integrado (MERGED) en la rama {(p.get('destination') or {}).get('branch', 'master')}."
                if lang == "es" else
                f"PR #{p.get('id')} ({p.get('repositoryName') or 'Bitbucket'}) merged into branch {(p.get('destination') or {}).get('branch', 'master')}."
            )
            for p in merged_prs
        ]
        criteria_items.append({
            "id": "4",
            "group": spec_4["group"],
            "title": spec_4["title"],
            "description": spec_4["description"],
            "status": "pass",
            "note": note_4,
            "checks": checks_4,
        })
    elif cf_only_mode and cf_summary["state"] == "MERGED":
        cf_cnt = cf_summary["count"]
        note_4 = (
            f"{cf_cnt} Pull Request(s) integrado(s) (MERGED) según el panel Development de Jira; sin Pull Requests abiertos."
            if lang == "es" else
            f"{cf_cnt} Pull Request(s) merged according to Jira Development panel; 0 open PRs."
        )
        checks_4 = [
            f"Confirmado en el panel Development de Jira: {cf_cnt} Pull Request(s) en estado MERGED."
            if lang == "es" else
            f"Confirmed via Jira Development panel: {cf_cnt} Pull Request(s) in MERGED state."
        ]
        criteria_items.append({
            "id": "4",
            "group": spec_4["group"],
            "title": spec_4["title"],
            "description": spec_4["description"],
            "status": "pass",
            "note": note_4,
            "checks": checks_4,
        })
    else:
        has_open_any = bool(open_prs) or (cf_only_mode and cf_summary["state"] == "OPEN")
        open_total = len(open_prs) if open_prs else cf_summary["open_count"]
        if lang == "es":
            reason_note = f"Hay {open_total} Pull Request(s) todavía en estado abierto (OPEN)." if has_open_any else "No se encontraron Pull Requests integrados (MERGED) en la rama principal."
        else:
            reason_note = f"{open_total} Pull Request(s) are still OPEN." if has_open_any else "No merged Pull Requests found."
        checks_4_prs = [
            (
                f"PR #{p.get('id')} ({p.get('repositoryName') or 'Bitbucket'}): estado actual {p.get('status')}."
                if lang == "es" else
                f"PR #{p.get('id')} ({p.get('repositoryName') or 'Bitbucket'}): current status {p.get('status')}."
            )
            for p in prs
        ]
        if not checks_4_prs and cf_only_mode:
            checks_4_prs = [
                f"El panel Development de Jira reporta {cf_summary['count']} Pull Request(s) con estado {cf_summary['state']}."
                if lang == "es" else
                f"Jira Development panel reports {cf_summary['count']} Pull Request(s) with state {cf_summary['state']}."
            ]
        if not checks_4_prs:
            checks_4_prs = (
                ["No hay Pull Requests integrados (MERGED) registrados para esta historia."]
                if lang == "es" else
                ["No merged Pull Requests recorded for this story."]
            )
        criteria_items.append({
            "id": "4",
            "group": spec_4["group"],
            "title": spec_4["title"],
            "description": spec_4["description"],
            "status": "fail",
            "note": reason_note,
            "checks": checks_4_prs,
            "issues": [key],
        })
        if has_open_any:
            act_text_4 = (
                f"Integrar (merge) o cerrar los Pull Requests abiertos vinculados a {key}."
                if lang == "es" else
                f"Merge or decline the open Pull Requests linked to {key}."
            )
            actions.append({
                "group": "fix",
                "criterion": "4",
                "text": act_text_4,
                "issues": [key],
            })

    # Item 5: Pull request approved by 1 different person than author (2 for unity)
    spec_5 = CRITERIA_SPECS["5"][lang]
    if pr_count > 0 and all_prs_properly_approved:
        note_5 = (
            "Todos los Pull Requests cumplen con la cantidad de aprobadores requeridos distintos al autor."
            if lang == "es" else
            "All Pull Requests meet repository-specific non-author approval requirements."
        )
        criteria_items.append({
            "id": "5",
            "group": spec_5["group"],
            "title": spec_5["title"],
            "description": spec_5["description"],
            "status": "pass",
            "note": note_5,
            "checks": pr_approval_checks,
        })
    elif cf_only_mode:
        is_unity_story = "[unity]" in summary.lower()
        req_app_hint = 2 if is_unity_story else 1
        note_5 = (
            f"Verificación manual requerida en Bitbucket: confirmar al menos {req_app_hint} aprobador(es) distinto(s) al autor en los {cf_summary['count']} PR(s)."
            if lang == "es" else
            f"Manual verification required in Bitbucket: confirm at least {req_app_hint} non-author approver(s) on the {cf_summary['count']} PR(s)."
        )
        checks_5 = [
            (
                f"Se registran {cf_summary['count']} Pull Request(s) en estado {cf_summary['state']} en el panel Development de Jira."
                if lang == "es" else
                f"{cf_summary['count']} Pull Request(s) in {cf_summary['state']} state are recorded in the Jira Development panel."
            ),
            (
                "Para validar automáticamente los nombres de revisores y la regla de 2 aprobadores en unity, configure JIRA_API_TOKEN en su archivo .env."
                if lang == "es" else
                "To automatically validate reviewer names and the unity 2-approver rule, configure JIRA_API_TOKEN in your .env file."
            ),
        ]
        criteria_items.append({
            "id": "5",
            "group": spec_5["group"],
            "title": spec_5["title"],
            "description": spec_5["description"],
            "status": "manual",
            "reason": "tool-unavailable",
            "note": note_5,
            "checks": checks_5,
            "issues": [key],
        })
        act_text_5_manual = (
            f"Verificar en Bitbucket que los {cf_summary['count']} Pull Request(s) de {key} cuenten con las aprobaciones requeridas (2 para repositorio unity, 1 para el resto)."
            if lang == "es" else
            f"Verify in Bitbucket that the {cf_summary['count']} Pull Request(s) of {key} have the required approvals (2 for unity repo, 1 for others)."
        )
        actions.append({
            "group": "manual",
            "criterion": "5",
            "text": act_text_5_manual,
            "issues": [key],
        })
    else:
        note_5 = (
            "; ".join(pr_approval_failures)
            if pr_approval_failures
            else ("No hay Pull Requests aprobados por los revisores requeridos." if lang == "es" else "No Pull Requests approved by required reviewers.")
        )
        no_approved_msg_5 = (
            "No hay Pull Requests aprobados por los revisores requeridos."
            if lang == "es" else
            "No Pull Requests approved by required reviewers."
        )
        if prs:
            checks_5 = ([no_approved_msg_5] if not pr_approval_checks else []) + pr_approval_checks + pr_approval_failures
        else:
            checks_5 = [no_approved_msg_5]
        criteria_items.append({
            "id": "5",
            "group": spec_5["group"],
            "title": spec_5["title"],
            "description": spec_5["description"],
            "status": "fail",
            "note": note_5,
            "checks": checks_5,
            "issues": [key],
        })
        if pr_approval_failures or has_any_pr:
            act_text_5 = (
                f"Completar las aprobaciones de code review requeridas en los Pull Requests de {key} (2 revisores distintos al autor para el repositorio unity, 1 para el resto)."
                if lang == "es" else
                f"Complete required reviewer approvals on the Pull Requests of {key} (2 non-author approvers for unity repo, 1 for others)."
            )
            actions.append({
                "group": "fix",
                "criterion": "5",
                "text": act_text_5,
                "issues": [key],
            })

    # Item 6: Green pipeline: unit tests + static analysis (build has passed)
    spec_6 = CRITERIA_SPECS["6"][lang]
    note_6 = (
        "La integración Jira-Jenkins aún no está activa; verificar en Jenkins que el build para los PR(s) de esta story haya pasado."
        if lang == "es" else
        "Jira-Jenkins integration is not yet active; verify in Jenkins that the build for this story's PR(s) passed."
    )
    checks_6 = [
        (
            "Los builds oficiales se ejecutan en Jenkins (los pipelines de Bitbucket no son la evidencia oficial de auditoría)."
            if lang == "es" else
            "Official builds are executed in Jenkins (Bitbucket pipelines are not the official audit evidence)."
        ),
        (
            "Verificación manual requerida hasta que se complete la integración del plugin Jira-Jenkins."
            if lang == "es" else
            "Manual verification required until the Jira-Jenkins plugin integration is completed."
        ),
    ]
    criteria_items.append({
        "id": "6",
        "group": spec_6["group"],
        "title": spec_6["title"],
        "description": spec_6["description"],
        "status": "manual",
        "reason": "by-design",
        "note": note_6,
        "checks": checks_6,
        "issues": [key],
    })
    act_text_6 = (
        f"Confirmar en Jenkins que el build oficial (pruebas unitarias + análisis estático) pasó con éxito para {key}."
        if lang == "es" else
        f"Confirm in Jenkins that the official build (unit tests + static analysis) passed for {key}."
    )
    actions.append({
        "group": "manual",
        "criterion": "6",
        "text": act_text_6,
        "issues": [key],
    })

    # -----------------------------------------------------------------------
    # Group B: UX & Figma Design (Items 7 - 8)
    # -----------------------------------------------------------------------

    design_req_field_id = fields_map.get("design required")
    figma_url_field_id = fields_map.get("figma url") or fields_map.get("design / figma reference")
    design_cf_val = fields.get("customfield_10086")

    formal_design_req = str(fields.get(design_req_field_id) or "") if design_req_field_id else ""
    formal_figma_url = str(fields.get(figma_url_field_id) or "") if figma_url_field_id else ""

    figma_urls: List[str] = []
    if formal_figma_url and "figma.com" in formal_figma_url.lower():
        figma_urls.append(formal_figma_url)
    if design_cf_val:
        for m in FIGMA_URL_RE.findall(json.dumps(design_cf_val)):
            if m not in figma_urls:
                figma_urls.append(m)
    for rl in remote_links:
        u = str(((rl.get("object") or {}).get("url")) or "")
        if "figma.com" in u.lower() and u not in figma_urls:
            figma_urls.append(u)
    for m in FIGMA_URL_RE.findall(all_text):
        if m not in figma_urls:
            figma_urls.append(m)

    no_figma_declared = (
        formal_design_req.strip().lower() == "no"
        or "no design required" in formal_figma_url.lower()
        or bool(NO_FIGMA_RE.search(all_text))
        or bool(NON_UI_PREFIX_RE.search(summary))
    )

    # Item 7: Figma linked (design)
    spec_7 = CRITERIA_SPECS["7"][lang]
    if figma_urls:
        note_7 = (
            f"Enlace de diseño Figma encontrado ({len(figma_urls)} URL(s))."
            if lang == "es" else
            f"Figma design link found ({len(figma_urls)} URL(s))."
        )
        checks_7 = [
            (f"URL de Figma detectada: {u}" if lang == "es" else f"Detected Figma URL: {u}")
            for u in figma_urls
        ]
        criteria_items.append({
            "id": "7",
            "group": spec_7["group"],
            "title": spec_7["title"],
            "description": spec_7["description"],
            "status": "pass",
            "note": note_7,
            "checks": checks_7,
            "evidence": figma_urls,
        })
    elif no_figma_declared:
        note_7 = (
            "No se requiere diseño de UI/Figma para esta historia (excepción o alcance de backend identificado)."
            if lang == "es" else
            "No UI/Figma design required for this story (exception or backend scope identified)."
        )
        checks_7 = [
            "Confirmada declaración explícita 'No Figma/Design Required' o alcance de componente backend sin interfaz."
            if lang == "es" else
            "Confirmed explicit 'No Figma/Design Required' declaration or non-UI backend component scope."
        ]
        criteria_items.append({
            "id": "7",
            "group": spec_7["group"],
            "title": spec_7["title"],
            "description": spec_7["description"],
            "status": "pass",
            "note": note_7,
            "checks": checks_7,
        })
    else:
        note_7 = (
            "No hay URL de Figma vinculada ni declaración de 'No Design Required'."
            if lang == "es" else
            "No Figma URL linked and no 'No Design Required' declaration found."
        )
        checks_7 = [
            (
                "Se revisaron el campo Design, enlaces externos, descripción y comentarios: no se encontró URL de Figma."
                if lang == "es" else
                "Checked the Design field, remote links, description, and comments: no Figma URL found."
            ),
            (
                "No se encontró declaración de 'No Figma Required' / 'No Design Required'."
                if lang == "es" else
                "No 'No Figma Required' / 'No Design Required' statement found."
            ),
        ]
        criteria_items.append({
            "id": "7",
            "group": spec_7["group"],
            "title": spec_7["title"],
            "description": spec_7["description"],
            "status": "fail",
            "note": note_7,
            "checks": checks_7,
            "issues": [key],
        })
        act_text_7 = (
            f"Vincular la URL de diseño de Figma a {key} o documentar 'No Design Required' si no aplican cambios de interfaz."
            if lang == "es" else
            f"Link the Figma design URL to {key} or document 'No Design Required' if no UI changes apply."
        )
        actions.append({
            "group": "fix",
            "criterion": "7",
            "text": act_text_7,
            "issues": [key],
        })

    # Item 8: Implemented UI design verified against latest approved baseline
    spec_8 = CRITERIA_SPECS["8"][lang]
    if no_figma_declared and not figma_urls:
        note_8 = (
            "No aplica (esta historia no requiere cambios de diseño de interfaz)."
            if lang == "es" else
            "Not applicable (no UI design changes required for this story)."
        )
        checks_8 = [
            "La historia no involucra cambios de interfaz gráfica."
            if lang == "es" else
            "Story does not involve UI design changes."
        ]
        criteria_items.append({
            "id": "8",
            "group": spec_8["group"],
            "title": spec_8["title"],
            "description": spec_8["description"],
            "status": "pass",
            "note": note_8,
            "checks": checks_8,
        })
    else:
        ux_summary_terms = (
            "ux validation", "ux verification", "[uxui]", "[ux/ui]",
            "ux/ui", "uxui", "ui/ux", "updating figma", "figma",
        )
        doc_summary_terms = ("[doc]", "documentation", "user manual", "architecture", "scmp", "soup")
        ux_subtasks: List[Dict[str, str]] = []
        for st in subtasks:
            s_low = st["summary"].lower()
            is_doc_st = any(dw in s_low for dw in doc_summary_terms)
            has_ux_title = any(uw in s_low for uw in ux_summary_terms) or bool(re.search(r"\bux\b", s_low))
            has_ux_assignee = is_same_person(st.get("assignee", ""), "Belen Yacoub") or is_same_person(s_low, "Belen Yacoub")
            if (has_ux_title or has_ux_assignee) and not is_doc_st:
                ux_subtasks.append(st)

        invalid_ux_resolutions = {"won't do", "wont do", "duplicate", "cannot reproduce", "cancelled", "canceled", "declined", "rejected"}
        completed_ux_subtasks = [
            st for st in ux_subtasks
            if st["status"].lower() in ("resolved", "closed", "done")
            and st.get("resolution", "").strip().lower() not in invalid_ux_resolutions
        ]
        open_ux_subtasks = [
            st for st in ux_subtasks
            if st["status"].lower() not in ("resolved", "closed", "done")
        ]

        if completed_ux_subtasks and not open_ux_subtasks:
            st_labels = ", ".join(f"{st['key']} ({st['status']})" for st in completed_ux_subtasks)
            note_8 = (
                f"Diseño de UI verificado mediante subtarea(s) de validación UX completada(s): {st_labels}."
                if lang == "es" else
                f"Implemented UI design verified via completed UX validation sub-task(s): {st_labels}."
            )
            checks_8 = []
            for ux_st in completed_ux_subtasks:
                res_info = f", resolución: {ux_st['resolution']}" if (lang == "es" and ux_st.get("resolution")) else (
                    f", resolution: {ux_st['resolution']}" if ux_st.get("resolution") else ""
                )
                ass_info = f", responsable: {ux_st['assignee']}" if (lang == "es" and ux_st.get("assignee")) else (
                    f", assignee: {ux_st['assignee']}" if ux_st.get("assignee") else ""
                )
                checks_8.append(
                    f"Subtarea de UX completada: {ux_st['key']} — {ux_st['summary']} (estado: {ux_st['status']}{res_info}{ass_info})."
                    if lang == "es" else
                    f"Completed UX sub-task: {ux_st['key']} — {ux_st['summary']} (status: {ux_st['status']}{res_info}{ass_info})."
                )
            criteria_items.append({
                "id": "8",
                "group": spec_8["group"],
                "title": spec_8["title"],
                "description": spec_8["description"],
                "status": "pass",
                "note": note_8,
                "checks": checks_8,
            })
        else:
            note_8 = (
                "Requiere confirmación visual de UX/QA para validar que la interfaz implementada coincide con el diseño aprobado en Figma."
                if lang == "es" else
                "Requires visual UX/QA confirmation that the implemented UI matches the approved Figma baseline."
            )
            checks_8 = [
                "Confirmar visualmente con el equipo de UX/QA que la pantalla implementada respeta la última versión aprobada en Figma."
                if lang == "es" else
                "Confirm visually with UX/QA that the implemented screen matches the latest approved Figma design."
            ]
            for ux_st in ux_subtasks:
                checks_8.append(
                    f"Subtarea de UX registrada en la historia: {ux_st['key']} — {ux_st['summary']} (estado: {ux_st['status']})."
                    if lang == "es" else
                    f"UX sub-task recorded on the story: {ux_st['key']} — {ux_st['summary']} (status: {ux_st['status']})."
                )
            criteria_items.append({
                "id": "8",
                "group": spec_8["group"],
                "title": spec_8["title"],
                "description": spec_8["description"],
                "status": "manual",
                "reason": "by-design",
                "note": note_8,
                "checks": checks_8,
                "issues": [key],
            })
            act_text_8 = (
                f"Confirmar que la interfaz implementada para {key} coincida con la última línea base aprobada en Figma."
                if lang == "es" else
                f"Confirm that the implemented UI for {key} matches the latest approved Figma baseline."
            )
            actions.append({
                "group": "manual",
                "criterion": "8",
                "text": act_text_8,
                "issues": [key],
            })

    # -----------------------------------------------------------------------
    # Group C: Traceability & Requirements (Items 9 - 11)
    # -----------------------------------------------------------------------

    # Item 9: Acceptance criteria in the Story description verified by at least one system test case
    spec_9 = CRITERIA_SPECS["9"][lang]
    covered_acs = [c for c in ac_coverage if c["covered"]]
    if ac_items and zephyr_tcs and len(covered_acs) == len(ac_items):
        note_9 = (
            f"Los {len(ac_items)} Criterios de Aceptación de la historia están cubiertos por casos de prueba en Zephyr."
            if lang == "es" else
            f"All {len(ac_items)} Acceptance Criteria items are covered by Zephyr test cases."
        )
        checks_9 = [
            (
                f"{c['ac_id']} verificado por {', '.join(c['matched_tcs'])}: {c['ac_text'][:90]}"
                if lang == "es" else
                f"{c['ac_id']} verified by {', '.join(c['matched_tcs'])}: {c['ac_text'][:90]}"
            )
            for c in ac_coverage
        ]
        criteria_items.append({
            "id": "9",
            "group": spec_9["group"],
            "title": spec_9["title"],
            "description": spec_9["description"],
            "status": "pass",
            "note": note_9,
            "checks": checks_9,
        })
    elif ac_items and zephyr_tcs and len(covered_acs) > 0:
        warn_9 = (
            f"{len(covered_acs)} de {len(ac_items)} criterios asociados de forma directa"
            if lang == "es" else
            f"{len(covered_acs)} of {len(ac_items)} criteria directly matched"
        )
        note_9 = (
            f"Se identificaron {len(ac_items)} Criterios de Aceptación y {len(zephyr_tcs)} Caso(s) de Prueba asociados al Requerimiento ({len(covered_acs)} de {len(ac_items)} vinculados directamente por contenido)."
            if lang == "es" else
            f"Identified {len(ac_items)} Acceptance Criteria and {len(zephyr_tcs)} Test Case(s) on the linked Requirement ({len(covered_acs)} of {len(ac_items)} directly matched by content)."
        )
        checks_9 = [
            (
                f"Se identificaron {len(ac_items)} Criterios de Aceptación en la descripción de la historia."
                if lang == "es" else
                f"Identified {len(ac_items)} Acceptance Criteria in the Story description."
            ),
            (
                f"El Requerimiento vinculado cuenta con {len(zephyr_tcs)} Caso(s) de Prueba en Zephyr."
                if lang == "es" else
                f"The linked Requirement has {len(zephyr_tcs)} Zephyr Test Case(s)."
            ),
        ]
        criteria_items.append({
            "id": "9",
            "group": spec_9["group"],
            "title": spec_9["title"],
            "description": spec_9["description"],
            "status": "pass",
            "warning": warn_9,
            "note": note_9,
            "checks": checks_9,
        })
    elif not ac_items:
        note_9 = (
            "La descripción de la historia no incluye una lista explícita de Criterios de Aceptación."
            if lang == "es" else
            "No explicit Acceptance Criteria section or items found in the Story description."
        )
        checks_9 = [
            "Se revisó la descripción de la historia en Jira y no se encontraron Criterios de Aceptación detallados."
            if lang == "es" else
            "Reviewed the Story description in Jira and found no detailed Acceptance Criteria list."
        ]
        criteria_items.append({
            "id": "9",
            "group": spec_9["group"],
            "title": spec_9["title"],
            "description": spec_9["description"],
            "status": "fail",
            "note": note_9,
            "checks": checks_9,
            "issues": [key],
        })
        act_text_9 = (
            f"Documentar los Criterios de Aceptación en la descripción de {key} y vincular sus casos de prueba en Zephyr."
            if lang == "es" else
            f"Document clear Acceptance Criteria in the description of {key} and link their test cases in Zephyr."
        )
        actions.append({
            "group": "fix",
            "criterion": "9",
            "text": act_text_9,
            "issues": [key],
        })
    elif not zephyr.available:
        note_9 = (
            f"Se identificaron {len(ac_items)} Criterios de Aceptación en la historia; requiere confirmar manualmente su cobertura en Zephyr."
            if lang == "es" else
            f"Identified {len(ac_items)} Acceptance Criteria in the story; manual confirmation of test coverage in Zephyr is required."
        )
        checks_9 = [
            (
                f"Se encontraron {len(ac_items)} Criterios de Aceptación en la descripción de la historia:"
                if lang == "es" else
                f"Found {len(ac_items)} Acceptance Criteria in the Story description:"
            )
        ] + [f"{a['id']}: {a['text'][:100]}" for a in ac_items[:5]]
        criteria_items.append({
            "id": "9",
            "group": spec_9["group"],
            "title": spec_9["title"],
            "description": spec_9["description"],
            "status": "manual",
            "reason": "tool-unavailable",
            "note": note_9,
            "checks": checks_9,
            "issues": [key],
        })
        act_text_9 = (
            f"Verificar en Zephyr que los {len(ac_items)} Criterios de Aceptación de {key} estén cubiertos por casos de prueba en su Requerimiento."
            if lang == "es" else
            f"Verify in Zephyr that the {len(ac_items)} Acceptance Criteria of {key} are covered by system test cases on its Requirement."
        )
        actions.append({
            "group": "manual",
            "criterion": "9",
            "text": act_text_9,
            "issues": [key],
        })
    else:
        note_9 = (
            f"La historia define {len(ac_items)} Criterio(s) de Aceptación, pero no hay Casos de Prueba vinculados en Zephyr."
            if lang == "es" else
            f"Story defines {len(ac_items)} Acceptance Criteria, but 0 Test Cases are linked in Zephyr."
        )
        checks_9 = [
            f"Se identificaron {len(ac_items)} Criterio(s) de Aceptación en la descripción, pero el Requerimiento y la historia no registran casos de prueba vinculados en Zephyr."
            if lang == "es" else
            f"Identified {len(ac_items)} Acceptance Criteria in the description, but the Requirement and Story have 0 linked test cases in Zephyr."
        ]
        criteria_items.append({
            "id": "9",
            "group": spec_9["group"],
            "title": spec_9["title"],
            "description": spec_9["description"],
            "status": "fail",
            "note": note_9,
            "checks": checks_9,
            "issues": [key],
        })
        act_text_9 = (
            f"Vincular en Zephyr los casos de prueba correspondientes al Requerimiento de {key} para cubrir sus Criterios de Aceptación."
            if lang == "es" else
            f"Link the corresponding Zephyr system test cases to the Requirement of {key} to cover its Acceptance Criteria."
        )
        actions.append({
            "group": "fix",
            "criterion": "9",
            "text": act_text_9,
            "issues": [key],
        })

    # Item 10: Links kept among Requirement, Story, Tests, and Bugs (Story -> Req; Req -> TC)
    spec_10 = CRITERIA_SPECS["10"][lang]
    if linked_reqs:
        req_keys_only = ", ".join(r["key"] for r in linked_reqs)
        if zephyr_tcs or linked_tests_jira:
            tc_count = len(zephyr_tcs) + len(linked_tests_jira)
            note_10 = (
                f"Trazabilidad completa: historia vinculada al Requerimiento {req_keys_only} con {tc_count} Caso(s) de Prueba asociado(s)."
                if lang == "es" else
                f"Full traceability: Story linked to Requirement {req_keys_only} with {tc_count} associated Test Case(s)."
            )
            checks_10 = [
                (
                    f"Vínculo Story -> Requerimiento verificado: {req_keys_only}."
                    if lang == "es" else
                    f"Story -> Requirement link verified: {req_keys_only}."
                ),
                (
                    f"Vínculo Requerimiento -> Casos de Prueba verificado: {tc_count} caso(s) de prueba en Zephyr."
                    if lang == "es" else
                    f"Requirement -> Test Case link verified: {tc_count} test case(s) in Zephyr."
                ),
            ]
            criteria_items.append({
                "id": "10",
                "group": spec_10["group"],
                "title": spec_10["title"],
                "description": spec_10["description"],
                "status": "pass",
                "note": note_10,
                "checks": checks_10,
            })
        elif not zephyr.available:
            warn_10 = (
                "Cobertura en Zephyr pendiente de verificación manual"
                if lang == "es" else
                "Zephyr coverage pending manual verification"
            )
            note_10 = (
                f"Vínculo Story -> Requerimiento verificado ({req_keys_only}). Confirmar manualmente en Zephyr los casos de prueba asociados al Requerimiento."
                if lang == "es" else
                f"Story -> Requirement link verified ({req_keys_only}). Confirm associated test cases on the Requirement in Zephyr manually."
            )
            checks_10 = [
                (
                    f"Confirmado que la historia {key} está vinculada al Requerimiento: {req_keys_only}."
                    if lang == "es" else
                    f"Confirmed Story {key} is linked to Requirement: {req_keys_only}."
                ),
                (
                    "La consulta automática a Zephyr no estuvo disponible en esta ejecución para verificar el tramo Requerimiento -> Casos de Prueba."
                    if lang == "es" else
                    "Automated Zephyr query was unavailable in this run to verify the Requirement -> Test Case link."
                ),
            ]
            criteria_items.append({
                "id": "10",
                "group": spec_10["group"],
                "title": spec_10["title"],
                "description": spec_10["description"],
                "status": "pass",
                "warning": warn_10,
                "note": note_10,
                "checks": checks_10,
            })
        else:
            note_10 = (
                f"La historia está vinculada al Requerimiento {req_keys_only}, pero dicho Requerimiento no tiene Casos de Prueba asociados en Zephyr."
                if lang == "es" else
                f"Story is linked to Requirement {req_keys_only}, but that Requirement has 0 linked Test Cases in Zephyr."
            )
            checks_10 = [
                (
                    f"Vínculo Story -> Requerimiento verificado ({req_keys_only})."
                    if lang == "es" else
                    f"Story -> Requirement link verified ({req_keys_only})."
                ),
                (
                    f"El Requerimiento {req_keys_only} no registra Casos de Prueba vinculados en Zephyr."
                    if lang == "es" else
                    f"Requirement {req_keys_only} has 0 linked Test Cases in Zephyr."
                ),
            ]
            criteria_items.append({
                "id": "10",
                "group": spec_10["group"],
                "title": spec_10["title"],
                "description": spec_10["description"],
                "status": "fail",
                "note": note_10,
                "checks": checks_10,
                "issues": [key] + [r["key"] for r in linked_reqs],
            })
            act_text_10 = (
                f"Vincular en Zephyr los Casos de Prueba correspondientes al Requerimiento asociado a {key}."
                if lang == "es" else
                f"Link the corresponding Zephyr Test Cases to the Requirement associated with {key}."
            )
            actions.append({
                "group": "fix",
                "criterion": "10",
                "text": act_text_10,
                "issues": [key],
            })
    else:
        note_10 = (
            f"La historia {key} no tiene ningún Requerimiento (Req) vinculado en Jira."
            if lang == "es" else
            f"Story {key} has no Requirement (Req) linked in Jira."
        )
        checks_10 = [
            f"Se revisaron las relaciones de la historia {key} en Jira: no se encontró ningún ticket de tipo Requerimiento (Req) asociado."
            if lang == "es" else
            f"Reviewed issue links of {key} in Jira: no Requirement (Req) issue found."
        ]
        criteria_items.append({
            "id": "10",
            "group": spec_10["group"],
            "title": spec_10["title"],
            "description": spec_10["description"],
            "status": "fail",
            "note": note_10,
            "checks": checks_10,
            "issues": [key],
        })
        act_text_10 = (
            f"Vincular en Jira la historia {key} con su Requerimiento (Req) correspondiente."
            if lang == "es" else
            f"Link Story {key} to its corresponding Requirement (Req) in Jira."
        )
        actions.append({
            "group": "fix",
            "criterion": "10",
            "text": act_text_10,
            "issues": [key],
        })

    # Item 11: No Critical nor Major issues related to the Story remain open (S4/S5)
    spec_11 = CRITERIA_SPECS["11"][lang]
    open_crit_bugs = [
        b for b in linked_bugs
        if b["status"].lower() not in ("closed", "resolved", "done", "verified")
        and any(k in b["priority"].lower() for k in ("critical", "high", "highest", "blocker", "major", "s4", "s5"))
    ]
    open_any_bugs = [
        b for b in linked_bugs
        if b["status"].lower() not in ("closed", "resolved", "done", "verified")
    ]
    if open_crit_bugs:
        bug_str = ", ".join(f"{b['key']} ({b['priority']}, {b['status']})" for b in open_crit_bugs)
        note_11 = (
            f"Hay {len(open_crit_bugs)} bug(s) de severidad Crítica/Alta abiertos vinculados a la historia: {bug_str}."
            if lang == "es" else
            f"{len(open_crit_bugs)} Critical/High bug(s) remain open on the story: {bug_str}."
        )
        checks_11 = [
            (
                f"Defecto Crítico/Alto abierto: {b['key']} — {b['summary']} (prioridad: {b['priority']}, estado: {b['status']})."
                if lang == "es" else
                f"Open Critical/High defect: {b['key']} — {b['summary']} (priority: {b['priority']}, status: {b['status']})."
            )
            for b in open_crit_bugs
        ]
        criteria_items.append({
            "id": "11",
            "group": spec_11["group"],
            "title": spec_11["title"],
            "description": spec_11["description"],
            "status": "fail",
            "note": note_11,
            "checks": checks_11,
            "issues": [key] + [b["key"] for b in open_crit_bugs],
        })
        act_text_11 = (
            f"Resolver y cerrar los defectos Críticos/Altos abiertos vinculados a {key}: {', '.join(b['key'] for b in open_crit_bugs)}."
            if lang == "es" else
            f"Resolve and close open Critical/High defect(s) linked to {key}: {', '.join(b['key'] for b in open_crit_bugs)}."
        )
        actions.append({
            "group": "fix",
            "criterion": "11",
            "text": act_text_11,
            "issues": [key] + [b["key"] for b in open_crit_bugs],
        })
    else:
        note_11 = (
            f"No hay bugs Críticos ni Altos abiertos vinculados a {key} ({len(linked_bugs)} bug(s) vinculados en total, {len(open_any_bugs)} menores abiertos)."
            if lang == "es" else
            f"No open Critical/High bugs linked to {key} ({len(linked_bugs)} total linked bug(s), {len(open_any_bugs)} open minor)."
        )
        checks_11 = [
            "Se revisaron todos los defectos vinculados a la historia: ninguno de severidad Crítica o Alta (S4/S5) permanece abierto."
            if lang == "es" else
            "Reviewed all linked Bug issues: no Critical or High (S4/S5) defects remain open."
        ]
        criteria_items.append({
            "id": "11",
            "group": spec_11["group"],
            "title": spec_11["title"],
            "description": spec_11["description"],
            "status": "pass",
            "note": note_11,
            "checks": checks_11,
        })


    # -----------------------------------------------------------------------
    # Group D: Testing & Zephyr (Items 12 - 15)
    # -----------------------------------------------------------------------

    if not zephyr.available:
        manual_actions_spec = {
            "12": {
                "en": f"Confirm in Zephyr that test cases were created or updated for {key}.",
                "es": f"Confirmar en Zephyr que los casos de prueba fueron creados o actualizados para {key}.",
            },
            "13": {
                "en": f"Confirm in Zephyr that all test cases for {key} are in 'Approved' status.",
                "es": f"Confirmar en Zephyr que todos los casos de prueba de {key} estén en estado 'Approved'.",
            },
            "14": {
                "en": f"Confirm in Zephyr that test cases for {key} were executed and passed on pre-staging.",
                "es": f"Confirmar en Zephyr que los casos de prueba de {key} fueron ejecutados con resultado aprobado en pre-staging.",
            },
            "15": {
                "en": f"Verify that test execution evidence is recorded in Zephyr for {key}.",
                "es": f"Verificar que la evidencia de ejecución de pruebas esté registrada en Zephyr para {key}.",
            },
        }
        for cid in ("12", "13", "14", "15"):
            spec_z = CRITERIA_SPECS[cid][lang]
            note_z = (
                "La consulta automática a Zephyr no estuvo disponible en esta ejecución; requiere verificación manual en Zephyr."
                if lang == "es" else
                "Automated Zephyr query was unavailable in this run; manual verification in Zephyr is required."
            )
            checks_z = [
                "Verificar directamente en Zephyr Scale el estado y las ejecuciones de los casos de prueba asociados al requerimiento."
                if lang == "es" else
                "Verify the status and executions of test cases associated with the requirement directly in Zephyr Scale."
            ]
            criteria_items.append({
                "id": cid,
                "group": spec_z["group"],
                "title": spec_z["title"],
                "description": spec_z["description"],
                "status": "manual",
                "reason": "tool-unavailable",
                "note": note_z,
                "checks": checks_z,
                "issues": [key],
            })
            actions.append({
                "group": "manual",
                "criterion": cid,
                "text": manual_actions_spec[cid][lang],
                "issues": [key],
            })
    else:
        # Shared Requirement Test Case Disambiguation (1 Requirement -> N Stories via Test Cycle names)
        active_all_tcs = [t for t in zephyr_tcs if str(t.get("status", "")).lower() != "deprecated"]
        deprecated_tcs = [t for t in zephyr_tcs if str(t.get("status", "")).lower() == "deprecated"]
        confirmed_tcs = [t for t in active_all_tcs if t.get("story_scope") == "confirmed"]
        other_story_tcs = [t for t in active_all_tcs if t.get("story_scope") == "other_story"]
        unscoped_tcs = [t for t in active_all_tcs if t.get("story_scope") == "unscoped"]

        # Test cases applicable to this story (exclude TCs explicitly executed only in another story's test cycle)
        active_tcs = [t for t in active_all_tcs if t.get("story_scope") != "other_story"]

        # Item 12: Test cases created/updated in Jira/Zephyr
        spec_12 = CRITERIA_SPECS["12"][lang]
        if active_tcs or deprecated_tcs:
            shown_tcs = active_tcs if active_tcs else deprecated_tcs
            tc_list_str = ", ".join(f"{t['key']} ({t['status']})" for t in shown_tcs)
            note_12 = (
                f"Se encontraron {len(shown_tcs)} Caso(s) de Prueba en Zephyr aplicables a {key}: {tc_list_str}."
                if lang == "es" else
                f"Found {len(shown_tcs)} Zephyr Test Case(s) applicable to {key}: {tc_list_str}."
            )
            checks_12 = [
                (
                    f"{t['key']} (vinculado vía {t.get('linked_via')}): {t['name']} — estado: {t['status']}."
                    if lang == "es" else
                    f"{t['key']} (linked via {t.get('linked_via')}): {t['name']} — status: {t['status']}."
                )
                for t in shown_tcs
            ]
            if other_story_tcs:
                other_desc = ", ".join(
                    f"{t['key']} ({'/'.join(t.get('other_story_keys') or [])})" for t in other_story_tcs
                )
                checks_12.append(
                    f"Se omitieron {len(other_story_tcs)} caso(s) de prueba del requerimiento compartido asignados en ciclos de otra historia: {other_desc}."
                    if lang == "es" else
                    f"Excluded {len(other_story_tcs)} test case(s) on the shared Requirement scoped to another story's Test Cycle: {other_desc}."
                )
            criteria_items.append({
                "id": "12",
                "group": spec_12["group"],
                "title": spec_12["title"],
                "description": spec_12["description"],
                "status": "pass",
                "note": note_12,
                "checks": checks_12,
            })
        elif other_story_tcs:
            other_desc = ", ".join(
                f"{t['key']} (ciclo de {'/'.join(t.get('other_story_keys') or [])})" for t in other_story_tcs
            )
            note_12 = (
                f"El Requerimiento vinculado cuenta con {len(other_story_tcs)} Caso(s) de Prueba en Zephyr, pero sus ciclos de prueba hacen referencia a otra historia ({other_desc})."
                if lang == "es" else
                f"The linked Requirement has {len(other_story_tcs)} Zephyr Test Case(s), but their Test Cycles reference another story ({other_desc})."
            )
            checks_12 = [
                f"Casos de prueba en el Requerimiento compartido asociados por ciclo a otra historia: {other_desc}."
                if lang == "es" else
                f"Test cases on the shared Requirement scoped by Test Cycle name to another story: {other_desc}."
            ]
            criteria_items.append({
                "id": "12",
                "group": spec_12["group"],
                "title": spec_12["title"],
                "description": spec_12["description"],
                "status": "fail",
                "note": note_12,
                "checks": checks_12,
                "issues": [key],
            })
            act_text_12 = (
                f"Vincular o incluir en un Test Cycle de {key} los Casos de Prueba de Zephyr correspondientes a esta historia."
                if lang == "es" else
                f"Link or include in a Test Cycle named with {key} the Zephyr Test Cases corresponding to this story."
            )
            actions.append({
                "group": "fix",
                "criterion": "12",
                "text": act_text_12,
                "issues": [key],
            })
        else:
            note_12 = (
                "No se encontraron Casos de Prueba en Zephyr asociados a la historia ni a su Requerimiento."
                if lang == "es" else
                "No Zephyr Test Cases found for the Story or its linked Requirement."
            )
            checks_12 = [
                "Se consultó Zephyr Scale para la historia y su Requerimiento vinculado: no hay casos de prueba registrados."
                if lang == "es" else
                "Checked Zephyr Scale for the Story and its linked Requirement: 0 test cases found."
            ]
            criteria_items.append({
                "id": "12",
                "group": spec_12["group"],
                "title": spec_12["title"],
                "description": spec_12["description"],
                "status": "fail",
                "note": note_12,
                "checks": checks_12,
                "issues": [key],
            })
            act_text_12 = (
                f"Crear o vincular en Zephyr los Casos de Prueba correspondientes al Requerimiento de {key}."
                if lang == "es" else
                f"Create or link the corresponding Zephyr Test Cases to the Requirement of {key}."
            )
            actions.append({
                "group": "fix",
                "criterion": "12",
                "text": act_text_12,
                "issues": [key],
            })

        # Item 13: Test cases reviewed and approved
        spec_13 = CRITERIA_SPECS["13"][lang]
        unapproved_tcs = [t for t in active_tcs if str(t.get("status", "")).lower() != "approved"]
        unapproved_confirmed = [t for t in confirmed_tcs if str(t.get("status", "")).lower() != "approved"]
        unapproved_unscoped = [t for t in unscoped_tcs if str(t.get("status", "")).lower() != "approved"]

        if active_tcs and not unapproved_tcs:
            note_13 = (
                f"Los {len(active_tcs)} Caso(s) de Prueba activos en Zephyr están revisados y aprobados ('Approved')."
                if lang == "es" else
                f"All {len(active_tcs)} active Zephyr Test Case(s) are reviewed and in 'Approved' status."
            )
            checks_13 = [
                (f"{t['key']} ({t['name']}): estado {t['status']}." if lang == "es" else f"{t['key']} ({t['name']}): status {t['status']}.")
                for t in active_tcs
            ]
            if other_story_tcs:
                other_keys = ", ".join(
                    f"{t['key']} ({'/'.join(t.get('other_story_keys') or [])})" for t in other_story_tcs
                )
                checks_13.append(
                    f"Se omitieron {len(other_story_tcs)} caso(s) de prueba asignados por Test Cycle a otra historia: {other_keys}."
                    if lang == "es" else
                    f"Ignored {len(other_story_tcs)} test case(s) assigned by Test Cycle to another story: {other_keys}."
                )
            if deprecated_tcs:
                dep_keys = ", ".join(t["key"] for t in deprecated_tcs)
                checks_13.append(
                    f"Se omitieron {len(deprecated_tcs)} caso(s) de prueba obsoletos (Deprecated): {dep_keys}."
                    if lang == "es" else
                    f"Ignored {len(deprecated_tcs)} deprecated test case(s): {dep_keys}."
                )
            criteria_items.append({
                "id": "13",
                "group": spec_13["group"],
                "title": spec_13["title"],
                "description": spec_13["description"],
                "status": "pass",
                "note": note_13,
                "checks": checks_13,
            })
        elif confirmed_tcs and not unapproved_confirmed and unapproved_unscoped:
            unscoped_str = ", ".join(f"{t['key']} ({t['status']})" for t in unapproved_unscoped)
            note_13 = (
                f"Los {len(confirmed_tcs)} Caso(s) de Prueba confirmados en ciclos de {key} están en estado 'Approved'. "
                f"Hay {len(unapproved_unscoped)} caso(s) en el Requerimiento compartido sin ciclo de esta historia ({unscoped_str}): confirmar con QA si pertenecen a otra historia."
                if lang == "es" else
                f"All {len(confirmed_tcs)} Test Case(s) confirmed in {key} Test Cycles are 'Approved'. "
                f"{len(unapproved_unscoped)} test case(s) on the shared Requirement have no cycle for this story ({unscoped_str}): confirm with QA if they belong to another story."
            )
            checks_13 = [
                (
                    f"{t['key']} ({t['name']}): estado {t['status']} — confirmado en ciclo de {key} ({', '.join(t.get('cycle_names') or []) or key})."
                    if lang == "es" else
                    f"{t['key']} ({t['name']}): status {t['status']} — confirmed in {key} cycle ({', '.join(t.get('cycle_names') or []) or key})."
                )
                for t in confirmed_tcs
            ]
            for t in unapproved_unscoped:
                checks_13.append(
                    f"⚠️ Revisión manual (Requerimiento compartido {t.get('linked_via')}): {t['key']} ({t['name']}) está en estado '{t['status']}' y aún no figura en un Test Cycle con el ID {key}. Confirmar si pertenece a otra historia."
                    if lang == "es" else
                    f"⚠️ Manual review (shared Requirement {t.get('linked_via')}): {t['key']} ({t['name']}) is in '{t['status']}' status and has no Test Cycle naming {key}. Confirm whether it belongs to another story."
                )
            if other_story_tcs:
                other_keys = ", ".join(
                    f"{t['key']} ({'/'.join(t.get('other_story_keys') or [])})" for t in other_story_tcs
                )
                checks_13.append(
                    f"Se omitieron {len(other_story_tcs)} caso(s) de prueba asignados por Test Cycle a otra historia: {other_keys}."
                    if lang == "es" else
                    f"Ignored {len(other_story_tcs)} test case(s) assigned by Test Cycle to another story: {other_keys}."
                )
            criteria_items.append({
                "id": "13",
                "group": spec_13["group"],
                "title": spec_13["title"],
                "description": spec_13["description"],
                "status": "manual",
                "reason": "by-design",
                "note": note_13,
                "checks": checks_13,
                "issues": [key],
            })
            act_text_13_manual = (
                f"Confirmar con QA si los casos de prueba {unscoped_str} (vinculados al Requerimiento compartido) pertenecen a otra historia o si deben aprobarse e incluirse en un Test Cycle de {key}."
                if lang == "es" else
                f"Confirm with QA whether test cases {unscoped_str} (linked to the shared Requirement) belong to another story or must be approved and added to a {key} Test Cycle."
            )
            actions.append({
                "group": "manual",
                "criterion": "13",
                "text": act_text_13_manual,
                "issues": [key],
            })
        else:
            if lang == "es":
                note_13 = (
                    f"{len(unapproved_tcs)} de {len(active_tcs)} Caso(s) de Prueba aún no están en estado 'Approved': "
                    + ", ".join(f"{t['key']} ({t['status']})" for t in unapproved_tcs)
                    if active_tcs
                    else "No hay casos de prueba vinculados a esta historia para verificar su aprobación."
                )
            else:
                note_13 = (
                    f"{len(unapproved_tcs)} of {len(active_tcs)} Test Case(s) are not yet 'Approved': "
                    + ", ".join(f"{t['key']} ({t['status']})" for t in unapproved_tcs)
                    if active_tcs
                    else "No linked Test Cases found for this story to verify approval status."
                )
            checks_13 = [
                (f"{t['key']} ({t['name']}): estado actual {t['status']}." if lang == "es" else f"{t['key']} ({t['name']}): current status {t['status']}.")
                for t in active_tcs
            ] or (
                ["No hay casos de prueba registrados en Zephyr para esta historia."]
                if lang == "es" else
                ["No test cases recorded in Zephyr for this story."]
            )
            criteria_items.append({
                "id": "13",
                "group": spec_13["group"],
                "title": spec_13["title"],
                "description": spec_13["description"],
                "status": "fail",
                "note": note_13,
                "checks": checks_13,
                "issues": [key],
            })
            if unapproved_tcs:
                act_text_13 = (
                    f"Revisar y pasar a estado 'Approved' los Casos de Prueba de Zephyr asociados a {key}: {', '.join(t['key'] for t in unapproved_tcs)}."
                    if lang == "es" else
                    f"Review and transition Zephyr Test Cases to 'Approved' for {key}: {', '.join(t['key'] for t in unapproved_tcs)}."
                )
                actions.append({
                    "group": "fix",
                    "criterion": "13",
                    "text": act_text_13,
                    "issues": [key],
                })

        # Item 14 & 15: Test cases executed on pre-staging & Evidence of test execution linked
        spec_14 = CRITERIA_SPECS["14"][lang]
        spec_15 = CRITERIA_SPECS["15"][lang]
        passed_tcs: List[Tuple[str, Dict[str, Any]]] = []
        failed_or_unexec_tcs: List[str] = []
        failed_confirmed_tcs: List[str] = []
        failed_unscoped_tcs: List[Dict[str, Any]] = []
        eval_tcs = active_tcs
        for t in eval_tcs:
            execs = t.get("executions") or []
            passed_ex = [e for e in execs if str(e.get("status", "")).lower() in ("pass", "passed")]
            if passed_ex:
                # Prefer an execution whose test_cycle_name explicitly mentions this story key
                story_ex = [e for e in passed_ex if story_key_upper in str(e.get("test_cycle_name") or "").upper()]
                chosen_ex = story_ex[0] if story_ex else passed_ex[0]
                passed_tcs.append((t["key"], chosen_ex))
            else:
                failed_or_unexec_tcs.append(t["key"])
                if t.get("story_scope") == "confirmed":
                    failed_confirmed_tcs.append(t["key"])
                else:
                    failed_unscoped_tcs.append(t)

        if eval_tcs and not failed_or_unexec_tcs:
            note_14 = (
                f"Los {len(eval_tcs)} Caso(s) de Prueba activos cuentan con ejecución aprobada en Zephyr."
                if lang == "es" else
                f"All {len(eval_tcs)} active Test Case(s) have passing executions in Zephyr."
            )
            checks_14 = [
                (
                    f"{tk}: última ejecución aprobada el {ex.get('date', 'N/A')} (entorno: {ex.get('environment') or 'pre-staging'}"
                    + (f", ciclo: {ex.get('test_cycle_name')}" if ex.get("test_cycle_name") else "")
                    + ")."
                    if lang == "es" else
                    f"{tk}: last passing execution on {ex.get('date', 'N/A')} (environment: {ex.get('environment') or 'pre-staging'}"
                    + (f", cycle: {ex.get('test_cycle_name')}" if ex.get("test_cycle_name") else "")
                    + ")."
                )
                for tk, ex in passed_tcs
            ]
            if other_story_tcs:
                other_keys = ", ".join(
                    f"{t['key']} ({'/'.join(t.get('other_story_keys') or [])})" for t in other_story_tcs
                )
                checks_14.append(
                    f"Se omitieron {len(other_story_tcs)} caso(s) de prueba ejecutados en Test Cycles de otra historia: {other_keys}."
                    if lang == "es" else
                    f"Ignored {len(other_story_tcs)} test case(s) executed in another story's Test Cycles: {other_keys}."
                )
            criteria_items.append({
                "id": "14",
                "group": spec_14["group"],
                "title": spec_14["title"],
                "description": spec_14["description"],
                "status": "pass",
                "note": note_14,
                "checks": checks_14,
            })
            note_15 = (
                f"Evidencia de ejecución verificada en Zephyr para los {len(eval_tcs)} Caso(s) de Prueba activos."
                if lang == "es" else
                f"Execution evidence verified in Zephyr for all {len(eval_tcs)} active Test Case(s)."
            )
            checks_15 = [
                (
                    f"Ejecución #{ex.get('id')} registrada para {tk} con resultado {ex.get('status')}"
                    + (f" ({ex.get('test_cycle_name')})." if ex.get("test_cycle_name") else ".")
                    if lang == "es" else
                    f"Execution #{ex.get('id')} recorded for {tk} with status {ex.get('status')}"
                    + (f" ({ex.get('test_cycle_name')})." if ex.get("test_cycle_name") else ".")
                )
                for tk, ex in passed_tcs
            ]
            criteria_items.append({
                "id": "15",
                "group": spec_15["group"],
                "title": spec_15["title"],
                "description": spec_15["description"],
                "status": "pass",
                "note": note_15,
                "checks": checks_15,
            })
        elif confirmed_tcs and not failed_confirmed_tcs and failed_unscoped_tcs:
            unscoped_exec_str = ", ".join(f"{t['key']} ({t['status']})" for t in failed_unscoped_tcs)
            note_manual_14 = (
                f"Los {len(confirmed_tcs)} Caso(s) de Prueba en los Test Cycles de {key} fueron ejecutados y aprobados (PASS). "
                f"Quedan {len(failed_unscoped_tcs)} caso(s) en el Requerimiento compartido sin ejecución en un ciclo de {key} ({unscoped_exec_str}): confirmar si pertenecen a otra historia."
                if lang == "es" else
                f"All {len(confirmed_tcs)} Test Case(s) in {key} Test Cycles have passing (PASS) executions. "
                f"{len(failed_unscoped_tcs)} test case(s) on the shared Requirement have no execution in a {key} cycle ({unscoped_exec_str}): confirm if they belong to another story."
            )
            checks_manual_14 = [
                (
                    f"{tk}: ejecución aprobada ({ex.get('status')}) el {ex.get('date', 'N/A')} en ciclo '{ex.get('test_cycle_name') or key}'."
                    if lang == "es" else
                    f"{tk}: passing execution ({ex.get('status')}) on {ex.get('date', 'N/A')} in cycle '{ex.get('test_cycle_name') or key}'."
                )
                for tk, ex in passed_tcs
            ]
            for t in failed_unscoped_tcs:
                checks_manual_14.append(
                    f"⚠️ Revisión manual (Requerimiento compartido {t.get('linked_via')}): {t['key']} ({t['name']}, estado: {t['status']}) no registra ejecuciones en un Test Cycle con el ID {key}. Confirmar con QA si corresponde a otra historia."
                    if lang == "es" else
                    f"⚠️ Manual review (shared Requirement {t.get('linked_via')}): {t['key']} ({t['name']}, status: {t['status']}) has no executions in a Test Cycle naming {key}. Confirm with QA if it belongs to another story."
                )
            criteria_items.append({
                "id": "14",
                "group": spec_14["group"],
                "title": spec_14["title"],
                "description": spec_14["description"],
                "status": "manual",
                "reason": "by-design",
                "note": note_manual_14,
                "checks": checks_manual_14,
                "issues": [key],
            })
            criteria_items.append({
                "id": "15",
                "group": spec_15["group"],
                "title": spec_15["title"],
                "description": spec_15["description"],
                "status": "manual",
                "reason": "by-design",
                "note": note_manual_14,
                "checks": checks_manual_14,
                "issues": [key],
            })
            act_text_14_manual = (
                f"Confirmar con QA si los casos de prueba sin ciclo de {key} ({unscoped_exec_str}) corresponden a otra historia que comparte el Requerimiento o si resta ejecutarlos para {key}."
                if lang == "es" else
                f"Confirm with QA whether the test cases without a {key} Test Cycle ({unscoped_exec_str}) belong to another story sharing the Requirement or still need execution for {key}."
            )
            actions.append({
                "group": "manual",
                "criterion": "14",
                "text": act_text_14_manual,
                "issues": [key],
            })
        else:
            missing_str = ", ".join(failed_or_unexec_tcs) if failed_or_unexec_tcs else ("ningún caso de prueba vinculado a esta historia" if lang == "es" else "no test cases linked to this story")
            note_fail = (
                f"Falta evidencia de ejecución aprobada (PASS) en Zephyr ({missing_str})."
                if lang == "es" else
                f"Missing passing (PASS) execution evidence in Zephyr ({missing_str})."
            )
            checks_fail = [
                (
                    f"Casos de prueba sin ejecución aprobada (PASS) en Zephyr: {missing_str}."
                    if failed_or_unexec_tcs else
                    "No hay casos de prueba vinculados en Zephyr para esta historia que permitan verificar registros de ejecución."
                )
                if lang == "es" else (
                    f"Test cases without a PASS execution in Zephyr: {missing_str}."
                    if failed_or_unexec_tcs else
                    "No linked test cases in Zephyr for this story to verify execution records."
                )
            ]
            criteria_items.append({
                "id": "14",
                "group": spec_14["group"],
                "title": spec_14["title"],
                "description": spec_14["description"],
                "status": "fail",
                "note": note_fail,
                "checks": checks_fail,
                "issues": [key],
            })
            criteria_items.append({
                "id": "15",
                "group": spec_15["group"],
                "title": spec_15["title"],
                "description": spec_15["description"],
                "status": "fail",
                "note": note_fail,
                "checks": checks_fail,
                "issues": [key],
            })
            act_text_14 = (
                f"Ejecutar los casos de prueba en pre-staging y registrar el resultado aprobado (PASS) en Zephyr para {key}."
                if lang == "es" else
                f"Execute test cases on pre-staging and record PASS results in Zephyr for {key}."
            )
            actions.append({
                "group": "fix",
                "criterion": "14",
                "text": act_text_14,
                "issues": [key],
            })

    # -----------------------------------------------------------------------
    # Group E: Technical Documentation & Governance (Items 16 - 20)
    # -----------------------------------------------------------------------

    doc_subtasks = [
        st for st in subtasks
        if any(w in st["summary"].lower() for w in ("doc", "architecture", "design", "api", "soup", "scmp", "notice"))
    ]
    open_doc_subtasks = [
        st for st in doc_subtasks
        if st["status"].lower() not in ("closed", "resolved", "done")
    ]
    drive_urls = DRIVE_URL_RE.findall(all_text)

    doc_items_spec = [
        ("16", "soup", "0GS-001 Software Configuration Management Plan", "Plan de Gestión de Configuración (0GS-001 SCMP)"),
        ("17", "architecture", "Product Architecture Document", "Documento de Arquitectura de Producto"),
        ("18", "design doc", "Product Design Document", "Documento de Diseño de Producto"),
        ("19", "api", "API Documentation", "Documentación de APIs"),
        ("20", "doc", "General Technical/User Documentation", "Documentación Técnica o Manual de Usuario"),
    ]

    for cid, kw, doc_label_en, doc_label_es in doc_items_spec:
        spec_doc = CRITERIA_SPECS[cid][lang]
        doc_label = doc_label_es if lang == "es" else doc_label_en
        matching_st = [st for st in doc_subtasks if kw in st["summary"].lower() or (cid == "20" and doc_subtasks)]
        open_matching_st = [st for st in matching_st if st["status"].lower() not in ("closed", "resolved", "done")]

        if matching_st and open_matching_st:
            st_keys = ", ".join(f"{s['key']} ({s['status']})" for s in open_matching_st)
            note_doc = (
                f"Subtarea(s) de documentación pendientes de cierre: {st_keys}."
                if lang == "es" else
                f"Documentation sub-task(s) still open: {st_keys}."
            )
            checks_doc = [
                (
                    f"Subtarea de documentación abierta: {s['key']} — {s['summary']} (estado: {s['status']})."
                    if lang == "es" else
                    f"Open documentation sub-task: {s['key']} — {s['summary']} (status: {s['status']})."
                )
                for s in open_matching_st
            ]
            criteria_items.append({
                "id": cid,
                "group": spec_doc["group"],
                "title": spec_doc["title"],
                "description": spec_doc["description"],
                "status": "fail",
                "note": note_doc,
                "checks": checks_doc,
                "issues": [key] + [s["key"] for s in open_matching_st],
            })
            act_text_doc = (
                f"Completar y cerrar la(s) subtarea(s) de documentación pendientes en {key}."
                if lang == "es" else
                f"Complete and close pending documentation sub-task(s) on {key}."
            )
            actions.append({
                "group": "fix",
                "criterion": cid,
                "text": act_text_doc,
                "issues": [key] + [s["key"] for s in open_matching_st],
            })
        elif matching_st and not open_matching_st:
            st_keys = ", ".join(f"{s['key']} ({s['status']})" for s in matching_st)
            note_doc = (
                f"Subtarea(s) de documentación completadas: {st_keys}."
                if lang == "es" else
                f"Documentation sub-task(s) completed: {st_keys}."
            )
            checks_doc = [
                (
                    f"Subtarea completada: {s['key']} — {s['summary']} (estado: {s['status']})."
                    if lang == "es" else
                    f"Completed sub-task: {s['key']} — {s['summary']} (status: {s['status']})."
                )
                for s in matching_st
            ]
            criteria_items.append({
                "id": cid,
                "group": spec_doc["group"],
                "title": spec_doc["title"],
                "description": spec_doc["description"],
                "status": "pass",
                "note": note_doc,
                "checks": checks_doc,
            })
        elif drive_urls and cid == "20":
            note_doc = (
                f"Documentación vinculada en Google Drive dentro de la historia ({len(drive_urls)} enlace(s))."
                if lang == "es" else
                f"Google Drive documentation link(s) referenced in story ({len(drive_urls)} link(s))."
            )
            checks_doc = [
                (f"Enlace de documentación en Google Drive: {u}" if lang == "es" else f"Google Drive documentation link: {u}")
                for u in drive_urls
            ]
            criteria_items.append({
                "id": cid,
                "group": spec_doc["group"],
                "title": spec_doc["title"],
                "description": spec_doc["description"],
                "status": "pass",
                "note": note_doc,
                "checks": checks_doc,
                "evidence": drive_urls,
            })
        else:
            warn_doc = (
                "No aplica según alcance de la historia"
                if lang == "es" else
                "Not applicable per story scope"
            )
            note_doc = (
                f"Sin subtareas pendientes para '{doc_label}' (no requerido salvo cambios específicos en ese alcance)."
                if lang == "es" else
                f"No pending sub-tasks for '{doc_label}' (not required unless scope changes apply)."
            )
            checks_doc = [
                (
                    f"Se revisaron las subtareas de {key}: no se registran tareas pendientes de '{doc_label}'."
                    if lang == "es" else
                    f"Reviewed sub-tasks of {key}: no pending tasks for '{doc_label}'."
                )
            ]
            criteria_items.append({
                "id": cid,
                "group": spec_doc["group"],
                "title": spec_doc["title"],
                "description": spec_doc["description"],
                "status": "pass",
                "warning": warn_doc,
                "note": note_doc,
                "checks": checks_doc,
            })

    # -----------------------------------------------------------------------
    # Group F: Story Resolution & Closure Governance (Item 21)
    # -----------------------------------------------------------------------

    spec_21 = CRITERIA_SPECS["21"][lang]
    changelog_histories = ((issue.get("changelog") or {}).get("histories")) or []
    resolved_by: Optional[str] = None
    resolved_date: Optional[str] = None
    closed_by: Optional[str] = None
    closed_date: Optional[str] = None

    for hist in changelog_histories:
        if not isinstance(hist, dict):
            continue
        author_disp = str((hist.get("author") or {}).get("displayName") or "Unknown")
        created_at = str(hist.get("created") or "")[:10]
        for item in hist.get("items") or []:
            if isinstance(item, dict) and str(item.get("field") or "").lower() == "status":
                to_str = str(item.get("toString") or "").lower()
                if to_str == "resolved":
                    resolved_by = author_disp
                    resolved_date = created_at
                elif to_str == "closed":
                    closed_by = author_disp
                    closed_date = created_at

    is_resolved_or_closed = status_name.lower() in ("resolved", "closed")
    is_res_done = resolution_name.lower() == "done"

    if is_resolved_or_closed and is_res_done:
        closure_checks = [
            (
                f"Estado actual en Jira: {status_name} (Resolución: {resolution_name})."
                if lang == "es" else
                f"Current Jira status: {status_name} (Resolution: {resolution_name})."
            ),
        ]
        if resolved_by:
            closure_checks.append(
                f"Marcada como Resolved por {resolved_by} el {resolved_date}."
                if lang == "es" else
                f"Marked Resolved by {resolved_by} on {resolved_date}."
            )
        warn_21: Optional[str] = None
        if status_name.lower() == "closed" and closed_by:
            if is_po_or_proxy(closed_by):
                closure_checks.append(
                    f"Cerrada (Closed) por PO / PO Proxy autorizado: {closed_by} ({closed_date})."
                    if lang == "es" else
                    f"Closed by authorized PO / PO Proxy: {closed_by} ({closed_date})."
                )
            else:
                warn_21 = (
                    f"Cerrada por {closed_by} (no es PO ni PO Proxy)"
                    if lang == "es" else
                    f"Closed by {closed_by} (not PO or PO Proxy)"
                )
                closure_checks.append(
                    f"Atención: pasada a Closed por {closed_by} el {closed_date} (el cierre de User Stories corresponde a Matt Miller o Vanesa Savino)."
                    if lang == "es" else
                    f"Notice: transitioned to Closed by {closed_by} on {closed_date} (User Story closure is reserved for Matt Miller or Vanesa Savino)."
                )
        item_21: Dict[str, Any] = {
            "id": "21",
            "group": spec_21["group"],
            "title": spec_21["title"],
            "description": spec_21["description"],
            "status": "pass",
            "note": (
                f"La historia se encuentra en estado '{status_name}' con resolución '{resolution_name}'."
                if lang == "es" else
                f"Story is in '{status_name}' status with resolution '{resolution_name}'."
            ),
            "checks": closure_checks,
        }
        if warn_21:
            item_21["warning"] = warn_21
        criteria_items.append(item_21)
    else:
        note_21 = (
            f"La historia se encuentra en estado '{status_name}' (resolución: '{resolution_name}'); requiere estar en Resolved o Closed con resolución 'Done'."
            if lang == "es" else
            f"Story is currently in '{status_name}' (resolution: '{resolution_name}'); requires Resolved or Closed with resolution 'Done'."
        )
        checks_21 = [
            (
                f"Estado actual en Jira: {status_name} — Resolución: {resolution_name}."
                if lang == "es" else
                f"Current Jira status: {status_name} — Resolution: {resolution_name}."
            )
        ]
        criteria_items.append({
            "id": "21",
            "group": spec_21["group"],
            "title": spec_21["title"],
            "description": spec_21["description"],
            "status": "fail",
            "note": note_21,
            "checks": checks_21,
            "issues": [key],
        })
        act_text_21 = (
            f"Una vez completadas las validaciones de la DoD, pasar {key} a estado 'Resolved' con resolución 'Done'."
            if lang == "es" else
            f"Once all DoD validations are complete, transition {key} to 'Resolved' with resolution 'Done'."
        )
        actions.append({
            "group": "fix",
            "criterion": "21",
            "text": act_text_21,
            "issues": [key],
        })

    # Compute story-level summary counts
    pass_count = sum(1 for c in criteria_items if c["status"] == "pass")
    fail_count = sum(1 for c in criteria_items if c["status"] == "fail")
    manual_count = sum(1 for c in criteria_items if c["status"] == "manual")

    flags: List[str] = []
    if not has_any_pr:
        flags.append("Sin Pull Request vinculado" if lang == "es" else "No PR linked")
    elif open_prs or (cf_only_mode and cf_summary["state"] == "OPEN"):
        flags.append("Tiene PR abierto" if lang == "es" else "Open PR")
    elif pr_count > 0 and not all_prs_properly_approved:
        flags.append("Faltan aprobaciones de PR" if lang == "es" else "Missing PR approvals")
    if not linked_reqs:
        flags.append("Sin requerimiento vinculado" if lang == "es" else "Missing requirement link")
    if not figma_urls and not no_figma_declared:
        flags.append("Falta diseño en Figma" if lang == "es" else "Missing Figma design")
    if not is_resolved_or_closed or not is_res_done:
        flags.append(f"Estado actual: {status_name}" if lang == "es" else f"Current status: {status_name}")

    return {
        "key": key,
        "summary": summary,
        "issue_type": issue_type,
        "status": status_name,
        "resolution": resolution_name,
        "assignee": assignee_name,
        "parent_key": (fields.get("parent") or {}).get("key"),
        "linked_reqs": linked_reqs,
        "linked_bugs": linked_bugs,
        "prs": prs,
        "cf_pr_summary": cf_summary,
        "figma_urls": figma_urls,
        "no_figma_declared": no_figma_declared,
        "ac_items": ac_items,
        "zephyr_tcs": zephyr_tcs,
        "ac_coverage": ac_coverage,
        "subtasks": subtasks,
        "criteria": criteria_items,
        "actions": actions,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "manual_count": manual_count,
        "flags": flags,
        "overall_status": "fail" if fail_count > 0 else ("warning" if manual_count > 0 else "pass"),
    }


# ---------------------------------------------------------------------------
# Report Builders (Single Story & Sprint Batch)
# ---------------------------------------------------------------------------

def build_single_story_report(
    eval_result: Dict[str, Any],
    jira_base_url: str,
    zephyr: ZephyrRestClient,
    started_iso: str,
    elapsed_sec: float,
    lang: str = "en",
) -> Dict[str, Any]:
    if lang not in ("en", "es"):
        lang = "en"

    key = eval_result["key"]
    now_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    stamp = re.sub(r"[^0-9T]", "", now_iso)[:13]

    total_criteria = len(eval_result["criteria"])
    pass_count = eval_result["pass_count"]
    fail_count = eval_result["fail_count"]
    actions = eval_result["actions"]

    # Best practice recommendation
    practice_text = (
        "Buena práctica: formalizar los campos personalizados 'Design Required' (Yes/No) y 'Figma URL' en Jira para eliminar ambigüedades en historias con y sin interfaz."
        if lang == "es" else
        "Best practice: formalize 'Design Required' (Yes/No) and 'Figma URL' custom fields in Jira to eliminate ambiguity on UI vs non-UI stories."
    )
    actions_with_practice = list(actions) + [
        {
            "group": "practice",
            "text": practice_text,
        }
    ]
    blocking_actions_count = len(actions)

    overall = "fail" if fail_count > 0 else ("warning" if eval_result["manual_count"] > 0 else "pass")
    if lang == "es":
        headline = (
            f"La Story {key} cumple todas las validaciones automatizadas del DoD ({pass_count}/{total_criteria} aprobados)"
            if fail_count == 0
            else f"La Story {key} aún no cumple con la Definition of Done ({fail_count} punto(s) pendiente(s))"
        )
    else:
        headline = (
            f"Story {key} meets all automated DoD checks ({pass_count}/{total_criteria} passed)"
            if fail_count == 0
            else f"Story {key} does not yet meet the Definition of Done ({fail_count} failing item(s))"
        )

    # Pull Requests table
    pr_rows: List[List[str]] = []
    cf_pr_summary = eval_result.get("cf_pr_summary") or {}
    for p in eval_result["prs"]:
        author = str((p.get("author") or {}).get("name") or ("Desconocido" if lang == "es" else "Unknown"))
        revs = [
            f"{r.get('name')} ({('Aprobado' if lang == 'es' else 'Approved') if r.get('approved') else ('Pendiente' if lang == 'es' else 'Pending')})"
            for r in (p.get("reviewers") or [])
            if isinstance(r, dict)
        ]
        pr_rows.append([
            f"PR #{p.get('id', '?')}",
            str(p.get("repositoryName") or "Bitbucket"),
            str(p.get("status") or "UNKNOWN"),
            author,
            ", ".join(revs) if revs else ("Ninguno" if lang == "es" else "None"),
            str((p.get("destination") or {}).get("branch") or "master"),
        ])
    if not pr_rows and cf_pr_summary.get("has_prs"):
        cf_cnt = int(cf_pr_summary.get("count") or 0)
        cf_state = str(cf_pr_summary.get("state") or "LINKED")
        pr_rows.append([
            f"{cf_cnt} PR(s)" if lang == "en" else f"{cf_cnt} PR(s) vinculado(s)",
            "Bitbucket (panel Development)" if lang == "es" else "Bitbucket (Development panel)",
            cf_state,
            "Ver en Bitbucket" if lang == "es" else "See in Bitbucket",
            "Verificación manual (modo MCP)" if lang == "es" else "Manual verification (MCP mode)",
            "master",
        ])

    # Traceability & AC coverage table
    trace_rows: List[List[str]] = []
    missing_req_label = "FALTANTE" if lang == "es" else "MISSING"
    req_str = ", ".join(r["key"] for r in eval_result["linked_reqs"]) or missing_req_label
    if eval_result["ac_coverage"]:
        for cov in eval_result["ac_coverage"]:
            if cov["matched_tcs"]:
                tcs_str = ", ".join(cov["matched_tcs"])
            elif not zephyr.available:
                tcs_str = "Pendiente de Token Zephyr" if lang == "es" else "Pending Zephyr Token"
            else:
                tcs_str = "Sin caso de prueba asociado" if lang == "es" else "No matching TC"

            if cov["covered"]:
                status_cov = "Cubierto" if lang == "es" else "Covered"
            elif not zephyr.available:
                status_cov = "Verificación Manual" if lang == "es" else "Manual Check"
            else:
                status_cov = "Brecha" if lang == "es" else "Gap"

            trace_rows.append([
                req_str,
                cov["ac_id"],
                cov["ac_text"][:120],
                tcs_str,
                status_cov,
            ])
    else:
        trace_rows.append([
            req_str,
            "N/A",
            (
                "No se extrajeron Criterios de Aceptación explícitos de la descripción de la Story"
                if lang == "es" else
                "No explicit Acceptance Criteria bullets extracted from Story description"
            ),
            ", ".join(t["key"] for t in eval_result["zephyr_tcs"]) or ("Ninguno" if lang == "es" else "None"),
            "Sin Criterios de Aceptación" if lang == "es" else "Missing ACs",
        ])

    # Figma display value
    if eval_result["figma_urls"]:
        figma_val = eval_result["figma_urls"][0]
    elif eval_result["no_figma_declared"]:
        figma_val = "No Requiere Diseño" if lang == "es" else "No Design Required"
    else:
        figma_val = "Faltante" if lang == "es" else "Missing"

    none_linked_reqs = "Ninguno (¡Faltante!)" if lang == "es" else "None (Missing!)"

    total_pr_count = len(eval_result["prs"]) or int(cf_pr_summary.get("count") or 0)
    merged_pr_count = (
        len([p for p in eval_result["prs"] if str(p.get("status", "")).upper() == "MERGED"])
        if eval_result["prs"]
        else int(cf_pr_summary.get("merged_count") or 0)
    )

    overview_items = [
        {"key": "Clave de Story" if lang == "es" else "Story Key", "value": f"{key} — {eval_result['summary']}"},
        {"key": "Estado / Resolución" if lang == "es" else "Status / Resolution", "value": f"{eval_result['status']} ({eval_result['resolution']})"},
        {"key": "Responsable" if lang == "es" else "Assignee", "value": eval_result["assignee"]},
        {"key": "Requerimiento(s) Vinculado(s)" if lang == "es" else "Linked Requirement(s)", "value": ", ".join(f"{r['key']}: {r['summary']}" for r in eval_result["linked_reqs"]) or none_linked_reqs},
        {"key": "Pull Requests Vinculados" if lang == "es" else "Linked Pull Requests", "value": str(total_pr_count)},
        {"key": "Diseño en Figma" if lang == "es" else "Figma Design", "value": figma_val},
    ]

    pr_columns = (
        ["ID de PR", "Repositorio", "Estado", "Autor", "Revisores y Aprobaciones", "Rama de Destino"]
        if lang == "es" else
        ["PR ID", "Repository", "Status", "Author", "Reviewers & Approvals", "Target Branch"]
    )
    trace_columns = (
        ["Requerimiento", "ID de AC", "Criterio de Aceptación", "Casos de Prueba Zephyr", "Estado de Cobertura"]
        if lang == "es" else
        ["Requirement", "AC ID", "Acceptance Criterion", "Zephyr Test Cases", "Coverage Status"]
    )

    sections: List[Dict[str, Any]] = [
        {
            "id": "story-overview",
            "type": "key-value",
            "title": "Metadatos de la Story y Resumen de Trazabilidad" if lang == "es" else "Story Metadata & Traceability Summary",
            "items": overview_items,
        },
        {
            "id": "dod-criteria",
            "type": "criteria",
            "title": "Definition of Done de User Story (21 Criterios)" if lang == "es" else "User Story Definition of Done (21 Criteria)",
            "items": eval_result["criteria"],
        },
        {
            "id": "pr-details",
            "type": "generic-table",
            "title": "Pull Requests de Bitbucket Vinculados y Aprobaciones" if lang == "es" else "Linked Bitbucket Pull Requests & Approvals",
            "columns": pr_columns,
            "rows": pr_rows or ([["Ninguno", "—", "FALTANTE", "—", "—", "—"]] if lang == "es" else [["None", "—", "MISSING", "—", "—", "—"]]),
        },
        {
            "id": "traceability-matrix",
            "type": "generic-table",
            "title": "Matriz de Trazabilidad: Requerimiento -> Criterios de Aceptación -> Casos de Prueba Zephyr" if lang == "es" else "Traceability Matrix: Requirement -> Acceptance Criteria -> Zephyr Test Cases",
            "columns": trace_columns,
            "rows": trace_rows,
        },
        {
            "id": "criteria-distribution",
            "type": "distribution",
            "title": "Distribución de Criterios DoD" if lang == "es" else "DoD Criteria Breakdown",
            "items": [
                {"label": "Aprobados" if lang == "es" else "Passed", "value": pass_count, "status": "pass"},
                {"label": "Fallidos" if lang == "es" else "Failed", "value": fail_count, "status": "fail"},
                {"label": "Verificación Manual" if lang == "es" else "Manual Verification", "value": eval_result["manual_count"], "status": "manual"},
            ],
        },
    ]

    zephyr_unavailable_reason = (
        "La conexión con Zephyr Scale no estuvo disponible en esta ejecución; los casos de prueba se verifican manualmente."
        if lang == "es" else
        "Zephyr Scale connection was unavailable in this run; test cases are marked for manual verification."
    )
    sources = [
        {"name": "Jira", "kind": "jira", "status": "ok"},
        {"name": "Bitbucket / Pull Requests", "kind": "bitbucket", "status": "ok"},
        {
            "name": "Zephyr Scale",
            "kind": "other",
            "status": "ok" if zephyr.available and not zephyr.last_error else "unavailable",
            "reason": None if (zephyr.available and not zephyr.last_error) else zephyr_unavailable_reason,
        },
        {
            "name": "Jenkins CI",
            "kind": "jenkins",
            "status": "unavailable",
            "reason": (
                "La integración automática con Jenkins está pendiente; el estado del build oficial se verifica manualmente."
                if lang == "es" else
                "Automated Jenkins integration is pending; official build status is verified manually."
            ),
        },
    ]
    for s in sources:
        if s.get("reason") is None:
            s.pop("reason", None)

    kpis = [
        {
            "label": "Criterios DoD Cumplidos" if lang == "es" else "DoD Criteria Passed",
            "value": pass_count,
            "total": total_criteria,
            "status": "pass" if fail_count == 0 else "fail",
        },
        {
            "label": "PRs Mergeados" if lang == "es" else "Merged PRs",
            "value": merged_pr_count,
            "total": total_pr_count,
        },
        {
            "label": "Acciones Pendientes" if lang == "es" else "Pending Actions",
            "value": blocking_actions_count,
            "status": "fail" if fail_count > 0 else "manual",
        },
    ]

    return {
        "schema_version": "1.0",
        "meta": {
            "agent_id": AGENT_ID,
            "agent_name": AGENT_NAME,
            "agent_version": AGENT_VERSION,
            "team": "AI Governance",
            "run_id": f"{AGENT_ID}-{key.lower()}-{stamp}",
            "started_at": started_iso,
            "generated_at": now_iso,
            "execution_time_seconds": round(elapsed_sec, 2),
            "execution_time": f"{round(elapsed_sec, 1)}s",
            "language": lang,
            "scope": {
                "type": "story",
                "id": key,
                "label": f"{key}: {eval_result['summary'][:55]}",
                "project": key.split("-")[0] if "-" in key else "RSV1",
            },
            "jira_base_url": f"{jira_base_url.rstrip('/')}/browse/",
            "sources": sources,
        },
        "summary": {
            "status": overall,
            "headline": headline,
            "kpis": kpis,
        },
        "notices": [],
        "sections": sections,
        "actions": actions_with_practice,
    }


def build_sprint_batch_report(
    sprint_label: str,
    sprint_id: str,
    story_evals: List[Dict[str, Any]],
    jql_used: str,
    jira_base_url: str,
    zephyr: ZephyrRestClient,
    started_iso: str,
    elapsed_sec: float,
    lang: str = "en",
) -> Dict[str, Any]:
    if lang not in ("en", "es"):
        lang = "en"

    now_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    stamp = re.sub(r"[^0-9T]", "", now_iso)[:13]

    total_stories = len(story_evals)
    fully_passing_stories = [s for s in story_evals if s["fail_count"] == 0]
    failing_stories = [s for s in story_evals if s["fail_count"] > 0]

    # Aggregate each of the 21 criteria across all stories in the sprint
    aggregated_criteria: List[Dict[str, Any]] = []
    if story_evals:
        template_criteria = story_evals[0]["criteria"]
        for idx, tmpl in enumerate(template_criteria):
            cid = tmpl["id"]
            c_failing_keys = [s["key"] for s in story_evals if s["criteria"][idx]["status"] == "fail"]
            c_manual_keys = [s["key"] for s in story_evals if s["criteria"][idx]["status"] == "manual"]
            c_pass_count = total_stories - len(c_failing_keys) - len(c_manual_keys)

            if c_failing_keys:
                c_status = "fail"
                c_note = (
                    f"{len(c_failing_keys)} de {total_stories} historias aún no cumplen este criterio ({c_pass_count} cumplidas)."
                    if lang == "es" else
                    f"{len(c_failing_keys)} of {total_stories} stories do not yet meet this criterion ({c_pass_count} passed)."
                )
            elif c_manual_keys:
                c_status = "manual"
                c_note = (
                    f"Requiere confirmación manual en {len(c_manual_keys)} historia(s)."
                    if lang == "es" else
                    f"Requires manual confirmation across {len(c_manual_keys)} stories."
                )
            else:
                c_status = "pass"
                c_note = (
                    f"Las {total_stories} historias de {sprint_label} cumplen este criterio."
                    if lang == "es" else
                    f"All {total_stories} stories in {sprint_label} pass this criterion."
                )

            c_check_summary = (
                f"Se evaluaron {total_stories} historias: {c_pass_count} cumplidas, {len(c_failing_keys)} con pendientes y {len(c_manual_keys)} de revisión manual."
                if lang == "es" else
                f"Evaluated {total_stories} stories: {c_pass_count} passed, {len(c_failing_keys)} failing, {len(c_manual_keys)} manual."
            )
            c_checks_list = [c_check_summary]
            for s in story_evals:
                sc = s["criteria"][idx]
                if sc["status"] == "fail":
                    first_detail = (sc.get("checks") or [sc.get("note", "")])[0]
                    c_checks_list.append(f"{s['key']}: {first_detail}")

            agg_item: Dict[str, Any] = {
                "id": cid,
                "group": tmpl.get("group", "Story DoD"),
                "title": tmpl["title"],
                "description": tmpl["description"],
                "status": c_status,
                "note": c_note,
                "checks": c_checks_list,
            }
            if c_status == "manual":
                agg_item["reason"] = tmpl.get("reason", "by-design")
            if c_failing_keys:
                agg_item["issues"] = c_failing_keys
            aggregated_criteria.append(agg_item)

    # Build issue-table rows and summary matrix
    issue_rows: List[Dict[str, Any]] = []
    matrix_rows: List[List[str]] = []
    consolidated_actions: List[Dict[str, Any]] = []

    failing_by_crit: Dict[str, Tuple[str, List[str]]] = {}
    for s in story_evals:
        s_key = s["key"]
        issue_rows.append({
            "key": s_key,
            "type": s["issue_type"],
            "summary": s["summary"],
            "status": s["status"],
            "resolution": s["resolution"],
            "status_category": "pass" if s["fail_count"] == 0 else "fail",
            "assignee": s["assignee"],
            "parent": s["parent_key"],
            "flags": s["flags"] or (["DoD completa"] if lang == "es" else ["DoD OK"]),
        })
        s_pr_total = len(s["prs"]) or int((s.get("cf_pr_summary") or {}).get("count") or 0)
        pr_badge = f"{s_pr_total} PR(s)" if s_pr_total else ("Sin PR" if lang == "es" else "No PR")
        missing_req_label = "FALTANTE" if lang == "es" else "MISSING"
        req_badge = ", ".join(r["key"] for r in s["linked_reqs"]) or missing_req_label
        if s["figma_urls"]:
            figma_badge = "Vinculado" if lang == "es" else "Linked"
        elif s["no_figma_declared"]:
            figma_badge = "No Aplica" if lang == "es" else "N/A"
        else:
            figma_badge = "FALTANTE" if lang == "es" else "MISSING"

        if s["fail_count"] == 0:
            verdict = "LISTA" if lang == "es" else "READY"
        else:
            verdict = f"{s['fail_count']} Fallo(s)" if lang == "es" else f"{s['fail_count']} Fail(s)"

        matrix_rows.append([
            s_key,
            s["status"],
            pr_badge,
            req_badge,
            figma_badge,
            f"{s['pass_count']}/21",
            verdict,
        ])
        for act in s["actions"]:
            if act.get("group") == "fix":
                crit_id = str(act.get("criterion") or "0")
                if crit_id not in failing_by_crit:
                    replace_target = "las historias afectadas" if lang == "es" else "the affected stories"
                    failing_by_crit[crit_id] = (act["text"].replace(s_key, replace_target), [])
                if s_key not in failing_by_crit[crit_id][1]:
                    failing_by_crit[crit_id][1].append(s_key)

    for crit_id, (desc_tmpl, keys_list) in failing_by_crit.items():
        act_prefix = (
            f"[Criterio #{crit_id}] En {len(keys_list)} historia(s): {desc_tmpl}"
            if lang == "es" else
            f"[Criterion #{crit_id}] Across {len(keys_list)} story/stories: {desc_tmpl}"
        )
        consolidated_actions.append({
            "group": "fix",
            "criterion": crit_id,
            "text": act_prefix,
            "issues": keys_list,
        })

    jenkins_action = (
        f"Confirmar en Jenkins que los builds oficiales pasaron con éxito para las {total_stories} historias de {sprint_label}."
        if lang == "es" else
        f"Confirm in Jenkins that official builds passed for all {total_stories} stories in {sprint_label}."
    )
    consolidated_actions.append({
        "group": "manual",
        "criterion": "6",
        "text": jenkins_action,
    })

    overall = "fail" if failing_stories else "warning"
    if lang == "es":
        headline = (
            f"{sprint_label}: {len(fully_passing_stories)}/{total_stories} User Stories cumplen las validaciones automatizadas del DoD"
            if failing_stories
            else f"¡Todas las {total_stories} User Stories en {sprint_label} cumplen las validaciones automatizadas del DoD!"
        )
    else:
        headline = (
            f"{sprint_label}: {len(fully_passing_stories)}/{total_stories} User Stories pass all automated DoD checks"
            if failing_stories
            else f"All {total_stories} User Stories in {sprint_label} pass automated DoD checks!"
        )

    zephyr_batch_reason = (
        "La conexión con Zephyr Scale no estuvo disponible en esta ejecución; los casos de prueba se verifican manualmente."
        if lang == "es" else
        "Zephyr Scale connection was unavailable in this run; test cases are marked for manual verification."
    )
    sources = [
        {"name": "Jira", "kind": "jira", "status": "ok"},
        {"name": "Bitbucket / Pull Requests", "kind": "bitbucket", "status": "ok"},
        {
            "name": "Zephyr Scale",
            "kind": "other",
            "status": "ok" if zephyr.available and not zephyr.last_error else "unavailable",
            "reason": None if (zephyr.available and not zephyr.last_error) else zephyr_batch_reason,
        },
        {
            "name": "Jenkins CI",
            "kind": "jenkins",
            "status": "unavailable",
            "reason": (
                "La integración automática con Jenkins está pendiente."
                if lang == "es" else
                "Automated Jenkins integration is pending."
            ),
        },
    ]
    for s in sources:
        if s.get("reason") is None:
            s.pop("reason", None)

    kpis = [
        {
            "label": "Stories Listas (DoD Automatizado)" if lang == "es" else "Stories Ready (Automated DoD)",
            "value": len(fully_passing_stories),
            "total": total_stories,
            "status": "pass" if not failing_stories else "fail",
        },
        {
            "label": "Stories con Brechas de DoD" if lang == "es" else "Stories with DoD Gaps",
            "value": len(failing_stories),
            "total": total_stories,
            "status": "fail" if failing_stories else "pass",
        },
        {
            "label": "Grupos de Acciones Consolidadas" if lang == "es" else "Consolidated Action Groups",
            "value": len(consolidated_actions),
            "status": "fail" if failing_stories else "manual",
        },
    ]

    matrix_columns = (
        ["Clave Story", "Estado Jira", "PRs", "Enlace a Req", "Diseño Figma", "Criterios Cumplidos", "Veredicto DoD"]
        if lang == "es" else
        ["Story Key", "Jira Status", "PRs", "Requirement Link", "Figma Design", "Criteria Passed", "DoD Verdict"]
    )

    return {
        "schema_version": "1.0",
        "meta": {
            "agent_id": AGENT_ID,
            "agent_name": AGENT_NAME,
            "agent_version": AGENT_VERSION,
            "team": "AI Governance",
            "run_id": f"{AGENT_ID}-sprint-{sprint_id}-{stamp}",
            "started_at": started_iso,
            "generated_at": now_iso,
            "execution_time_seconds": round(elapsed_sec, 2),
            "execution_time": f"{round(elapsed_sec, 1)}s",
            "language": lang,
            "scope": {
                "type": "sprint-stories",
                "id": f"sprint-{sprint_id}",
                "label": f"{sprint_label} ({total_stories} Stories)",
                "project": "RSV1",
            },
            "jira_base_url": f"{jira_base_url.rstrip('/')}/browse/",
            "sources": sources,
        },
        "summary": {
            "status": overall,
            "headline": headline,
            "kpis": kpis,
        },
        "sections": [
            {
                "id": "dod-criteria",
                "type": "criteria",
                "title": f"Criterios de DoD Consolidados de User Stories ({sprint_label})" if lang == "es" else f"Aggregated User Story DoD Criteria ({sprint_label})",
                "items": aggregated_criteria,
            },
            {
                "id": "sprint-stories-matrix",
                "type": "generic-table",
                "title": "Matriz de Cumplimiento de DoD de User Stories" if lang == "es" else "User Story DoD Compliance Matrix",
                "columns": matrix_columns,
                "rows": matrix_rows,
            },
            {
                "id": "issues",
                "type": "issue-table",
                "title": f"User Stories Evaluadas en {sprint_label}" if lang == "es" else f"Evaluated User Stories in {sprint_label}",
                "jql": jql_used,
                "collapsed": False,
                "rows": issue_rows,
            },
            {
                "id": "story-readiness-dist",
                "type": "distribution",
                "title": "Distribución de Preparación de DoD" if lang == "es" else "Story DoD Readiness Distribution",
                "items": [
                    {"label": "Cumple DoD (Automatizado)" if lang == "es" else "DoD Compliant (Automated)", "value": len(fully_passing_stories), "status": "pass"},
                    {"label": "Con Brechas de DoD" if lang == "es" else "Has DoD Gaps", "value": len(failing_stories), "status": "fail"},
                ],
            },
        ],
        "actions": consolidated_actions,
    }


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    t0 = time.perf_counter()
    started_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    configure_stdout()

    parser = argparse.ArgumentParser(description="Validate Genesis User Story Definition of Done (DoD)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--story", help="Single User Story key (e.g. RSV1-29343)")
    group.add_argument("--sprint", help="Sprint number or name to audit all stories in batch (e.g. 183)")
    group.add_argument("--issue-json", type=Path, help="Path to pre-fetched Jira issue JSON (e.g. from MCP or cache). Bypasses direct Jira API calls.")
    parser.add_argument("--dev-info-json", type=Path, help="Optional path to pre-fetched Bitbucket PR / development info JSON (e.g. from mcp-atlassian jira_get_issue_development_info or Atlassian Remote MCP v2)")
    parser.add_argument("--lang", "--language", choices=["en", "es"], default=None, help="Language for report generation (en = English, es = Español)")
    parser.add_argument("--project", default="RSV1", help="Jira project key (default: RSV1)")
    parser.add_argument("--jira-url", help="Jira base URL")
    parser.add_argument("--jira-email", help="Jira user email")
    parser.add_argument("--jira-token", help="Jira API token")
    parser.add_argument("--zephyr-token", help="Zephyr Scale API token")
    parser.add_argument("--out", type=Path, help="Custom output report-data.json path")
    parser.add_argument("--no-render", action="store_true", help="Only write report-data.json without invoking build_report.py")
    args = parser.parse_args(argv)

    lang = discover_language(args.lang)
    if args.lang:
        save_language_preference(lang)

    lang_label = "Español (es)" if lang == "es" else "English (en)"
    log_step(t0, f"🚀 Starting User Story DoD Validator (Direct REST Gateway mode) | Language: {lang_label}...")

    creds = discover_credentials(
        cli_jira_url=args.jira_url,
        cli_jira_email=args.jira_email,
        cli_jira_token=args.jira_token,
        cli_zephyr_token=args.zephyr_token,
    )

    is_offline = bool(args.issue_json)
    has_jira_creds = bool(creds["jira_base_url"] and creds["jira_email"] and creds["jira_api_token"])

    if not is_offline and not has_jira_creds:
        print("ERROR: Missing Jira credentials (JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN).", file=sys.stderr)
        return 2

    jira = JiraRestClient(
        base_url=str(creds["jira_base_url"] or "https://genesissoftwareinnovations.atlassian.net"),
        email=str(creds["jira_email"] or "offline@genesis.internal"),
        api_token=str(creds["jira_api_token"] or "dummy-token"),
        offline=not has_jira_creds,
    )
    zephyr = ZephyrRestClient(
        api_token=creds.get("zephyr_api_token"),
        base_url=str(creds.get("zephyr_base_url") or "https://api.zephyrscale.smartbear.com/v2"),
    )

    pre_fetched_prs: Optional[List[dict]] = None
    if args.dev_info_json:
        try:
            dev_raw = json.loads(args.dev_info_json.read_text(encoding="utf-8-sig"))
            pre_fetched_prs = normalize_dev_info_prs(dev_raw)
            log_step(t0, f"🔗 Loaded {len(pre_fetched_prs)} Pull Request(s) from --dev-info-json: {args.dev_info_json}")
        except Exception as exc:
            print(f"WARNING: Could not parse --dev-info-json ({args.dev_info_json}): {exc}", file=sys.stderr)

    target_html_name: str = "report.html"
    if args.issue_json:
        log_step(t0, f"📋 Mode: Offline / Pre-fetched Issue JSON Mode -> {args.issue_json}")
        try:
            issue_data = json.loads(args.issue_json.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            print(f"ERROR: Cannot load JSON from {args.issue_json}: {exc}", file=sys.stderr)
            return 1
        norm_preview = normalize_issue_payload(issue_data)
        story_key = str(norm_preview.get("key") or "UNKNOWN").upper()
        eval_res = evaluate_single_story(issue_data, jira, zephyr, lang=lang, pre_fetched_prs=pre_fetched_prs)
        log_step(
            t0,
            f"📊 Evaluated 21 DoD criteria for {story_key}: "
            f"{eval_res['pass_count']} pass, {eval_res['fail_count']} fail, {eval_res['manual_count']} manual",
        )
        elapsed = time.perf_counter() - t0
        report_data = build_single_story_report(
            eval_res,
            str(creds["jira_base_url"] or "https://genesissoftwareinnovations.atlassian.net"),
            zephyr,
            started_iso,
            elapsed,
            lang=lang,
        )
        out_json = args.out or (WORKSPACE_ROOT / "reports" / AGENT_ID / story_key / "report-data.json")
        target_html_name = f"{story_key}-report.html"
    elif args.story:
        story_key = args.story.strip().upper()
        log_step(t0, f"📋 Mode: Single Story Audit -> {story_key}")
        try:
            log_step(t0, f"🔍 Querying Jira REST API v3 & dev-status for {story_key}...")
            issue_data = jira.get_issue(story_key, expand_changelog=True)
        except Exception as exc:
            print(f"ERROR: Failed to fetch {story_key}: {exc}", file=sys.stderr)
            return 1

        eval_res = evaluate_single_story(issue_data, jira, zephyr, lang=lang, pre_fetched_prs=pre_fetched_prs)
        log_step(
            t0,
            f"📊 Evaluated 21 DoD criteria for {story_key}: "
            f"{eval_res['pass_count']} pass, {eval_res['fail_count']} fail, {eval_res['manual_count']} manual",
        )
        elapsed = time.perf_counter() - t0
        report_data = build_single_story_report(
            eval_res,
            str(creds["jira_base_url"]),
            zephyr,
            started_iso,
            elapsed,
            lang=lang,
        )
        out_json = args.out or (WORKSPACE_ROOT / "reports" / AGENT_ID / story_key / "report-data.json")
        target_html_name = f"{story_key}-report.html"
        if not args.out:
            update_memory(lastAuditedStory=story_key)
    else:
        sprint_val = str(args.sprint).strip()
        log_step(t0, f"📋 Mode: Sprint Batch Audit -> Project {args.project}, Sprint '{sprint_val}'")
        jql = f'project = "{args.project}" AND issuetype = Story AND sprint = "{sprint_val}" ORDER BY key ASC'
        try:
            log_step(t0, f"🔍 Searching stories via Jira REST API: {jql}")
            issues = jira.search_jql(jql, fields=SEARCH_FIELDS, max_results=100)
        except Exception as exc:
            print(f"ERROR: Failed to search sprint stories: {exc}", file=sys.stderr)
            return 1

        if not issues:
            print(f"ERROR: No stories found for JQL: {jql}", file=sys.stderr)
            return 1

        log_step(t0, f"📦 Found {len(issues)} stories in Sprint {sprint_val}. Evaluating DoD criteria ({STORY_WORKERS} in parallel)...")
        jira.get_fields_map()
        if zephyr.available:
            zephyr.get_statuses(args.project)
            zephyr.get_environments(args.project)

        def eval_story(stub: dict) -> Dict[str, Any]:
            try:
                full_issue = jira.get_issue(stub.get("key", ""), expand_changelog=True)
            except Exception:
                full_issue = stub
            return evaluate_single_story(full_issue, jira, zephyr, lang=lang)

        story_evals: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=STORY_WORKERS) as pool:
            for idx, ev in enumerate(pool.map(eval_story, issues), start=1):
                story_evals.append(ev)
                log_step(t0, f"  [{idx}/{len(issues)}] {ev['key']}: {ev['pass_count']}/21 pass, {ev['fail_count']} fail")

        elapsed = time.perf_counter() - t0
        clean_sprint_id = re.sub(r"[^a-zA-Z0-9_-]", "-", sprint_val.lower())
        report_data = build_sprint_batch_report(
            sprint_label=f"Sprint {sprint_val}",
            sprint_id=clean_sprint_id,
            story_evals=story_evals,
            jql_used=jql,
            jira_base_url=str(creds["jira_base_url"]),
            zephyr=zephyr,
            started_iso=started_iso,
            elapsed_sec=elapsed,
            lang=lang,
        )
        out_json = args.out or (WORKSPACE_ROOT / "reports" / AGENT_ID / f"sprint-{clean_sprint_id}" / "report-data.json")
        target_html_name = f"sprint-{clean_sprint_id}-report.html"
        if not args.out:
            update_memory(lastAuditedSprint=sprint_val)

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report_data, indent=2, ensure_ascii=False), encoding="utf-8")
    log_step(t0, f"📝 Wrote report contract to: {out_json}")

    html_path: Optional[Path] = None
    if not args.no_render and RENDERER_SCRIPT.is_file():
        target_html_path = out_json.with_name(target_html_name)
        log_step(t0, f"🎨 Invoking genesis-report-renderer (build_report.py -> {target_html_path.name})...")
        proc = subprocess.run(
            [sys.executable, str(RENDERER_SCRIPT), str(out_json), "--out", str(target_html_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.stdout:
            print(proc.stdout.strip())
        if proc.returncode != 0:
            if proc.stderr:
                print(proc.stderr.strip(), file=sys.stderr)
            return proc.returncode
        html_path = target_html_path
        log_step(t0, f"✅ HTML Report compiled successfully: {html_path} (engine: {time.perf_counter() - t0:.1f}s)")

    print_summary(out_json, html_path)
    return 0


def print_summary(out_json: Path, html_path: Optional[Path]) -> None:
    """One compact line with everything the chat reply needs, so the LLM never opens the JSON/HTML."""
    data = json.loads(out_json.read_text(encoding="utf-8"))
    meta = data.get("meta") or {}
    criteria = [i for s in data.get("sections", []) if s.get("type") == "criteria" for i in s.get("items", [])]
    actions = data.get("actions") or []
    summary = {
        "verdict": (data.get("summary") or {}).get("status"),
        "headline": (data.get("summary") or {}).get("headline"),
        "counts": {st: sum(1 for c in criteria if c.get("status") == st) for st in ("pass", "fail", "manual")},
        "fail": [f"{c.get('id')}. {c.get('title')} — {c.get('note')}" for c in criteria if c.get("status") == "fail"],
        "manual": [f"{c.get('id')}. {c.get('title')}" for c in criteria if c.get("status") == "manual"],
        "actions": [f"[{a.get('group')}] {a.get('text')}" for a in actions if a.get("group") in ("fix", "manual")][:10],
        "execution_time": meta.get("execution_time"),
        "timing_source": (meta.get("timing") or {}).get("source", "engine-only"),
        "html": str(html_path) if html_path else None,
        "json": str(out_json),
    }
    print("SUMMARY " + json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    sys.exit(main())