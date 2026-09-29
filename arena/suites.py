# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""Task suites: built-in YAML files under suites/, plus uploads (a suite
YAML, or a .md/.txt source document turned into judged tasks)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

from arena.settings import ROOT, TASK_TYPES, safe_name

SUITES_DIR = ROOT / "suites"
UPLOADS_DIR = ROOT / "runtime" / "suites"

_REQUIRED = {
    "code": ["prompt", "tests"],
    "extraction": ["prompt", "expected"],
    "reasoning": ["prompt", "answer"],
    "summarization": ["prompt", "reference"],
    "chat": ["prompt", "rubric"],
    "adversarial": ["prompt", "expect"],
}


def validate(suite: Dict[str, Any]) -> List[str]:
    errors = []
    tasks = suite.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return ["suite has no tasks"]
    seen = set()
    for i, t in enumerate(tasks):
        tid = t.get("id") or f"#{i + 1}"
        if tid in seen:
            errors.append(f"{tid}: duplicate id")
        seen.add(tid)
        ttype = t.get("type")
        if ttype not in TASK_TYPES:
            errors.append(f"{tid}: unknown type {ttype!r} (expected one of {', '.join(TASK_TYPES)})")
            continue
        for key in _REQUIRED[ttype]:
            if not t.get(key):
                errors.append(f"{tid}: {ttype} task needs '{key}'")
        if ttype == "adversarial" and t.get("expect") not in ("refuse", "comply"):
            errors.append(f"{tid}: expect must be 'refuse' or 'comply'")
    return errors


def _load_file(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def list_suites() -> List[Dict[str, Any]]:
    out = []
    for folder, source in ((SUITES_DIR, "built-in"), (UPLOADS_DIR, "uploaded")):
        if not folder.exists():
            continue
        for p in sorted(folder.glob("*.yaml")):
            try:
                s = _load_file(p)
            except Exception:
                continue
            tasks = s.get("tasks") or []
            types = sorted({t.get("type") for t in tasks if t.get("type")})
            out.append({
                "key": f"{source}:{p.stem}",
                "name": s.get("name", p.stem),
                "source": source,
                "tasks": len(tasks),
                "types": types,
            })
    return out


def load_suite(key: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    source, _, stem = key.partition(":")
    folder = SUITES_DIR if source == "built-in" else UPLOADS_DIR
    path = folder / f"{safe_name(stem) if source != 'built-in' else stem}.yaml"
    if not path.exists() or path.parent.resolve() != folder.resolve():
        raise FileNotFoundError(f"suite not found: {key}")
    suite = _load_file(path)
    errors = validate(suite)
    if errors:
        raise ValueError("; ".join(errors))
    return suite, suite["tasks"]


def save_uploaded_suite(name: str, suite: Dict[str, Any]) -> str:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    stem = safe_name(name) or "upload"
    (UPLOADS_DIR / f"{stem}.yaml").write_text(yaml.safe_dump(suite, sort_keys=False))
    return f"uploaded:{stem}"


def suite_from_document(file_name: str, text: str) -> Dict[str, Any]:
    """A source document becomes judged tasks: one summary, and one
    question-answering chat task per top-level section (up to 4)."""
    title = Path(file_name).stem.replace("-", " ").replace("_", " ").strip().title() or "Document"
    doc = text.strip()[:12000]
    sections = [s.strip() for s in re.split(r"\n(?=#{1,2}\s)", doc) if s.strip()]
    tasks = [{
        "id": "DOC-SUM-1", "type": "summarization", "title": f"Summarize: {title}",
        "prompt": f"Summarize the following document for a busy manager in 5 bullet points.\n\n{doc}",
        "reference": "A faithful 5-bullet summary covering the document's main points, with no invented facts.",
    }]
    for i, sec in enumerate(sections[:4], start=1):
        heading = sec.splitlines()[0].lstrip("# ").strip()[:80]
        tasks.append({
            "id": f"DOC-CHAT-{i}", "type": "chat", "title": f"Explain: {heading}",
            "prompt": f"Using only the document below, explain '{heading}' to a customer in plain language.\n\n{doc}",
            "rubric": "Accurate to the document, plain language, polite, no invented facts, under 150 words.",
        })
    return {"name": f"Uploaded: {title}", "tasks": tasks}
