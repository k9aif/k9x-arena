# SPDX-License-Identifier: Apache-2.0
# K9-AIF Framework
"""Read-only Ollama host status: pulled models (/api/tags) and the model
currently loaded on the GPU (/api/ps). No inference here — every model call
goes through llm_invoke."""

from __future__ import annotations

import re
from typing import Any, Dict, List

import requests

from arena.settings import ollama_base_url


def params_billions(parameter_size: str):
    """'27.3B' -> 27.3, '560M' -> 0.56; None when Ollama doesn't say."""
    m = re.fullmatch(r"\s*([\d.]+)\s*([BMK])\s*", parameter_size or "", re.IGNORECASE)
    if not m:
        return None
    value = float(m.group(1))
    return {"B": value, "M": value / 1000, "K": value / 1e6}[m.group(2).upper()]


def list_models(timeout: float = 5.0) -> List[Dict[str, Any]]:
    resp = requests.get(f"{ollama_base_url()}/api/tags", timeout=timeout)
    resp.raise_for_status()
    out = []
    for m in resp.json().get("models", []):
        d = m.get("details", {}) or {}
        out.append({
            "tag": m.get("name"),
            "size_gb": round((m.get("size") or 0) / 1e9, 1),
            "family": d.get("family", ""),
            "parameters": d.get("parameter_size", ""),
            "params_b": params_billions(d.get("parameter_size", "")),
            "quantization": d.get("quantization_level", ""),
            "modified_at": m.get("modified_at", ""),
        })
    return sorted(out, key=lambda x: x["tag"] or "")


def loaded_models(timeout: float = 5.0) -> List[str]:
    try:
        resp = requests.get(f"{ollama_base_url()}/api/ps", timeout=timeout)
        resp.raise_for_status()
        return [m.get("name") for m in resp.json().get("models", []) if m.get("name")]
    except Exception:
        return []


def reachable(timeout: float = 3.0) -> bool:
    try:
        requests.get(f"{ollama_base_url()}/api/tags", timeout=timeout).raise_for_status()
        return True
    except Exception:
        return False
