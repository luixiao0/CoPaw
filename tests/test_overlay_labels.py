"""Tests for _overlay_labels_and_screenshot coordinate handling.

Verifies that bounding_box() viewport-relative coordinates are passed
through to the JS overlay without scroll-offset subtraction.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from copaw.agents.tools.browser_control import (
    _overlay_labels_and_screenshot,
    _state,
    get_labeled_screenshot,
)


def _make_page(
    *,
    viewport_w: int = 1280,
    viewport_h: int = 720,
) -> AsyncMock:
    """Build a fake Playwright page."""
    page = AsyncMock()
    overlay_labels_captured: list[list[dict]] = []

    async def evaluate(script, arg=None):
        if "innerWidth" in script:
            return {"width": viewport_w, "height": viewport_h}
        if "data-copaw-labels" in script and arg is not None:
            overlay_labels_captured.append(arg)
        return None

    page.evaluate = AsyncMock(side_effect=evaluate)
    page.screenshot = AsyncMock(return_value=b"\x89PNG_FAKE")
    page._overlay_labels_captured = overlay_labels_captured
    return page


def _make_locator(box: dict | None) -> AsyncMock:
    locator = AsyncMock()
    locator.bounding_box = AsyncMock(return_value=box)
    return locator


def _refs_dict(names: list[str]) -> dict[str, dict]:
    return {name: {"role": "link", "name": f"Link {name}"} for name in names}


@pytest.fixture()
def patch_locator():
    """Yield a helper that patches _get_locator_by_ref with given boxes."""

    class _Ctx:
        def __init__(self):
            self.called_refs: list[str] = []

    def _setup(boxes: dict[str, dict | None]):
        ctx = _Ctx()

        def _fake_get_locator(page, page_id, ref, frame_selector=""):
            ctx.called_refs.append(ref)
            box = boxes.get(ref)
            return _make_locator(box)

        patcher = patch(
            "copaw.agents.tools.browser_control._get_locator_by_ref",
            side_effect=_fake_get_locator,
        )
        patcher.start()
        return patcher, ctx

    yield _setup


# ---------- coordinate pass-through ----------


@pytest.mark.asyncio
async def test_coords_passthrough_no_scroll(patch_locator):
    """bounding_box viewport-relative coords must appear unchanged in labels."""
    boxes = {
        "e1": {"x": 100, "y": 200, "width": 80, "height": 30},
        "e2": {"x": 500, "y": 50, "width": 120, "height": 40},
    }
    patcher, _ = patch_locator(boxes)
    page = _make_page()

    try:
        _, drawn, skipped = await _overlay_labels_and_screenshot(
            page, _refs_dict(["e1", "e2"]), "pg1",
        )
    finally:
        patcher.stop()

    assert drawn == 2
    assert skipped == 0

    labels = page._overlay_labels_captured
    assert len(labels) == 1
    by_ref = {lb["ref"]: lb for lb in labels[0]}
    assert by_ref["e1"]["x"] == 100
    assert by_ref["e1"]["y"] == 200
    assert by_ref["e2"]["x"] == 500
    assert by_ref["e2"]["y"] == 50


@pytest.mark.asyncio
async def test_coords_passthrough_after_scroll(patch_locator):
    """After scrolling, bounding_box still returns viewport-relative coords.

    The overlay must NOT subtract scroll offsets — that was the prior bug.
    Simulates: page scrolled 500px down, element near top of viewport.
    """
    boxes = {
        "e10": {"x": 50, "y": 20, "width": 200, "height": 60},
        "e11": {"x": 400, "y": 650, "width": 150, "height": 50},
    }
    patcher, _ = patch_locator(boxes)
    page = _make_page()

    try:
        _, drawn, _ = await _overlay_labels_and_screenshot(
            page, _refs_dict(["e10", "e11"]), "pg1",
        )
    finally:
        patcher.stop()

    assert drawn == 2
    by_ref = {lb["ref"]: lb for lb in page._overlay_labels_captured[0]}
    assert by_ref["e10"]["x"] == 50
    assert by_ref["e10"]["y"] == 20
    assert by_ref["e11"]["x"] == 400
    assert by_ref["e11"]["y"] == 650


# ---------- viewport clipping ----------


@pytest.mark.asyncio
async def test_offscreen_right_skipped(patch_locator):
    boxes = {
        "e1": {"x": 100, "y": 100, "width": 80, "height": 30},
        "e2": {"x": 1500, "y": 100, "width": 80, "height": 30},
    }
    patcher, _ = patch_locator(boxes)
    page = _make_page(viewport_w=1280, viewport_h=720)

    try:
        _, drawn, skipped = await _overlay_labels_and_screenshot(
            page, _refs_dict(["e1", "e2"]), "pg1",
        )
    finally:
        patcher.stop()

    assert drawn == 1
    assert skipped == 1


@pytest.mark.asyncio
async def test_offscreen_above_skipped(patch_locator):
    boxes = {
        "e1": {"x": 100, "y": -50, "width": 80, "height": 20},
    }
    patcher, _ = patch_locator(boxes)
    page = _make_page()

    try:
        _, drawn, skipped = await _overlay_labels_and_screenshot(
            page, _refs_dict(["e1"]), "pg1",
        )
    finally:
        patcher.stop()

    assert drawn == 0
    assert skipped == 1


@pytest.mark.asyncio
async def test_offscreen_below_skipped(patch_locator):
    boxes = {"e1": {"x": 100, "y": 800, "width": 80, "height": 30}}
    patcher, _ = patch_locator(boxes)
    page = _make_page(viewport_h=720)

    try:
        _, drawn, skipped = await _overlay_labels_and_screenshot(
            page, _refs_dict(["e1"]), "pg1",
        )
    finally:
        patcher.stop()

    assert drawn == 0
    assert skipped == 1


@pytest.mark.asyncio
async def test_partially_visible_kept(patch_locator):
    """Element partially off-screen on the left should still be drawn."""
    boxes = {"e1": {"x": -30, "y": 100, "width": 80, "height": 30}}
    patcher, _ = patch_locator(boxes)
    page = _make_page()

    try:
        _, drawn, skipped = await _overlay_labels_and_screenshot(
            page, _refs_dict(["e1"]), "pg1",
        )
    finally:
        patcher.stop()

    assert drawn == 1
    assert skipped == 0


# ---------- invisible / failed locators ----------


@pytest.mark.asyncio
async def test_none_bbox_skipped(patch_locator):
    boxes = {
        "e1": {"x": 100, "y": 100, "width": 80, "height": 30},
        "e2": None,
    }
    patcher, _ = patch_locator(boxes)
    page = _make_page()

    try:
        _, drawn, skipped = await _overlay_labels_and_screenshot(
            page, _refs_dict(["e1", "e2"]), "pg1",
        )
    finally:
        patcher.stop()

    assert drawn == 1
    assert skipped == 1


@pytest.mark.asyncio
async def test_bbox_timeout_skipped(patch_locator):
    """bounding_box raising TimeoutError should be treated as skipped."""
    good_box = {"x": 100, "y": 100, "width": 80, "height": 30}

    def _fake_get_locator(page, page_id, ref, frame_selector=""):
        if ref == "e2":
            loc = AsyncMock()
            loc.bounding_box = AsyncMock(side_effect=TimeoutError("timeout"))
            return loc
        return _make_locator(good_box)

    with patch(
        "copaw.agents.tools.browser_control._get_locator_by_ref",
        side_effect=_fake_get_locator,
    ):
        page = _make_page()
        _, drawn, skipped = await _overlay_labels_and_screenshot(
            page, _refs_dict(["e1", "e2"]), "pg1",
        )

    assert drawn == 1
    assert skipped == 1


@pytest.mark.asyncio
async def test_unresolvable_ref_skipped(patch_locator):
    """_get_locator_by_ref returning None should be treated as skipped."""
    def _fake_get_locator(page, page_id, ref, frame_selector=""):
        if ref == "e2":
            return None
        return _make_locator({"x": 10, "y": 10, "width": 50, "height": 50})

    with patch(
        "copaw.agents.tools.browser_control._get_locator_by_ref",
        side_effect=_fake_get_locator,
    ):
        page = _make_page()
        _, drawn, skipped = await _overlay_labels_and_screenshot(
            page, _refs_dict(["e1", "e2"]), "pg1",
        )

    assert drawn == 1
    assert skipped == 1


# ---------- cleanup always runs ----------


@pytest.mark.asyncio
async def test_remove_labels_called_even_on_screenshot_error(patch_locator):
    """_JS_REMOVE_LABELS must execute even if screenshot() raises."""
    boxes = {"e1": {"x": 10, "y": 10, "width": 50, "height": 50}}
    patcher, _ = patch_locator(boxes)
    page = _make_page()
    page.screenshot = AsyncMock(side_effect=RuntimeError("screenshot failed"))

    with pytest.raises(RuntimeError, match="screenshot failed"):
        try:
            await _overlay_labels_and_screenshot(
                page, _refs_dict(["e1"]), "pg1",
            )
        finally:
            patcher.stop()

    remove_calls = [
        c for c in page.evaluate.call_args_list
        if c.args
        and isinstance(c.args[0], str)
        and "el.remove()" in c.args[0]
        and "appendChild" not in c.args[0]
    ]
    assert len(remove_calls) == 1


# ---------- get_labeled_screenshot refreshes snapshot ----------

_SAMPLE_ARIA = (
    "- document:\n"
    '  - link "Home" [ref=e1]:\n'
    "    - /url: /\n"
    '  - button "Login" [ref=e2]\n'
)

_SAMPLE_ARIA_EXTENDED = (
    "- document:\n"
    '  - link "Home":\n'
    "    - /url: /\n"
    '  - button "Login"\n'
    '  - link "New lazy item":\n'
    "    - /url: /new\n"
)


@pytest.mark.asyncio
async def test_get_labeled_screenshot_refreshes_snapshot():
    """get_labeled_screenshot must take a fresh aria_snapshot.

    Simulates: initial snapshot has 2 refs, then lazy-loaded content
    adds a third element. The labeled screenshot should include all 3.
    """
    page = _make_page()
    page_id = "test_page"

    call_count = [0]

    locator_mock = AsyncMock()

    async def aria_snapshot_side_effect():
        call_count[0] += 1
        return _SAMPLE_ARIA_EXTENDED

    locator_mock.aria_snapshot = AsyncMock(side_effect=aria_snapshot_side_effect)

    root_mock = AsyncMock()
    root_mock.locator = lambda sel: locator_mock

    _state["current_page_id"] = page_id
    _state["pages"][page_id] = page
    _state["refs"][page_id] = {
        "e1": {"role": "link", "name": "Home"},
        "e2": {"role": "button", "name": "Login"},
    }
    _state["refs_frame"] = {page_id: ""}

    boxes_map = {
        "e1": {"x": 10, "y": 10, "width": 100, "height": 30},
        "e2": {"x": 200, "y": 10, "width": 80, "height": 30},
        "e3": {"x": 10, "y": 600, "width": 150, "height": 40},
    }

    def fake_get_locator(pg, pid, ref, fs=""):
        box = boxes_map.get(ref)
        return _make_locator(box)

    with (
        patch(
            "copaw.agents.tools.browser_control._get_root",
            return_value=root_mock,
        ),
        patch(
            "copaw.agents.tools.browser_control._get_locator_by_ref",
            side_effect=fake_get_locator,
        ),
    ):
        result = await get_labeled_screenshot()

    assert result is not None
    assert call_count[0] == 1, "aria_snapshot should have been called"

    new_refs = _state["refs"][page_id]
    assert len(new_refs) == 3, f"Expected 3 refs after refresh, got {len(new_refs)}"
    ref_names = [info.get("name") for info in new_refs.values()]
    assert "New lazy item" in ref_names

    labels = page._overlay_labels_captured
    assert len(labels) == 1
    drawn_refs = {lb["ref"] for lb in labels[0]}
    assert len(drawn_refs) == 3

    _state["current_page_id"] = None
    _state["pages"].pop(page_id, None)
    _state["refs"].pop(page_id, None)
    _state["refs_frame"].pop(page_id, None)
