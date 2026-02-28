from __future__ import annotations

import json
from pathlib import Path

import pytest

from copaw.agents.model_capabilities import supports_vision
from copaw.agents.model_fallback import run_with_vlm_fallback
from copaw.providers.models import (
    ModelSlotConfig,
    ProviderSettings,
    ResolvedModelConfig,
)
from copaw.providers.store import load_providers_json, save_providers_json


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def test_load_providers_json_defaults_vlm_fields(tmp_path: Path) -> None:
    providers_path = tmp_path / "providers.json"
    _write_json(
        providers_path,
        {
            "providers": {},
            "custom_providers": {},
            "active_llm": {"provider_id": "", "model": ""},
        },
    )

    data = load_providers_json(path=providers_path)
    assert data.active_vlm.provider_id == ""
    assert data.active_vlm.model == ""
    assert data.active_vlm_fallbacks == []


def test_save_and_reload_vlm_fields(tmp_path: Path) -> None:
    providers_path = tmp_path / "providers.json"
    data = load_providers_json(path=providers_path)
    data.providers["dashscope"] = ProviderSettings(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="test-key",
    )
    data.active_vlm = ModelSlotConfig(provider_id="dashscope", model="qwen-vl-max")
    data.active_vlm_fallbacks = [
        ModelSlotConfig(provider_id="dashscope", model="qwen-vl-plus"),
    ]
    save_providers_json(data, path=providers_path)

    reloaded = load_providers_json(path=providers_path)
    assert reloaded.active_vlm.provider_id == "dashscope"
    assert reloaded.active_vlm.model == "qwen-vl-max"
    assert len(reloaded.active_vlm_fallbacks) == 1
    assert reloaded.active_vlm_fallbacks[0].model == "qwen-vl-plus"


@pytest.mark.asyncio
async def test_run_with_vlm_fallback_uses_next_candidate() -> None:
    primary = ResolvedModelConfig(provider_id="p1", model="m1")
    fallback = ResolvedModelConfig(provider_id="p2", model="m2")
    calls: list[str] = []

    async def _runner(cfg: ResolvedModelConfig) -> str:
        calls.append(f"{cfg.provider_id}/{cfg.model}")
        if cfg.provider_id == "p1":
            raise RuntimeError("primary failed")
        return "ok"

    result = await run_with_vlm_fallback(primary, [fallback], _runner)
    assert result.result == "ok"
    assert result.used.provider_id == "p2"
    assert calls == ["p1/m1", "p2/m2"]
    assert len(result.attempts) == 1


def test_supports_vision_heuristics() -> None:
    assert supports_vision(ResolvedModelConfig(model="qwen-vl-max")) is True
    assert supports_vision(ResolvedModelConfig(model="deepseek-v3")) is False

