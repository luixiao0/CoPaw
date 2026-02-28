# -*- coding: utf-8 -*-
"""Vision prepass prompt and normalization helpers."""

from __future__ import annotations

import json
import re

_JSON_BLOCK_RE = re.compile(r"```json\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)
_OBJECT_RE = re.compile(r"(\{[\s\S]*\})")


def build_vlm_prepass_prompt(user_text: str, selected_image_count: int) -> str:
    return (
        "You are a vision preprocessor for a stronger text-only planner.\n"
        "Analyze the provided images and return ONLY valid JSON.\n"
        "Do NOT answer the user directly and do NOT invent unseen details.\n\n"
        "Required JSON schema:\n"
        "{\n"
        '  "ocr_text": ["..."],\n'
        '  "key_entities": ["..."],\n'
        '  "spatial_layout_cues": ["..."],\n'
        '  "ambiguities": ["..."],\n'
        '  "follow_up_checks": ["..."],\n'
        '  "confidence": "low|medium|high"\n'
        "}\n\n"
        f"Selected image count: {selected_image_count}\n"
        f"User task:\n{user_text}"
    )


def normalize_vlm_prepass_output(raw: str) -> str:
    """Normalize arbitrary VLM output into a stable JSON contract."""
    parsed = _parse_json_payload(raw)
    if not isinstance(parsed, dict):
        parsed = {"raw_summary": (raw or "").strip()}

    normalized = {
        "ocr_text": _norm_list(parsed.get("ocr_text")),
        "key_entities": _norm_list(parsed.get("key_entities")),
        "spatial_layout_cues": _norm_list(parsed.get("spatial_layout_cues")),
        "ambiguities": _norm_list(parsed.get("ambiguities")),
        "follow_up_checks": _norm_list(parsed.get("follow_up_checks")),
        "confidence": _norm_confidence(parsed.get("confidence")),
    }
    return json.dumps(normalized, ensure_ascii=False)


def _parse_json_payload(raw: str):
    if not raw:
        return None
    txt = raw.strip()
    try:
        return json.loads(txt)
    except Exception:
        pass

    m = _JSON_BLOCK_RE.search(txt)
    if m:
        candidate = m.group(1)
        try:
            return json.loads(candidate)
        except Exception:
            pass

    m = _OBJECT_RE.search(txt)
    if m:
        candidate = m.group(1)
        try:
            return json.loads(candidate)
        except Exception:
            pass
    return None


def _norm_list(value, max_items: int = 8, max_item_len: int = 280) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        text = str(item).strip()
        if not text:
            continue
        if len(text) > max_item_len:
            text = text[: max_item_len - 3] + "..."
        out.append(text)
        if len(out) >= max_items:
            break
    return out


def _norm_confidence(value) -> str:
    if not value:
        return "medium"
    s = str(value).strip().lower()
    if s in {"low", "medium", "high"}:
        return s
    if s in {"uncertain", "weak"}:
        return "low"
    if s in {"strong", "certain"}:
        return "high"
    return "medium"

