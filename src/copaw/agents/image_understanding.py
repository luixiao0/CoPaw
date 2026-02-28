# -*- coding: utf-8 -*-
"""Media understanding prepass runner used by CoPawAgent."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from agentscope.message import Msg, TextBlock

from .model_fallback import run_with_vlm_fallback
from .vision_prepass import build_vlm_prepass_prompt, normalize_vlm_prepass_output
from ..providers import ResolvedModelConfig

DecisionOutcome = Literal["success", "failed", "skipped", "disabled"]
MediaCapability = Literal["image", "audio", "video"]


@dataclass
class MediaUnderstandingAttempt:
    provider_id: str
    model: str
    outcome: Literal["success", "failed", "skipped"]
    reason: str = ""


@dataclass
class MediaUnderstandingDecision:
    outcome: DecisionOutcome
    reason: str = ""
    selected_item_count: int = 0
    capability: MediaCapability = "image"
    attempts: list[MediaUnderstandingAttempt] = field(default_factory=list)


@dataclass
class MediaUnderstandingResult:
    analysis: str | None
    decision: MediaUnderstandingDecision
    used: ResolvedModelConfig | None = None


def get_last_message(msg: Msg | list[Msg] | None) -> Msg:
    if isinstance(msg, list):
        for item in reversed(msg):
            if isinstance(item, Msg):
                return item
    if isinstance(msg, Msg):
        return msg
    return Msg(
        name="user",
        role="user",
        content=[TextBlock(type="text", text="")],
    )


def extract_media_blocks(
    msg: Msg | list[Msg] | None,
    *,
    capability: MediaCapability,
) -> list[dict]:
    source = get_last_message(msg)
    blocks = source.content if isinstance(source.content, list) else []
    return [
        block
        for block in blocks
        if isinstance(block, dict) and block.get("type") == capability
    ]


def select_media_blocks_for_prepass(
    media_blocks: list[dict],
    *,
    mode: str = "first",
    max_items: int = 4,
) -> list[dict]:
    if max_items < 1:
        max_items = 1
    if mode != "all":
        return media_blocks[:1]
    return media_blocks[:max_items]


def build_prepass_message(
    source: Msg,
    media_blocks: list[dict],
    *,
    capability: MediaCapability,
    prompt_override: str = "",
) -> Msg:
    user_text = source.get_text_content() or ""
    prompt = (
        prompt_override.strip()
        if prompt_override and prompt_override.strip()
        else _build_prompt_by_capability(capability, user_text, len(media_blocks))
    )
    content = [TextBlock(type="text", text=prompt), *media_blocks]
    return Msg(name=source.name, role="user", content=content)


def _build_prompt_by_capability(
    capability: MediaCapability,
    user_text: str,
    selected_count: int,
) -> str:
    if capability == "image":
        return build_vlm_prepass_prompt(
            user_text=user_text,
            selected_image_count=selected_count,
        )
    if capability == "audio":
        return (
            "You are an audio preprocessor for a stronger text-only planner.\n"
            "Analyze provided audio blocks and return ONLY valid JSON.\n"
            "Do NOT answer the user directly.\n\n"
            "Required JSON schema:\n"
            "{\n"
            '  "ocr_text": ["..."],\n'
            '  "key_entities": ["..."],\n'
            '  "spatial_layout_cues": ["..."],\n'
            '  "ambiguities": ["..."],\n'
            '  "follow_up_checks": ["..."],\n'
            '  "confidence": "low|medium|high"\n'
            "}\n\n"
            f"Selected audio count: {selected_count}\n"
            f"User task:\n{user_text}"
        )
    return (
        "You are a video preprocessor for a stronger text-only planner.\n"
        "Analyze provided video blocks and return ONLY valid JSON.\n"
        "Do NOT answer the user directly.\n\n"
        "Required JSON schema:\n"
        "{\n"
        '  "ocr_text": ["..."],\n'
        '  "key_entities": ["..."],\n'
        '  "spatial_layout_cues": ["..."],\n'
        '  "ambiguities": ["..."],\n'
        '  "follow_up_checks": ["..."],\n'
        '  "confidence": "low|medium|high"\n'
        "}\n\n"
        f"Selected video count: {selected_count}\n"
        f"User task:\n{user_text}"
    )


def _cap_output_size(analysis: str, max_output_chars: int) -> str:
    if max_output_chars <= 0 or len(analysis) <= max_output_chars:
        return analysis
    compact = {
        "ocr_text": [],
        "key_entities": [],
        "spatial_layout_cues": [],
        "ambiguities": [
            "Vision prepass output exceeded max_output_chars and was compacted.",
        ],
        "follow_up_checks": [],
        "confidence": "low",
    }
    return json.dumps(compact, ensure_ascii=False)


async def run_media_understanding_prepass(
    *,
    msg: Msg | list[Msg] | None,
    capability: MediaCapability,
    enabled: bool,
    attachments_mode: str,
    max_items: int,
    prompt_override: str,
    timeout_seconds: int,
    max_output_chars: int,
    active_vlm_cfg: ResolvedModelConfig | None,
    vlm_fallback_models: list[tuple[ResolvedModelConfig, Any]],
    active_vlm_model: Any | None,
    run_with_runtime_model: Callable[[Any, Msg, int], Awaitable[str]],
) -> MediaUnderstandingResult:
    all_blocks = extract_media_blocks(msg, capability=capability)
    if not all_blocks:
        return MediaUnderstandingResult(
            analysis=None,
            decision=MediaUnderstandingDecision(
                outcome="skipped",
                reason=f"no {capability} blocks",
                capability=capability,
            ),
        )
    if not enabled:
        return MediaUnderstandingResult(
            analysis=None,
            decision=MediaUnderstandingDecision(
                outcome="disabled",
                reason=f"vision.{capability}.enabled=false",
                selected_item_count=0,
                capability=capability,
            ),
        )

    selected_blocks = select_media_blocks_for_prepass(
        all_blocks,
        mode=attachments_mode,
        max_items=max_items,
    )
    if not selected_blocks:
        return MediaUnderstandingResult(
            analysis=None,
            decision=MediaUnderstandingDecision(
                outcome="skipped",
                reason=f"no selected {capability} blocks",
                selected_item_count=0,
                capability=capability,
            ),
        )

    if active_vlm_cfg is None and len(vlm_fallback_models) == 0:
        return MediaUnderstandingResult(
            analysis=None,
            decision=MediaUnderstandingDecision(
                outcome="skipped",
                reason="no VLM configured",
                selected_item_count=len(selected_blocks),
                capability=capability,
            ),
        )

    source = get_last_message(msg)
    vlm_msg = build_prepass_message(
        source,
        selected_blocks,
        capability=capability,
        prompt_override=prompt_override,
    )

    model_map: dict[tuple[str, str], Any] = {}
    if active_vlm_cfg is not None and active_vlm_model is not None:
        model_map[(active_vlm_cfg.provider_id, active_vlm_cfg.model)] = active_vlm_model
    for cfg, model in vlm_fallback_models:
        model_map[(cfg.provider_id, cfg.model)] = model

    fallbacks = [cfg for cfg, _ in vlm_fallback_models]
    if active_vlm_cfg is None:
        primary = fallbacks[0]
        fallbacks = fallbacks[1:]
    else:
        primary = active_vlm_cfg

    async def _run(cfg: ResolvedModelConfig) -> str:
        runtime_model = model_map.get((cfg.provider_id, cfg.model))
        if runtime_model is None:
            raise RuntimeError(
                f"Runtime VLM model missing for {cfg.provider_id}/{cfg.model}",
            )
        raw = await run_with_runtime_model(runtime_model, vlm_msg, timeout_seconds)
        if not raw:
            raise RuntimeError("VLM prepass returned empty analysis")
        normalized = normalize_vlm_prepass_output(raw)
        return _cap_output_size(normalized, max_output_chars)

    try:
        fb_result = await run_with_vlm_fallback(primary, fallbacks, _run)
    except Exception as exc:
        return MediaUnderstandingResult(
            analysis=None,
            decision=MediaUnderstandingDecision(
                outcome="failed",
                reason=str(exc),
                selected_item_count=len(selected_blocks),
                capability=capability,
                attempts=[],
            ),
        )

    attempts = [
        MediaUnderstandingAttempt(
            provider_id=a.provider_id,
            model=a.model,
            outcome="failed",
            reason=a.error,
        )
        for a in fb_result.attempts
    ]
    attempts.append(
        MediaUnderstandingAttempt(
            provider_id=fb_result.used.provider_id,
            model=fb_result.used.model,
            outcome="success",
        ),
    )
    return MediaUnderstandingResult(
        analysis=fb_result.result,
        decision=MediaUnderstandingDecision(
            outcome="success",
            selected_item_count=len(selected_blocks),
            capability=capability,
            attempts=attempts,
        ),
        used=fb_result.used,
    )


async def run_image_understanding_prepass(**kwargs):
    """Backward-compatible wrapper for image-only callers."""
    return await run_media_understanding_prepass(capability="image", **kwargs)


select_image_blocks_for_prepass = select_media_blocks_for_prepass
