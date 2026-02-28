# -*- coding: utf-8 -*-
"""Model capability helpers used by runtime routing."""

from __future__ import annotations

import os

from ..providers import ResolvedModelConfig

# Conservative defaults: prefer explicit VLM slot unless model is clearly vision-capable.
_VISION_HINTS = (
    "gpt-4o",
    "gpt-4.1",
    "gemini",
    "qwen-vl",
    "qvq",
    "llava",
    "internvl",
    "minicpm-v",
    "glm-4v",
    "doubao-vision",
    "claude-3",
    "claude-sonnet-4",
)


def _parse_csv_env(name: str) -> set[str]:
    raw = os.getenv(name, "")
    values = [v.strip().lower() for v in raw.split(",") if v.strip()]
    return set(values)


def supports_vision(model_cfg: ResolvedModelConfig | None) -> bool:
    """Best-effort check for whether a model can accept image blocks."""
    if model_cfg is None or not model_cfg.model:
        return False

    model_name = model_cfg.model.strip().lower()
    if not model_name:
        return False

    # Explicit operator overrides take precedence.
    deny = _parse_csv_env("COPAW_NON_VISION_MODELS")
    if model_name in deny:
        return False

    allow = _parse_csv_env("COPAW_VISION_MODELS")
    if model_name in allow:
        return True

    return any(hint in model_name for hint in _VISION_HINTS)

