from copaw.agents.vision_prepass import (
    build_vlm_prepass_prompt,
    normalize_vlm_prepass_output,
)


def test_build_prompt_contains_json_contract() -> None:
    prompt = build_vlm_prepass_prompt("read the chart", 2)
    assert "return ONLY valid JSON" in prompt
    assert '"ocr_text": ["..."]' in prompt
    assert "Selected image count: 2" in prompt


def test_normalize_vlm_output_from_json() -> None:
    raw = (
        '{"ocr_text":["abc"],"key_entities":["cat"],'
        '"spatial_layout_cues":["top-left"],"ambiguities":["blur"],'
        '"follow_up_checks":["zoom"],"confidence":"high"}'
    )
    normalized = normalize_vlm_prepass_output(raw)
    assert '"ocr_text": ["abc"]' in normalized
    assert '"confidence": "high"' in normalized


def test_normalize_vlm_output_from_fenced_json() -> None:
    raw = "```json\n{\"ocr_text\":[\"x\"],\"confidence\":\"certain\"}\n```"
    normalized = normalize_vlm_prepass_output(raw)
    assert '"ocr_text": ["x"]' in normalized
    assert '"confidence": "high"' in normalized


def test_normalize_vlm_output_fallback_from_plain_text() -> None:
    raw = "I can see two tables and one blue button."
    normalized = normalize_vlm_prepass_output(raw)
    assert '"confidence": "medium"' in normalized
    assert '"ocr_text": []' in normalized

