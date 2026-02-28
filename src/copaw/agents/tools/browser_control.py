# -*- coding: utf-8 -*-
# flake8: noqa: E501
"""Browser automation tool using Playwright.

Single tool with action-based API matching browser MCP: start, stop, open,
navigate, navigate_back, screenshot, snapshot, click, type, eval, evaluate,
resize, console_messages, handle_dialog, file_upload, fill_form, install,
press_key, network_requests, run_code, drag, hover, select_option, tabs,
wait_for, pdf, close. Uses refs from snapshot for ref-based actions.
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from typing import Any

from pathlib import Path

from agentscope.message import ImageBlock, TextBlock, URLSource
from agentscope.tool import ToolResponse

from .browser_snapshot import build_role_snapshot_from_aria

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stealth / anti-detection configuration
# ---------------------------------------------------------------------------

_STEALTH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_STEALTH_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-infobars",
    "--disable-component-update",
    "--disable-hang-monitor",
    "--disable-ipc-flooding-protection",
    "--disable-popup-blocking",
    "--disable-prompt-on-repost",
    "--disable-renderer-backgrounding",
    "--disable-sync",
    "--metrics-recording-only",
    "--no-service-autorun",
    "--password-store=basic",
    "--use-mock-keychain",
    "--window-size=1920,1080",
    "--disable-web-security=false",
]

_STEALTH_INIT_JS = r"""
(() => {
// ---- helpers: make patched functions look native in toString() ----
const _nativeToString = Function.prototype.toString;
const _patchedFns = new Set();
Function.prototype.toString = function () {
    if (_patchedFns.has(this))
        return `function ${this.name || ''}() { [native code] }`;
    return _nativeToString.call(this);
};
_patchedFns.add(Function.prototype.toString);

function _makeNative(fn) { _patchedFns.add(fn); return fn; }
function _defineGetter(obj, prop, getter) {
    Object.defineProperty(obj, prop, {
        get: _makeNative(getter),
        configurable: true,
    });
}

// ---- 1. navigator.webdriver ----
_defineGetter(navigator, 'webdriver', function webdriver() {
    return undefined;
});

// ---- 2. chrome.runtime ----
if (!window.chrome) window.chrome = {};
if (!window.chrome.runtime) {
    window.chrome.runtime = {
        connect: _makeNative(function connect() {}),
        sendMessage: _makeNative(function sendMessage() {}),
        id: undefined,
    };
}
window.chrome.app = {
    InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
    RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' },
    getDetails: _makeNative(function getDetails() {}),
    getIsInstalled: _makeNative(function getIsInstalled() {}),
    installState: _makeNative(function installState() { return 'not_installed'; }),
    isInstalled: false,
    runningState: _makeNative(function runningState() { return 'cannot_run'; }),
};
window.chrome.csi = _makeNative(function csi() { return {}; });
window.chrome.loadTimes = _makeNative(function loadTimes() { return {}; });

// ---- 3. permissions ----
const _origQuery = navigator.permissions.query.bind(navigator.permissions);
const _patchedQuery = _makeNative(function query(params) {
    if (params && params.name === 'notifications')
        return Promise.resolve({ state: Notification.permission });
    return _origQuery(params);
});
navigator.permissions.query = _patchedQuery;

// ---- 4. plugins & mimeTypes (realistic Chrome set) ----
function _fakePlugin(name, desc, filename) {
    return { name, description: desc, filename, length: 1 };
}
const _plugins = [
    _fakePlugin('Chrome PDF Plugin', 'Portable Document Format', 'internal-pdf-viewer'),
    _fakePlugin('Chrome PDF Viewer', '', 'mhjfbmdgcfjbbpaeojofohoefgiehjai'),
    _fakePlugin('Native Client', '', 'internal-nacl-plugin'),
];
_defineGetter(navigator, 'plugins', function plugins() { return _plugins; });
_defineGetter(navigator, 'mimeTypes', function mimeTypes() {
    return [{ type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' }];
});

// ---- 5. languages ----
_defineGetter(navigator, 'languages', function languages() {
    return ['zh-CN', 'zh', 'en-US', 'en'];
});
_defineGetter(navigator, 'language', function language() { return 'zh-CN'; });

// ---- 6. platform & vendor ----
_defineGetter(navigator, 'platform', function platform() { return 'Win32'; });
_defineGetter(navigator, 'vendor', function vendor() { return 'Google Inc.'; });

// ---- 7. hardware ----
_defineGetter(navigator, 'hardwareConcurrency', function hardwareConcurrency() { return 8; });
_defineGetter(navigator, 'deviceMemory', function deviceMemory() { return 8; });
_defineGetter(navigator, 'maxTouchPoints', function maxTouchPoints() { return 0; });

// ---- 8. connection / NetworkInformation ----
if (!navigator.connection) {
    Object.defineProperty(navigator, 'connection', {
        value: { effectiveType: '4g', rtt: 50, downlink: 10, saveData: false },
        configurable: true,
    });
}

// ---- 9. screen dimensions (match viewport) ----
for (const [p, v] of Object.entries({
    width: 1920, height: 1080,
    availWidth: 1920, availHeight: 1040,
    colorDepth: 24, pixelDepth: 24,
})) {
    _defineGetter(screen, p, new Function(`return ${v};`));
}
_defineGetter(window, 'outerWidth', function outerWidth() { return 1920; });
_defineGetter(window, 'outerHeight', function outerHeight() { return 1080; });
_defineGetter(window, 'devicePixelRatio', function devicePixelRatio() { return 1; });

// ---- 10. WebGL vendor & renderer ----
const _getParam = WebGLRenderingContext.prototype.getParameter;
const _patchedGetParam = _makeNative(function getParameter(param) {
    if (param === 37445) return 'Google Inc. (NVIDIA)';
    if (param === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)';
    return _getParam.call(this, param);
});
WebGLRenderingContext.prototype.getParameter = _patchedGetParam;
if (typeof WebGL2RenderingContext !== 'undefined') {
    const _getParam2 = WebGL2RenderingContext.prototype.getParameter;
    const _patchedGetParam2 = _makeNative(function getParameter(param) {
        if (param === 37445) return 'Google Inc. (NVIDIA)';
        if (param === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)';
        return _getParam2.call(this, param);
    });
    WebGL2RenderingContext.prototype.getParameter = _patchedGetParam2;
}

// ---- 11. Canvas fingerprint noise ----
const _origToDataURL = HTMLCanvasElement.prototype.toDataURL;
HTMLCanvasElement.prototype.toDataURL = _makeNative(function toDataURL(type) {
    const ctx = this.getContext('2d');
    if (ctx) {
        const shift = (0.01 * (Math.random() - 0.5));
        const img = ctx.getImageData(0, 0, Math.min(this.width, 2), 1);
        if (img.data.length > 0) img.data[0] = Math.max(0, img.data[0] + shift);
        ctx.putImageData(img, 0, 0);
    }
    return _origToDataURL.apply(this, arguments);
});
const _origToBlob = HTMLCanvasElement.prototype.toBlob;
HTMLCanvasElement.prototype.toBlob = _makeNative(function toBlob() {
    const ctx = this.getContext('2d');
    if (ctx) {
        const shift = (0.01 * (Math.random() - 0.5));
        const img = ctx.getImageData(0, 0, Math.min(this.width, 2), 1);
        if (img.data.length > 0) img.data[0] = Math.max(0, img.data[0] + shift);
        ctx.putImageData(img, 0, 0);
    }
    return _origToBlob.apply(this, arguments);
});

// ---- 12. AudioContext fingerprint ----
if (typeof AudioContext !== 'undefined') {
    const _origCreateOsc = AudioContext.prototype.createOscillator;
    AudioContext.prototype.createOscillator = _makeNative(function createOscillator() {
        const osc = _origCreateOsc.call(this);
        const _origConnect = osc.connect.bind(osc);
        osc.connect = _makeNative(function connect(dest) {
            if (dest instanceof AnalyserNode) {
                const gain = this.context.createGain();
                gain.gain.value = 1 + (Math.random() * 0.0001 - 0.00005);
                _origConnect(gain);
                gain.connect(dest);
                return dest;
            }
            return _origConnect(dest);
        });
        return osc;
    });
}

// ---- 13. WebRTC: prevent real IP leak ----
if (typeof RTCPeerConnection !== 'undefined') {
    const _OrigRTC = RTCPeerConnection;
    window.RTCPeerConnection = _makeNative(function RTCPeerConnection(cfg, constraints) {
        if (cfg && cfg.iceServers) {
            cfg.iceServers = cfg.iceServers.filter(
                s => !(s.urls && /stun:|turn:/.test(s.urls.toString()))
            );
        }
        return new _OrigRTC(cfg, constraints);
    });
    window.RTCPeerConnection.prototype = _OrigRTC.prototype;
}

// ---- 14. Notification.permission default ----
if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
    _defineGetter(Notification, 'permission', function permission() { return 'default'; });
}

// ---- 15. iframe contentWindow / contentDocument ----
const _origContentWindow = Object.getOwnPropertyDescriptor(
    HTMLIFrameElement.prototype, 'contentWindow'
);
if (_origContentWindow && _origContentWindow.get) {
    _defineGetter(HTMLIFrameElement.prototype, 'contentWindow',
        _makeNative(function contentWindow() {
            return _origContentWindow.get.call(this);
        })
    );
}

// ---- 16. Brave / headless detection CSS queries ----
try {
    const _origMatchMedia = window.matchMedia.bind(window);
    window.matchMedia = _makeNative(function matchMedia(query) {
        if (query === '(prefers-reduced-motion: reduce)')
            return { matches: false, media: query, addListener: ()=>{}, removeListener: ()=>{} };
        return _origMatchMedia(query);
    });
} catch(_) {}

// ---- 17. navigator.userAgentData (Client Hints API) ----
if (!navigator.userAgentData) {
    const _uaData = {
        brands: [
            { brand: 'Chromium', version: '131' },
            { brand: 'Not_A Brand', version: '24' },
            { brand: 'Google Chrome', version: '131' },
        ],
        mobile: false,
        platform: 'Windows',
        getHighEntropyValues: _makeNative(function getHighEntropyValues(hints) {
            return Promise.resolve({
                architecture: 'x86',
                bitness: '64',
                brands: [
                    { brand: 'Chromium', version: '131.0.0.0' },
                    { brand: 'Not_A Brand', version: '24.0.0.0' },
                    { brand: 'Google Chrome', version: '131.0.0.0' },
                ],
                fullVersionList: [
                    { brand: 'Chromium', version: '131.0.6778.86' },
                    { brand: 'Not_A Brand', version: '24.0.0.0' },
                    { brand: 'Google Chrome', version: '131.0.6778.86' },
                ],
                mobile: false,
                model: '',
                platform: 'Windows',
                platformVersion: '15.0.0',
                uaFullVersion: '131.0.6778.86',
                wow64: false,
            });
        }),
        toJSON: _makeNative(function toJSON() {
            return {
                brands: this.brands,
                mobile: this.mobile,
                platform: this.platform,
            };
        }),
    };
    Object.defineProperty(navigator, 'userAgentData', {
        get: _makeNative(function userAgentData() { return _uaData; }),
        configurable: true,
    });
}

// ---- 18. Battery API ----
if (navigator.getBattery) {
    const _origGetBattery = navigator.getBattery.bind(navigator);
    navigator.getBattery = _makeNative(function getBattery() {
        return _origGetBattery().then(function(battery) {
            try {
                Object.defineProperties(battery, {
                    charging: { get: () => true, configurable: true },
                    chargingTime: { get: () => 0, configurable: true },
                    dischargingTime: { get: () => Infinity, configurable: true },
                    level: { get: () => 1.0, configurable: true },
                });
            } catch(_) {}
            return battery;
        });
    });
}

// ---- 19. MediaDevices.enumerateDevices (look like a real machine) ----
if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {
    const _origEnum = navigator.mediaDevices.enumerateDevices.bind(navigator.mediaDevices);
    navigator.mediaDevices.enumerateDevices = _makeNative(function enumerateDevices() {
        return _origEnum().then(function(devices) {
            if (devices.length === 0) {
                return [
                    { deviceId: 'default', kind: 'audioinput',  label: '', groupId: 'g1' },
                    { deviceId: 'comms',   kind: 'audioinput',  label: '', groupId: 'g1' },
                    { deviceId: 'default', kind: 'audiooutput', label: '', groupId: 'g2' },
                    { deviceId: 'vid1',    kind: 'videoinput',  label: '', groupId: 'g3' },
                ];
            }
            return devices;
        });
    });
}

// ---- 20. Remove CDP / automation artifacts from window & document ----
(function _cleanAutomationArtifacts() {
    const domTargets = [window, document];
    const patterns = [
        /^__webdriver/i, /^__selenium/i, /^__fxdriver/i,
        /^__driver/i, /^\$cdc_/, /^\$wdc_/,
        /^cdc_/, /^wdc_/, /callPhantom/i, /phantom/i,
        /_Selenium_IDE/i, /callSelenium/i, /domAutomation/i,
        /domAutomationController/i,
    ];
    for (const target of domTargets) {
        try {
            for (const key of Object.getOwnPropertyNames(target)) {
                if (patterns.some(p => p.test(key))) {
                    try { delete target[key]; } catch(_) {}
                }
            }
        } catch(_) {}
    }
})();

// ---- 21. Error stack trace scrubbing ----
const _OrigError = Error;
const _origCaptureStack = Error.captureStackTrace;
if (_origCaptureStack) {
    Error.captureStackTrace = _makeNative(function captureStackTrace(target, ctor) {
        _origCaptureStack.call(_OrigError, target, ctor);
        if (target.stack) {
            target.stack = target.stack
                .split('\n')
                .filter(l => !/playwright|puppeteer|__pw_|__wdc_|DevTools/i.test(l))
                .join('\n');
        }
    });
}

// ---- 22. Keyboard / Pointer event isTrusted can't be spoofed, but ----
// ensure automation-dispatched events look normal by patching
// getOwnPropertyDescriptor for isTrusted (advanced detectors)
try {
    const _origGetOwnPD = Object.getOwnPropertyDescriptor;
    Object.getOwnPropertyDescriptor = _makeNative(function getOwnPropertyDescriptor(obj, prop) {
        if (prop === 'isTrusted' && (obj instanceof Event || obj === Event.prototype)) {
            return { get: () => true, configurable: false };
        }
        return _origGetOwnPD.call(Object, obj, prop);
    });
} catch(_) {}

})();
"""


def _stealth_context_options() -> dict[str, Any]:
    """Return context kwargs that make the browser look like a real user."""
    return {
        "user_agent": _STEALTH_USER_AGENT,
        "viewport": {"width": 1920, "height": 1080},
        "screen": {"width": 1920, "height": 1080},
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        "color_scheme": "light",
        "device_scale_factor": 1,
        "has_touch": False,
        "is_mobile": False,
        "extra_http_headers": {
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        },
    }


def _detect_chrome_executable() -> str | None:
    """Auto-detect the user's installed Chrome/Edge executable path."""
    candidates: list[str] = []
    if sys.platform == "win32":
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env, "")
            if base:
                candidates.append(
                    os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
                )
                candidates.append(
                    os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                )
    elif sys.platform == "darwin":
        candidates.extend([
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ])
    else:
        candidates.extend(["google-chrome", "google-chrome-stable", "chromium-browser"])
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _detect_chrome_user_data_dir() -> str | None:
    """Auto-detect the default Chrome user data directory."""
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            d = os.path.join(local, "Google", "Chrome", "User Data")
            if os.path.isdir(d):
                return d
    elif sys.platform == "darwin":
        d = os.path.expanduser("~/Library/Application Support/Google/Chrome")
        if os.path.isdir(d):
            return d
    else:
        d = os.path.expanduser("~/.config/google-chrome")
        if os.path.isdir(d):
            return d
    return None


# Process-global browser state (one browser, multiple pages by page_id)
_state: dict[str, Any] = {
    "playwright": None,
    "browser": None,
    "context": None,
    "pages": {},
    "refs": {},  # page_id -> ref -> {role, name?, nth?}
    "refs_frame": {},  # page_id -> frame for last snapshot
    "console_logs": {},  # page_id -> list of {level, text}
    "network_requests": {},  # page_id -> list of request dicts
    "pending_dialogs": {},  # page_id -> dialog handlers
    "pending_file_choosers": {},  # page_id -> FileChooser list
    "headless": True,
    "current_page_id": None,
    "page_counter": 0,  # monotonic counter for page_N ids, avoids reuse after close
}


def _tool_response(text: str) -> ToolResponse:
    """Wrap text for agentscope Toolkit (return ToolResponse)."""
    return ToolResponse(
        content=[TextBlock(type="text", text=text)],
    )


# Actions that change page state and should auto-return a snapshot.
_MUTATING_ACTIONS: set[str] = {
    "open", "navigate", "navigate_back", "click", "click_at",
    "type", "press_key", "select_option", "fill_form",
    "hover", "drag", "handle_dialog", "wait_for",
    "eval", "evaluate", "scroll",
}

# Max chars for the auto-appended snapshot text.
_AUTO_SNAPSHOT_MAX_CHARS = 8000


async def _auto_snapshot_text(
    page_id: str,
    frame_selector: str = "",
) -> str | None:
    """Generate a compact snapshot to append after mutating actions.

    Returns the snapshot text (truncated) or None on failure.
    """
    try:
        page = _get_page(page_id)
        if not page:
            return None
        root = _get_root(page, page_id, frame_selector)
        locator = root.locator(":root")
        raw = await locator.aria_snapshot()
        raw_str = str(raw) if raw is not None else ""
        snapshot, refs = build_role_snapshot_from_aria(
            raw_str, interactive=False, compact=True,
        )
        _state["refs"][page_id] = refs
        _state["refs_frame"][page_id] = (
            frame_selector.strip() if frame_selector else ""
        )
        if len(snapshot) > _AUTO_SNAPSHOT_MAX_CHARS:
            snapshot = snapshot[:_AUTO_SNAPSHOT_MAX_CHARS] + "\n... (truncated)"
        ref_list = list(refs.keys())
        return json.dumps(
            {
                "snapshot": snapshot,
                "refs": ref_list,
                "url": page.url,
                "note": "Text-only structure. Use action=screenshot to see visual content.",
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as exc:
        logger.debug("Auto-snapshot failed: %s", exc)
        return None


def _append_snapshot_to_response(
    response: ToolResponse,
    snapshot_text: str,
) -> ToolResponse:
    """Append auto-snapshot text block to an existing ToolResponse."""
    new_content = list(response.content) + [
        TextBlock(
            type="text",
            text=f"\n[Auto-snapshot (interactive elements)]\n{snapshot_text}\n[/Auto-snapshot]",
        ),
    ]
    return ToolResponse(content=new_content)


def _ensure_playwright_async():
    """Import async_playwright; raise ImportError with hint if missing."""
    try:
        from playwright.async_api import async_playwright

        return async_playwright
    except ImportError as exc:
        raise ImportError(
            "Playwright not installed. Install with: pip install playwright "
            "&& python -m playwright install",
        ) from exc


def _parse_json_param(value: str, default: Any = None):
    """Parse optional JSON string param (e.g. fields, paths, values)."""
    if not value or not isinstance(value, str):
        return default
    value = value.strip()
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        if "," in value:
            return [x.strip() for x in value.split(",")]
        return default


async def browser_use(  # pylint: disable=R0911,R0912
    action: str,
    url: str = "",
    page_id: str = "default",
    selector: str = "",
    text: str = "",
    code: str = "",
    path: str = "",
    wait: int = 0,
    full_page: bool = False,
    width: int = 0,
    height: int = 0,
    level: str = "info",
    filename: str = "",
    accept: bool = True,
    prompt_text: str = "",
    ref: str = "",
    element: str = "",
    paths_json: str = "",
    fields_json: str = "",
    key: str = "",
    submit: bool = False,
    slowly: bool = False,
    include_static: bool = False,
    screenshot_type: str = "png",
    snapshot_filename: str = "",
    double_click: bool = False,
    button: str = "left",
    modifiers_json: str = "",
    start_ref: str = "",
    end_ref: str = "",
    start_selector: str = "",
    end_selector: str = "",
    start_element: str = "",
    end_element: str = "",
    values_json: str = "",
    tab_action: str = "",
    index: int = -1,
    wait_time: float = 0,
    text_gone: str = "",
    frame_selector: str = "",
    headed: bool = False,
    user_data_dir: str = "",
    channel: str = "",
    labels: bool = False,
    x: int = 0,
    y: int = 0,
) -> ToolResponse:
    """Browser control (Playwright). One tool, many actions via the `action` param.

    ## Typical workflows

    BROWSE: start(headed=True) -> open(url) -> read auto-snapshot -> click(ref) -> read auto-snapshot -> ...
    NAVIGATE: navigate(url) to go to a new URL on an existing page (open creates the first page)
    VISUAL: screenshot(ref, path) -> scrolls to element, captures viewport with context -> VLM auto-describes
    FIND:   snapshot() -> search for ref in tree -> click(ref)

    ## Key behaviors

    - Actions that change page state (click, type, navigate, eval, etc.)
      automatically return an updated accessibility snapshot.
      No need to call snapshot after every action. Use the auto-snapshot to
      understand page structure, available elements, and text content.
    - FAST path: snapshot/auto-snapshot (text tree, instant). Use this for
      navigation, finding elements, reading text content, checking structure.
    - SLOW path: screenshot (triggers vision analysis, takes 10-30s). Use
      when you need VISUAL content like images, thumbnails,
      colors, or visual layout that the text snapshot cannot provide.
    - With ref (e.g. screenshot(ref="e73")), screenshot scrolls to the
      element and captures the viewport with surrounding context.
    - Target elements by ref (from snapshot), e.g. click(ref="e5"). Prefer ref over selector.

    ## Actions

    Lifecycle: start, stop, install
    Navigation: open(url), navigate(url), navigate_back, close
    Inspection: snapshot, screenshot
    Interaction: click(ref), click_at(x,y), type(ref,text), press_key(key),
                 hover(ref), drag(start_ref,end_ref), select_option(ref,values_json),
                 fill_form(fields_json), file_upload(paths_json), handle_dialog(accept)
    Advanced: eval(code), evaluate(ref,code), run_code(code), resize(width,height),
              console_messages, network_requests, tabs(tab_action), wait_for, pdf

    ## Parameters (by action)

    action (str): Required. See list above.
    url (str): For open, navigate.
    ref (str): Element ref from snapshot (e.g. "e5"). For click, type, hover, evaluate, select_option, screenshot.
    text (str): For type. Text to enter.
    key (str): For press_key. E.g. "Enter", "Control+a".
    code (str): For eval, evaluate, run_code. JavaScript code.
    path (str): For screenshot, pdf. File path to save.
    selector (str): CSS selector fallback when ref unavailable.
    x, y (int): For click_at. Viewport coordinates.
    headed (bool): For start. True = visible browser window.
    user_data_dir (str): For start. Chrome profile path or "auto".
    channel (str): For start. "chrome", "msedge", or "auto".
    page_id (str): Tab identifier, default "default".
    frame_selector (str): iframe CSS selector for operating inside iframes.
    submit (bool): For type. Press Enter after typing.
    slowly (bool): For type. Type character by character.
    full_page (bool): For screenshot. Capture full page.
    double_click (bool): For click. Double-click instead.
    button (str): For click. "left"/"right"/"middle".
    modifiers_json (str): For click. JSON array, e.g. '["Shift"]'.
    start_ref, end_ref (str): For drag.
    values_json (str): For select_option. JSON array of values.
    fields_json (str): For fill_form. JSON object {field: value}.
    paths_json (str): For file_upload. JSON array of file paths.
    tab_action (str): For tabs. "list"/"new"/"close"/"select".
    index (int): For tabs select. Zero-based tab index.
    accept (bool): For handle_dialog. True=accept, False=dismiss.
    prompt_text (str): For handle_dialog. Input text for prompt dialogs.
    wait_time (float): For wait_for. Seconds to wait.
    text_gone (str): For wait_for. Wait until this text disappears.
    wait (int): For click. Milliseconds to wait before clicking.
    level (str): For console_messages. Filter level.
    filename (str): For console_messages, network_requests. Save to file.
    include_static (bool): For network_requests. Include static resources.
    screenshot_type (str): For screenshot. "png" or "jpeg".
    snapshot_filename (str): For snapshot. Save tree to file.
    """
    action = (action or "").strip().lower()
    if not action:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": "action required"},
                ensure_ascii=False,
                indent=2,
            ),
        )

    page_id = (page_id or "default").strip() or "default"
    current = _state.get("current_page_id")
    pages = _state.get("pages") or {}
    if page_id == "default" and current and current in pages:
        page_id = current
    current = _state.get("current_page_id")
    pages = _state.get("pages") or {}
    if page_id == "default" and current and current in pages:
        page_id = current

    result: ToolResponse | None = None
    try:
        if action == "start":
            return await _action_start(
                headed=headed,
                user_data_dir=user_data_dir,
                channel=channel,
            )
        if action == "stop":
            return await _action_stop()
        if action == "open":
            result = await _action_open(url, page_id)
        elif action == "navigate":
            result = await _action_navigate(url, page_id)
        elif action == "navigate_back":
            result = await _action_navigate_back(page_id)
        elif action in ("screenshot", "take_screenshot"):
            return await _action_screenshot(
                page_id,
                path or filename,
                full_page,
                screenshot_type,
                ref,
                element,
                frame_selector,
            )
        elif action == "snapshot":
            return await _action_snapshot(
                page_id,
                snapshot_filename or filename,
                frame_selector,
                labels=labels,
            )
        elif action == "click_at":
            result = await _action_click_at(
                page_id,
                x,
                y,
                button,
                double_click,
            )
        elif action == "click":
            result = await _action_click(
                page_id,
                selector,
                ref,
                element,
                wait,
                double_click,
                button,
                modifiers_json,
                frame_selector,
            )
        elif action == "type":
            result = await _action_type(
                page_id,
                selector,
                ref,
                element,
                text,
                submit,
                slowly,
                frame_selector,
            )
        elif action == "eval":
            return await _action_eval(page_id, code)
        elif action == "evaluate":
            return await _action_evaluate(
                page_id,
                code,
                ref,
                element,
                frame_selector,
            )
        elif action == "resize":
            return await _action_resize(page_id, width, height)
        elif action == "console_messages":
            return await _action_console_messages(
                page_id,
                level,
                filename or path,
            )
        elif action == "handle_dialog":
            result = await _action_handle_dialog(page_id, accept, prompt_text)
        elif action == "file_upload":
            return await _action_file_upload(page_id, paths_json)
        elif action == "fill_form":
            result = await _action_fill_form(page_id, fields_json)
        elif action == "install":
            return await _action_install()
        elif action == "press_key":
            result = await _action_press_key(page_id, key)
        elif action == "network_requests":
            return await _action_network_requests(
                page_id,
                include_static,
                filename or path,
            )
        elif action == "run_code":
            return await _action_run_code(page_id, code)
        elif action == "drag":
            result = await _action_drag(
                page_id,
                start_ref,
                end_ref,
                start_selector,
                end_selector,
                start_element,
                end_element,
                frame_selector,
            )
        elif action == "hover":
            result = await _action_hover(
                page_id,
                ref,
                element,
                selector,
                frame_selector,
            )
        elif action == "select_option":
            result = await _action_select_option(
                page_id,
                ref,
                element,
                values_json,
                frame_selector,
            )
        elif action == "tabs":
            return await _action_tabs(page_id, tab_action, index)
        elif action == "wait_for":
            result = await _action_wait_for(page_id, wait_time, text, text_gone)
        elif action == "pdf":
            return await _action_pdf(page_id, path)
        elif action == "close":
            return await _action_close(page_id)
        else:
            return _tool_response(
                json.dumps(
                    {"ok": False, "error": f"Unknown action: {action}"},
                    ensure_ascii=False,
                    indent=2,
                ),
            )

        # Auto-append compact snapshot after mutating actions.
        if result is not None and action in _MUTATING_ACTIONS:
            snap = await _auto_snapshot_text(page_id, frame_selector)
            if snap:
                result = _append_snapshot_to_response(result, snap)

        return result
    except Exception as e:
        logger.error("Browser tool error: %s", e, exc_info=True)
        return _tool_response(
            json.dumps(
                {"ok": False, "error": str(e)},
                ensure_ascii=False,
                indent=2,
            ),
        )


def _get_page(page_id: str):
    """Return page for page_id or None if not found."""
    return _state["pages"].get(page_id)


def _get_refs(page_id: str) -> dict[str, dict]:
    """Return refs map for page_id (ref -> {role, name?, nth?})."""
    return _state["refs"].setdefault(page_id, {})


def _get_root(page, _page_id: str, frame_selector: str = ""):
    """Return page or frame for frame_selector (ref/selector)."""
    if not (frame_selector and frame_selector.strip()):
        return page
    return page.frame_locator(frame_selector.strip())


def _get_locator_by_ref(
    page,
    page_id: str,
    ref: str,
    frame_selector: str = "",
):
    """Resolve snapshot ref to locator; frame_selector for iframe."""
    refs = _get_refs(page_id)
    info = refs.get(ref)
    if not info:
        return None
    role = info.get("role", "generic")
    name = info.get("name")
    nth = info.get("nth", 0)
    root = _get_root(page, page_id, frame_selector)
    locator = root.get_by_role(role, name=name or None)
    if nth is not None and nth > 0:
        locator = locator.nth(nth)
    else:
        locator = locator.first
    return locator


def _attach_page_listeners(page, page_id: str) -> None:
    """Attach console and request listeners for a page."""
    logs = _state["console_logs"].setdefault(page_id, [])

    def on_console(msg):
        logs.append({"level": msg.type, "text": msg.text})

    page.on("console", on_console)
    requests_list = _state["network_requests"].setdefault(page_id, [])

    def on_request(req):
        requests_list.append(
            {
                "url": req.url,
                "method": req.method,
                "resourceType": getattr(req, "resource_type", None),
            },
        )

    def on_response(res):
        for r in requests_list:
            if r.get("url") == res.url and "status" not in r:
                r["status"] = res.status
                break

    page.on("request", on_request)
    page.on("response", on_response)
    dialogs = _state["pending_dialogs"].setdefault(page_id, [])

    def on_dialog(dialog):
        dialogs.append(dialog)

    page.on("dialog", on_dialog)
    choosers = _state["pending_file_choosers"].setdefault(page_id, [])

    def on_filechooser(chooser):
        choosers.append(chooser)

    page.on("filechooser", on_filechooser)


def _next_page_id() -> str:
    """Return a unique page_id (page_N).
    Uses monotonic counter so IDs are not reused after close."""
    _state["page_counter"] = _state.get("page_counter", 0) + 1
    return f"page_{_state['page_counter']}"


def _attach_context_listeners(context) -> None:
    """When the page opens a new tab (e.g. target=_blank, window.open),
    register it and set as current."""

    def on_page(page):
        new_id = _next_page_id()
        _state["refs"][new_id] = {}
        _state["console_logs"][new_id] = []
        _state["network_requests"][new_id] = []
        _state["pending_dialogs"][new_id] = []
        _state["pending_file_choosers"][new_id] = []
        _attach_page_listeners(page, new_id)
        _state["pages"][new_id] = page
        _state["current_page_id"] = new_id
        logger.debug(
            "New tab opened by page, registered as page_id=%s",
            new_id,
        )

    context.on("page", on_page)


async def _ensure_browser() -> bool:
    """Start browser if not running. Return True if ready, False on failure."""
    if _state["browser"] is not None and _state["context"] is not None:
        return True
    try:
        async_playwright = _ensure_playwright_async()
        pw = await async_playwright().start()
        pw_browser = await pw.chromium.launch(
            headless=_state["headless"],
            args=_STEALTH_LAUNCH_ARGS,
        )
        context = await pw_browser.new_context(**_stealth_context_options())
        await context.add_init_script(_STEALTH_INIT_JS)
        _attach_context_listeners(context)
        _state["playwright"] = pw
        _state["browser"] = pw_browser
        _state["context"] = context
        return True
    except Exception:
        return False


def _reset_state() -> None:
    """Clear all browser state entries."""
    _state["playwright"] = None
    _state["browser"] = None
    _state["context"] = None
    _state["pages"].clear()
    _state["refs"].clear()
    _state["refs_frame"].clear()
    _state["console_logs"].clear()
    _state["network_requests"].clear()
    _state["pending_dialogs"].clear()
    _state["pending_file_choosers"].clear()
    _state["current_page_id"] = None
    _state["page_counter"] = 0


async def _action_start(
    headed: bool = False,
    user_data_dir: str = "",
    channel: str = "",
) -> ToolResponse:
    need_restart = False
    if _state["browser"] is not None or _state["context"] is not None:
        if headed and _state["headless"]:
            need_restart = True
        elif user_data_dir and not _state.get("persistent"):
            need_restart = True
        else:
            return _tool_response(
                json.dumps(
                    {"ok": True, "message": "Browser already running"},
                    ensure_ascii=False,
                    indent=2,
                ),
            )
    if need_restart:
        try:
            if _state["browser"] is not None:
                await _state["browser"].close()
            elif _state["context"] is not None:
                await _state["context"].close()
            if _state["playwright"] is not None:
                await _state["playwright"].stop()
        except Exception:
            pass
        finally:
            _reset_state()

    _state["headless"] = not headed
    try:
        async_playwright = _ensure_playwright_async()
    except ImportError as e:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": str(e)},
                ensure_ascii=False,
                indent=2,
            ),
        )

    # Resolve user_data_dir="auto"
    resolved_data_dir = ""
    if user_data_dir:
        if user_data_dir.strip().lower() == "auto":
            resolved_data_dir = _detect_chrome_user_data_dir() or ""
            if not resolved_data_dir:
                return _tool_response(
                    json.dumps(
                        {
                            "ok": False,
                            "error": "Could not auto-detect Chrome user data dir. "
                            "Provide an explicit path.",
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
        else:
            resolved_data_dir = user_data_dir.strip()

    # Resolve channel="auto"
    resolved_channel = ""
    if channel:
        ch = channel.strip().lower()
        if ch == "auto":
            exe = _detect_chrome_executable()
            if exe:
                resolved_channel = exe
        elif ch in ("chrome", "msedge", "chromium"):
            resolved_channel = ch
        else:
            resolved_channel = ch

    launch_kwargs: dict[str, Any] = {
        "headless": _state["headless"],
        "args": _STEALTH_LAUNCH_ARGS,
    }
    if resolved_channel:
        if os.path.isfile(resolved_channel):
            launch_kwargs["executable_path"] = resolved_channel
        else:
            launch_kwargs["channel"] = resolved_channel

    try:
        pw = await async_playwright().start()

        if resolved_data_dir:
            # Persistent context: shares cookies/sessions with real browser.
            # launch_persistent_context returns a BrowserContext directly.
            ctx_opts = _stealth_context_options()
            ctx_opts.update(launch_kwargs)
            ctx_opts.pop("headless", None)
            context = await pw.chromium.launch_persistent_context(
                resolved_data_dir,
                headless=_state["headless"],
                **ctx_opts,
            )
            await context.add_init_script(_STEALTH_INIT_JS)
            _attach_context_listeners(context)
            _state["playwright"] = pw
            _state["browser"] = None  # no separate browser object
            _state["context"] = context
            _state["persistent"] = True

            # Persistent context may already have pages open
            for page in context.pages:
                pid = _next_page_id()
                _state["pages"][pid] = page
                _state["refs"][pid] = {}
                _state["console_logs"][pid] = []
                _state["network_requests"][pid] = []
                _state["pending_dialogs"][pid] = []
                _state["pending_file_choosers"][pid] = []
                _attach_page_listeners(page, pid)
                _state["current_page_id"] = pid
        else:
            pw_browser = await pw.chromium.launch(**launch_kwargs)
            context = await pw_browser.new_context(**_stealth_context_options())
            await context.add_init_script(_STEALTH_INIT_JS)
            _attach_context_listeners(context)
            _state["playwright"] = pw
            _state["browser"] = pw_browser
            _state["context"] = context
            _state["persistent"] = False

        parts = []
        if not _state["headless"]:
            parts.append("visible window")
        if resolved_data_dir:
            parts.append(f"profile={resolved_data_dir}")
        if resolved_channel:
            parts.append(f"channel={resolved_channel}")
        detail = f" ({', '.join(parts)})" if parts else ""
        return _tool_response(
            json.dumps(
                {"ok": True, "message": f"Browser started{detail}"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    except Exception as e:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Browser start failed: {e!s}"},
                ensure_ascii=False,
                indent=2,
            ),
        )


async def _action_stop() -> ToolResponse:
    if _state["browser"] is None and _state["context"] is None:
        return _tool_response(
            json.dumps(
                {"ok": True, "message": "Browser not running"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    try:
        if _state["browser"] is not None:
            await _state["browser"].close()
        elif _state["context"] is not None:
            await _state["context"].close()
        if _state["playwright"] is not None:
            await _state["playwright"].stop()
    except Exception as e:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Browser stop failed: {e!s}"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    finally:
        _reset_state()
        _state["headless"] = True
    return _tool_response(
        json.dumps(
            {"ok": True, "message": "Browser stopped"},
            ensure_ascii=False,
            indent=2,
        ),
    )


async def _action_open(url: str, page_id: str) -> ToolResponse:
    url = (url or "").strip()
    if not url:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": "url required for open"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    if not await _ensure_browser():
        return _tool_response(
            json.dumps(
                {"ok": False, "error": "Browser not started"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    try:
        page = await _state["context"].new_page()
        _state["refs"][page_id] = {}
        _state["console_logs"][page_id] = []
        _state["network_requests"][page_id] = []
        _state["pending_dialogs"][page_id] = []
        _state["pending_file_choosers"][page_id] = []
        _attach_page_listeners(page, page_id)
        await page.goto(url)
        _state["pages"][page_id] = page
        _state["current_page_id"] = page_id
        _state["current_page_id"] = page_id
        return _tool_response(
            json.dumps(
                {
                    "ok": True,
                    "message": f"Opened {url}",
                    "page_id": page_id,
                    "url": url,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    except Exception as e:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Open failed: {e!s}"},
                ensure_ascii=False,
                indent=2,
            ),
        )


async def _action_navigate(url: str, page_id: str) -> ToolResponse:
    url = (url or "").strip()
    if not url:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": "url required for navigate"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    page = _get_page(page_id)
    if not page:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Page '{page_id}' not found"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    try:
        await page.goto(url)
        _state["current_page_id"] = page_id
        _state["current_page_id"] = page_id
        return _tool_response(
            json.dumps(
                {
                    "ok": True,
                    "message": f"Navigated to {url}",
                    "url": page.url,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    except Exception as e:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Navigate failed: {e!s}"},
                ensure_ascii=False,
                indent=2,
            ),
        )


async def _action_screenshot(
    page_id: str,
    path: str,
    full_page: bool,
    screenshot_type: str = "png",
    ref: str = "",
    element: str = "",  # pylint: disable=unused-argument
    frame_selector: str = "",
) -> ToolResponse:
    path = (path or "").strip()
    if not path:
        ext = "jpeg" if screenshot_type == "jpeg" else "png"
        path = f"page-{int(time.time())}.{ext}"
    page = _get_page(page_id)
    if not page:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Page '{page_id}' not found"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    try:
        if ref and ref.strip():
            locator = _get_locator_by_ref(
                page,
                page_id,
                ref.strip(),
                frame_selector,
            )
            if locator is None:
                return _tool_response(
                    json.dumps(
                        {"ok": False, "error": f"Unknown ref: {ref}"},
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
            await locator.evaluate(
                'el => el.scrollIntoView({block:"center",inline:"center"})',
            )
            await page.wait_for_timeout(300)
            await page.screenshot(
                path=path,
                type=screenshot_type if screenshot_type == "jpeg" else "png",
            )
        else:
            if frame_selector and frame_selector.strip():
                root = _get_root(page, page_id, frame_selector)
                locator = root.locator("body").first
                await locator.screenshot(
                    path=path,
                    type=screenshot_type
                    if screenshot_type == "jpeg"
                    else "png",
                )
            else:
                await page.screenshot(
                    path=path,
                    full_page=full_page,
                    type=screenshot_type
                    if screenshot_type == "jpeg"
                    else "png",
                )
        abs_path = str(Path(path).resolve())
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=json.dumps(
                        {
                            "ok": True,
                            "message": f"Screenshot saved to {path}",
                            "path": abs_path,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                ),
                ImageBlock(
                    type="image",
                    source=URLSource(
                        type="url",
                        url=Path(abs_path).as_uri(),
                    ),
                ),
            ],
        )
    except Exception as e:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Screenshot failed: {e!s}"},
                ensure_ascii=False,
                indent=2,
            ),
        )


async def _action_click(  # pylint: disable=too-many-branches
    page_id: str,
    selector: str,
    ref: str = "",
    element: str = "",  # pylint: disable=unused-argument
    wait: int = 0,
    double_click: bool = False,
    button: str = "left",
    modifiers_json: str = "",
    frame_selector: str = "",
) -> ToolResponse:
    ref = (ref or "").strip()
    selector = (selector or "").strip()
    if not ref and not selector:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": "selector or ref required for click"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    page = _get_page(page_id)
    if not page:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Page '{page_id}' not found"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    try:
        if wait > 0:
            await asyncio.sleep(wait / 1000.0)
        mods = _parse_json_param(modifiers_json, [])
        if not isinstance(mods, list):
            mods = []
        kwargs = {
            "button": button
            if button in ("left", "right", "middle")
            else "left",
        }
        if mods:
            kwargs["modifiers"] = [
                m
                for m in mods
                if m in ("Alt", "Control", "ControlOrMeta", "Meta", "Shift")
            ]
        if ref:
            locator = _get_locator_by_ref(page, page_id, ref, frame_selector)
            if locator is None:
                return _tool_response(
                    json.dumps(
                        {"ok": False, "error": f"Unknown ref: {ref}"},
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
            if double_click:
                await locator.dblclick(**kwargs)
            else:
                await locator.click(**kwargs)
        else:
            root = _get_root(page, page_id, frame_selector)
            locator = root.locator(selector).first
            if double_click:
                await locator.dblclick(**kwargs)
            else:
                await locator.click(**kwargs)
        return _tool_response(
            json.dumps(
                {"ok": True, "message": f"Clicked {ref or selector}"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    except Exception as e:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Click failed: {e!s}"},
                ensure_ascii=False,
                indent=2,
            ),
        )


async def _action_click_at(
    page_id: str,
    x: int,
    y: int,
    button: str = "left",
    double_click: bool = False,
) -> ToolResponse:
    """Click at raw viewport coordinates (x, y)."""
    page = _get_page(page_id)
    if not page:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Page '{page_id}' not found"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    btn = button if button in ("left", "right", "middle") else "left"
    try:
        if double_click:
            await page.mouse.dblclick(x, y, button=btn)
        else:
            await page.mouse.click(x, y, button=btn)
        return _tool_response(
            json.dumps(
                {
                    "ok": True,
                    "message": f"Clicked at ({x}, {y})",
                    "x": x,
                    "y": y,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    except Exception as e:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"click_at failed: {e!s}"},
                ensure_ascii=False,
                indent=2,
            ),
        )


async def _action_type(
    page_id: str,
    selector: str,
    ref: str = "",
    element: str = "",  # pylint: disable=unused-argument
    text: str = "",
    submit: bool = False,
    slowly: bool = False,
    frame_selector: str = "",
) -> ToolResponse:
    ref = (ref or "").strip()
    selector = (selector or "").strip()
    if not ref and not selector:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": "selector or ref required for type"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    page = _get_page(page_id)
    if not page:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Page '{page_id}' not found"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    try:
        if ref:
            locator = _get_locator_by_ref(page, page_id, ref, frame_selector)
            if locator is None:
                return _tool_response(
                    json.dumps(
                        {"ok": False, "error": f"Unknown ref: {ref}"},
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
            if slowly:
                await locator.press_sequentially(text or "")
            else:
                await locator.fill(text or "")
            if submit:
                await locator.press("Enter")
        else:
            root = _get_root(page, page_id, frame_selector)
            loc = root.locator(selector).first
            if slowly:
                await loc.press_sequentially(text or "")
            else:
                await loc.fill(text or "")
            if submit:
                await loc.press("Enter")
        return _tool_response(
            json.dumps(
                {"ok": True, "message": f"Typed into {ref or selector}"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    except Exception as e:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Type failed: {e!s}"},
                ensure_ascii=False,
                indent=2,
            ),
        )


async def _action_eval(page_id: str, code: str) -> ToolResponse:
    code = (code or "").strip()
    if not code:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": "code required for eval"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    page = _get_page(page_id)
    if not page:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Page '{page_id}' not found"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    try:
        if code.strip().startswith("(") or code.strip().startswith("function"):
            result = await page.evaluate(code)
        else:
            result = await page.evaluate(f"() => {{ return ({code}); }}")
        try:
            out = json.dumps(
                {"ok": True, "result": result},
                ensure_ascii=False,
                indent=2,
            )
        except TypeError:
            out = json.dumps(
                {"ok": True, "result": str(result)},
                ensure_ascii=False,
                indent=2,
            )
        return _tool_response(out)
    except Exception as e:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Eval failed: {e!s}"},
                ensure_ascii=False,
                indent=2,
            ),
        )


async def _action_pdf(page_id: str, path: str) -> ToolResponse:
    path = (path or "page.pdf").strip() or "page.pdf"
    page = _get_page(page_id)
    if not page:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Page '{page_id}' not found"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    try:
        await page.pdf(path=path)
        return _tool_response(
            json.dumps(
                {"ok": True, "message": f"PDF saved to {path}", "path": path},
                ensure_ascii=False,
                indent=2,
            ),
        )
    except Exception as e:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"PDF failed: {e!s}"},
                ensure_ascii=False,
                indent=2,
            ),
        )


async def _action_close(page_id: str) -> ToolResponse:
    page = _get_page(page_id)
    if not page:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Page '{page_id}' not found"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    try:
        await page.close()
        del _state["pages"][page_id]
        for key in (
            "refs",
            "refs_frame",
            "console_logs",
            "network_requests",
            "pending_dialogs",
            "pending_file_choosers",
        ):
            _state[key].pop(page_id, None)
        if _state.get("current_page_id") == page_id:
            remaining = list(_state["pages"].keys())
            _state["current_page_id"] = remaining[0] if remaining else None
        if _state.get("current_page_id") == page_id:
            remaining = list(_state["pages"].keys())
            _state["current_page_id"] = remaining[0] if remaining else None
        return _tool_response(
            json.dumps(
                {"ok": True, "message": f"Closed page '{page_id}'"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    except Exception as e:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Close failed: {e!s}"},
                ensure_ascii=False,
                indent=2,
            ),
        )


_MAX_LABELS = 100

_JS_OVERLAY_LABELS = """
(labels) => {
    const existing = document.querySelectorAll("[data-copaw-labels]");
    existing.forEach(el => el.remove());
    const root = document.createElement("div");
    root.setAttribute("data-copaw-labels", "1");
    root.style.position = "fixed";
    root.style.left = "0";
    root.style.top = "0";
    root.style.zIndex = "2147483647";
    root.style.pointerEvents = "none";
    root.style.fontFamily = '"SF Mono",SFMono-Regular,Menlo,Monaco,Consolas,monospace';
    const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
    for (const lb of labels) {
        const box = document.createElement("div");
        box.setAttribute("data-copaw-labels", "1");
        box.style.position = "absolute";
        box.style.left = lb.x + "px";
        box.style.top = lb.y + "px";
        box.style.width = lb.w + "px";
        box.style.height = lb.h + "px";
        box.style.border = "2px solid #ffb020";
        box.style.boxSizing = "border-box";
        const tag = document.createElement("div");
        tag.setAttribute("data-copaw-labels", "1");
        tag.textContent = lb.ref;
        tag.style.position = "absolute";
        tag.style.left = lb.x + "px";
        tag.style.top = clamp(lb.y - 18, 0, 20000) + "px";
        tag.style.background = "#ffb020";
        tag.style.color = "#1a1a1a";
        tag.style.fontSize = "12px";
        tag.style.lineHeight = "14px";
        tag.style.padding = "1px 4px";
        tag.style.borderRadius = "3px";
        tag.style.boxShadow = "0 1px 2px rgba(0,0,0,0.35)";
        tag.style.whiteSpace = "nowrap";
        root.appendChild(box);
        root.appendChild(tag);
    }
    document.documentElement.appendChild(root);
}
"""

_JS_REMOVE_LABELS = """
() => {
    const existing = document.querySelectorAll("[data-copaw-labels]");
    existing.forEach(el => el.remove());
}
"""

_BBOX_TIMEOUT_MS = 200


async def _overlay_labels_and_screenshot(
    page,
    refs_dict: dict[str, dict],
    page_id: str,
    frame_selector: str = "",
) -> tuple[bytes, int, int]:
    """Overlay ref bounding-box labels on page, screenshot, then remove.

    Uses Playwright's native locator resolution with short timeouts.
    Returns (screenshot_bytes, labels_drawn, labels_skipped).
    """
    viewport = await page.evaluate(
        "() => ({"
        "width: window.innerWidth || 0,"
        "height: window.innerHeight || 0"
        "})",
    )
    vw, vh = viewport["width"], viewport["height"]

    boxes: list[dict] = []
    skipped = 0
    for ref in refs_dict:
        if len(boxes) >= _MAX_LABELS:
            skipped += 1
            continue
        locator = _get_locator_by_ref(page, page_id, ref, frame_selector)
        if locator is None:
            skipped += 1
            continue
        try:
            box = await locator.bounding_box(timeout=_BBOX_TIMEOUT_MS)
        except Exception:
            skipped += 1
            continue
        if box is None:
            skipped += 1
            continue
        x, y, w, h = box["x"], box["y"], box["width"], box["height"]
        if x + w < 0 or x > vw or y + h < 0 or y > vh:
            skipped += 1
            continue
        boxes.append({
            "ref": ref,
            "x": x, "y": y,
            "w": max(1, w), "h": max(1, h),
        })

    try:
        if boxes:
            await page.evaluate(_JS_OVERLAY_LABELS, boxes)
        screenshot_bytes = await page.screenshot(type="png")
        return screenshot_bytes, len(boxes), skipped
    finally:
        try:
            await page.evaluate(_JS_REMOVE_LABELS)
        except Exception:
            pass


async def get_labeled_screenshot() -> bytes | None:
    """Take a viewport screenshot with ref label overlays.

    Returns PNG bytes, or None if no browser/page/refs are available.
    Called by VLM prepass to provide grounded visual descriptions.

    A fresh aria_snapshot is taken to capture lazy-loaded content that
    may have appeared since the last auto-snapshot.
    """
    page_id = _state.get("current_page_id")
    if not page_id:
        return None
    page = _get_page(page_id)
    if not page:
        return None
    frame_selector = _state.get("refs_frame", {}).get(page_id, "")

    try:
        root = _get_root(page, page_id, frame_selector)
        locator = root.locator(":root")
        raw = await locator.aria_snapshot()
        raw_str = str(raw) if raw is not None else ""
        _, refs = build_role_snapshot_from_aria(
            raw_str, interactive=False, compact=False,
        )
        _state["refs"][page_id] = refs
        logger.debug(
            "Refreshed refs for labeled screenshot: %d refs", len(refs),
        )
    except Exception as exc:
        logger.debug("Snapshot refresh failed, using cached refs: %s", exc)
        refs = _get_refs(page_id)

    if not refs:
        return None

    try:
        img_bytes, drawn, _ = await _overlay_labels_and_screenshot(
            page, refs, page_id, frame_selector,
        )
        if drawn == 0:
            return None
        logger.debug("Labeled screenshot: %d refs drawn", drawn)
        return img_bytes
    except Exception as exc:
        logger.debug("get_labeled_screenshot failed: %s", exc)
        return None


async def _action_snapshot(
    page_id: str,
    filename: str,
    frame_selector: str = "",
    labels: bool = False,
) -> ToolResponse:
    page = _get_page(page_id)
    if not page:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Page '{page_id}' not found"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    try:
        root = _get_root(page, page_id, frame_selector)
        locator = root.locator(":root")
        raw = await locator.aria_snapshot()
        raw_str = str(raw) if raw is not None else ""
        snapshot, refs = build_role_snapshot_from_aria(
            raw_str,
            interactive=False,
            compact=False,
        )
        _state["refs"][page_id] = refs
        _state["refs_frame"][page_id] = (
            frame_selector.strip() if frame_selector else ""
        )
        out: dict[str, Any] = {
            "ok": True,
            "snapshot": snapshot,
            "refs": list(refs.keys()),
            "url": page.url,
            "note": "This is the text accessibility tree. To see VISUAL content (images, colors, thumbnails), use action=screenshot instead.",
        }
        if frame_selector and frame_selector.strip():
            out["frame_selector"] = frame_selector.strip()
        if filename and filename.strip():
            with open(filename.strip(), "w", encoding="utf-8") as f:
                f.write(snapshot)
            out["filename"] = filename.strip()

        if not labels:
            return _tool_response(
                json.dumps(out, ensure_ascii=False, indent=2),
            )

        img_bytes, drawn, skipped = await _overlay_labels_and_screenshot(
            page,
            refs,
            page_id,
            frame_selector,
        )
        out["labels"] = True
        out["labels_drawn"] = drawn
        out["labels_skipped"] = skipped

        img_path = f"snapshot-labels-{int(time.time())}.png"
        Path(img_path).write_bytes(img_bytes)
        abs_path = str(Path(img_path).resolve())
        out["image_path"] = abs_path

        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=json.dumps(out, ensure_ascii=False, indent=2),
                ),
                ImageBlock(
                    type="image",
                    source=URLSource(
                        type="url",
                        url=Path(abs_path).as_uri(),
                    ),
                ),
            ],
        )
    except Exception as e:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Snapshot failed: {e!s}"},
                ensure_ascii=False,
                indent=2,
            ),
        )


async def _action_navigate_back(page_id: str) -> ToolResponse:
    page = _get_page(page_id)
    if not page:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Page '{page_id}' not found"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    try:
        await page.go_back()
        return _tool_response(
            json.dumps(
                {"ok": True, "message": "Navigated back", "url": page.url},
                ensure_ascii=False,
                indent=2,
            ),
        )
    except Exception as e:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Navigate back failed: {e!s}"},
                ensure_ascii=False,
                indent=2,
            ),
        )


async def _action_evaluate(
    page_id: str,
    code: str,
    ref: str = "",
    element: str = "",  # pylint: disable=unused-argument
    frame_selector: str = "",
) -> ToolResponse:
    code = (code or "").strip()
    if not code:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": "code required for evaluate"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    page = _get_page(page_id)
    if not page:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Page '{page_id}' not found"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    try:
        if ref and ref.strip():
            locator = _get_locator_by_ref(
                page,
                page_id,
                ref.strip(),
                frame_selector,
            )
            if locator is None:
                return _tool_response(
                    json.dumps(
                        {"ok": False, "error": f"Unknown ref: {ref}"},
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
            result = await locator.evaluate(code)
        else:
            if code.strip().startswith("(") or code.strip().startswith(
                "function",
            ):
                result = await page.evaluate(code)
            else:
                result = await page.evaluate(f"() => {{ return ({code}); }}")
        try:
            out = json.dumps(
                {"ok": True, "result": result},
                ensure_ascii=False,
                indent=2,
            )
        except TypeError:
            out = json.dumps(
                {"ok": True, "result": str(result)},
                ensure_ascii=False,
                indent=2,
            )
        return _tool_response(out)
    except Exception as e:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Evaluate failed: {e!s}"},
                ensure_ascii=False,
                indent=2,
            ),
        )


async def _action_resize(
    page_id: str,
    width: int,
    height: int,
) -> ToolResponse:
    if width <= 0 or height <= 0:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": "width and height must be positive"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    page = _get_page(page_id)
    if not page:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Page '{page_id}' not found"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    try:
        await page.set_viewport_size({"width": width, "height": height})
        return _tool_response(
            json.dumps(
                {"ok": True, "message": f"Resized to {width}x{height}"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    except Exception as e:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Resize failed: {e!s}"},
                ensure_ascii=False,
                indent=2,
            ),
        )


async def _action_console_messages(
    page_id: str,
    level: str,
    filename: str,
) -> ToolResponse:
    level = (level or "info").strip().lower()
    order = ("error", "warning", "info", "debug")
    idx = order.index(level) if level in order else 2
    page = _get_page(page_id)
    if not page:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Page '{page_id}' not found"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    logs = _state["console_logs"].get(page_id, [])
    filtered = (
        [m for m in logs if order.index(m["level"]) <= idx]
        if level in order
        else logs
    )
    lines = [f"[{m['level']}] {m['text']}" for m in filtered]
    text = "\n".join(lines)
    if filename and filename.strip():
        with open(filename.strip(), "w", encoding="utf-8") as f:
            f.write(text)
        return _tool_response(
            json.dumps(
                {
                    "ok": True,
                    "message": f"Console messages saved to {filename}",
                    "filename": filename.strip(),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    return _tool_response(
        json.dumps(
            {"ok": True, "messages": filtered, "text": text},
            ensure_ascii=False,
            indent=2,
        ),
    )


async def _action_handle_dialog(
    page_id: str,
    accept: bool,
    prompt_text: str,
) -> ToolResponse:
    page = _get_page(page_id)
    if not page:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Page '{page_id}' not found"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    dialogs = _state["pending_dialogs"].get(page_id, [])
    if not dialogs:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": "No pending dialog"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    try:
        dialog = dialogs.pop(0)
        if accept:
            if prompt_text and hasattr(dialog, "accept"):
                await dialog.accept(prompt_text)
            else:
                await dialog.accept()
        else:
            await dialog.dismiss()
        return _tool_response(
            json.dumps(
                {"ok": True, "message": "Dialog handled"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    except Exception as e:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Handle dialog failed: {e!s}"},
                ensure_ascii=False,
                indent=2,
            ),
        )


async def _action_file_upload(page_id: str, paths_json: str) -> ToolResponse:
    page = _get_page(page_id)
    if not page:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Page '{page_id}' not found"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    paths = _parse_json_param(paths_json, [])
    if not isinstance(paths, list):
        paths = []
    try:
        choosers = _state["pending_file_choosers"].get(page_id, [])
        if not choosers:
            return _tool_response(
                json.dumps(
                    {
                        "ok": False,
                        "error": "No chooser. Click upload then file_upload.",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        chooser = choosers.pop(0)
        if paths:
            await chooser.set_files(paths)
            return _tool_response(
                json.dumps(
                    {"ok": True, "message": f"Uploaded {len(paths)} file(s)"},
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        await chooser.set_files([])
        return _tool_response(
            json.dumps(
                {"ok": True, "message": "File chooser cancelled"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    except Exception as e:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"File upload failed: {e!s}"},
                ensure_ascii=False,
                indent=2,
            ),
        )


async def _action_fill_form(page_id: str, fields_json: str) -> ToolResponse:
    page = _get_page(page_id)
    if not page:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Page '{page_id}' not found"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    fields = _parse_json_param(fields_json, [])
    if not isinstance(fields, list) or not fields:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": "fields required (JSON array)"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    refs = _get_refs(page_id)
    # Use last snapshot's frame so fill_form works after iframe snapshot
    frame = _state["refs_frame"].get(page_id, "")
    try:
        for f in fields:
            ref = (f.get("ref") or "").strip()
            if not ref or ref not in refs:
                continue
            locator = _get_locator_by_ref(page, page_id, ref, frame)
            if locator is None:
                continue
            field_type = (f.get("type") or "textbox").lower()
            value = f.get("value")
            if field_type == "checkbox":
                if isinstance(value, str):
                    value = value.strip().lower() in ("true", "1", "yes")
                await locator.set_checked(bool(value))
            elif field_type == "radio":
                await locator.set_checked(True)
            elif field_type == "combobox":
                await locator.select_option(
                    label=value if isinstance(value, str) else None,
                    value=value,
                )
            elif field_type == "slider":
                await locator.fill(str(value))
            else:
                await locator.fill(str(value) if value is not None else "")
        return _tool_response(
            json.dumps(
                {"ok": True, "message": f"Filled {len(fields)} field(s)"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    except Exception as e:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Fill form failed: {e!s}"},
                ensure_ascii=False,
                indent=2,
            ),
        )


async def _action_install() -> ToolResponse:
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install"],
            check=True,
            capture_output=True,
            text=True,
            timeout=120000,
        )
        return _tool_response(
            json.dumps(
                {"ok": True, "message": "Browser installed"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    except Exception as e:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Install failed: {e!s}"},
                ensure_ascii=False,
                indent=2,
            ),
        )


async def _action_press_key(page_id: str, key: str) -> ToolResponse:
    key = (key or "").strip()
    if not key:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": "key required for press_key"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    page = _get_page(page_id)
    if not page:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Page '{page_id}' not found"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    try:
        await page.keyboard.press(key)
        return _tool_response(
            json.dumps(
                {"ok": True, "message": f"Pressed key {key}"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    except Exception as e:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Press key failed: {e!s}"},
                ensure_ascii=False,
                indent=2,
            ),
        )


async def _action_network_requests(
    page_id: str,
    include_static: bool,
    filename: str,
) -> ToolResponse:
    page = _get_page(page_id)
    if not page:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Page '{page_id}' not found"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    requests = _state["network_requests"].get(page_id, [])
    if not include_static:
        static = ("image", "stylesheet", "font", "media")
        requests = [r for r in requests if r.get("resourceType") not in static]
    lines = [
        f"{r.get('method', '')} {r.get('url', '')} {r.get('status', '')}"
        for r in requests
    ]
    text = "\n".join(lines)
    if filename and filename.strip():
        with open(filename.strip(), "w", encoding="utf-8") as f:
            f.write(text)
        return _tool_response(
            json.dumps(
                {
                    "ok": True,
                    "message": f"Network requests saved to {filename}",
                    "filename": filename.strip(),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    return _tool_response(
        json.dumps(
            {"ok": True, "requests": requests, "text": text},
            ensure_ascii=False,
            indent=2,
        ),
    )


async def _action_run_code(page_id: str, code: str) -> ToolResponse:
    """Run JS in page (like eval). Use evaluate for element (ref)."""
    code = (code or "").strip()
    if not code:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": "code required for run_code"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    page = _get_page(page_id)
    if not page:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Page '{page_id}' not found"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    try:
        if code.strip().startswith("(") or code.strip().startswith("function"):
            result = await page.evaluate(code)
        else:
            result = await page.evaluate(f"() => {{ return ({code}); }}")
        try:
            out = json.dumps(
                {"ok": True, "result": result},
                ensure_ascii=False,
                indent=2,
            )
        except TypeError:
            out = json.dumps(
                {"ok": True, "result": str(result)},
                ensure_ascii=False,
                indent=2,
            )
        return _tool_response(out)
    except Exception as e:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Run code failed: {e!s}"},
                ensure_ascii=False,
                indent=2,
            ),
        )


async def _action_drag(
    page_id: str,
    start_ref: str,
    end_ref: str,
    start_selector: str = "",
    end_selector: str = "",
    start_element: str = "",  # pylint: disable=unused-argument
    end_element: str = "",  # pylint: disable=unused-argument
    frame_selector: str = "",
) -> ToolResponse:
    start_ref = (start_ref or "").strip()
    end_ref = (end_ref or "").strip()
    start_selector = (start_selector or "").strip()
    end_selector = (end_selector or "").strip()
    use_refs = bool(start_ref and end_ref)
    use_selectors = bool(start_selector and end_selector)
    if not use_refs and not use_selectors:
        return _tool_response(
            json.dumps(
                {
                    "ok": False,
                    "error": (
                        "drag needs (start_ref,end_ref) or (start_sel,end_sel)"
                    ),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    page = _get_page(page_id)
    if not page:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Page '{page_id}' not found"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    try:
        root = _get_root(page, page_id, frame_selector)
        if use_refs:
            start_locator = _get_locator_by_ref(
                page,
                page_id,
                start_ref,
                frame_selector,
            )
            end_locator = _get_locator_by_ref(
                page,
                page_id,
                end_ref,
                frame_selector,
            )
            if start_locator is None or end_locator is None:
                return _tool_response(
                    json.dumps(
                        {"ok": False, "error": "Unknown ref for drag"},
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
        else:
            start_locator = root.locator(start_selector).first
            end_locator = root.locator(end_selector).first
        await start_locator.drag_to(end_locator)
        return _tool_response(
            json.dumps(
                {"ok": True, "message": "Drag completed"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    except Exception as e:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Drag failed: {e!s}"},
                ensure_ascii=False,
                indent=2,
            ),
        )


async def _action_hover(
    page_id: str,
    ref: str = "",
    element: str = "",  # pylint: disable=unused-argument
    selector: str = "",
    frame_selector: str = "",
) -> ToolResponse:
    ref = (ref or "").strip()
    selector = (selector or "").strip()
    if not ref and not selector:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": "hover requires ref or selector"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    page = _get_page(page_id)
    if not page:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Page '{page_id}' not found"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    try:
        if ref:
            locator = _get_locator_by_ref(page, page_id, ref, frame_selector)
            if locator is None:
                return _tool_response(
                    json.dumps(
                        {"ok": False, "error": f"Unknown ref: {ref}"},
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
        else:
            root = _get_root(page, page_id, frame_selector)
            locator = root.locator(selector).first
        await locator.hover()
        return _tool_response(
            json.dumps(
                {"ok": True, "message": f"Hovered {ref or selector}"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    except Exception as e:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Hover failed: {e!s}"},
                ensure_ascii=False,
                indent=2,
            ),
        )


async def _action_select_option(
    page_id: str,
    ref: str = "",
    element: str = "",  # pylint: disable=unused-argument
    values_json: str = "",
    frame_selector: str = "",
) -> ToolResponse:
    ref = (ref or "").strip()
    values = _parse_json_param(values_json, [])
    if not isinstance(values, list):
        values = [values] if values is not None else []
    if not ref:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": "ref required for select_option"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    if not values:
        return _tool_response(
            json.dumps(
                {
                    "ok": False,
                    "error": "values required (JSON array or comma-separated)",
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    page = _get_page(page_id)
    if not page:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Page '{page_id}' not found"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    try:
        locator = _get_locator_by_ref(page, page_id, ref, frame_selector)
        if locator is None:
            return _tool_response(
                json.dumps(
                    {"ok": False, "error": f"Unknown ref: {ref}"},
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        await locator.select_option(value=values)
        return _tool_response(
            json.dumps(
                {"ok": True, "message": f"Selected {values}"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    except Exception as e:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Select option failed: {e!s}"},
                ensure_ascii=False,
                indent=2,
            ),
        )


async def _action_tabs(  # pylint: disable=too-many-return-statements
    page_id: str,
    tab_action: str,
    index: int,
) -> ToolResponse:
    tab_action = (tab_action or "").strip().lower()
    if not tab_action:
        return _tool_response(
            json.dumps(
                {
                    "ok": False,
                    "error": "tab_action required (list, new, close, select)",
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    pages = _state["pages"]
    page_ids = list(pages.keys())
    if tab_action == "list":
        return _tool_response(
            json.dumps(
                {"ok": True, "tabs": page_ids, "count": len(page_ids)},
                ensure_ascii=False,
                indent=2,
            ),
        )
    if tab_action == "new":
        if not _state["context"]:
            ok = await _ensure_browser()
            if not ok:
                return _tool_response(
                    json.dumps(
                        {"ok": False, "error": "Browser not started"},
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
        try:
            page = await _state["context"].new_page()
            new_id = _next_page_id()
            _state["refs"][new_id] = {}
            _state["console_logs"][new_id] = []
            _state["network_requests"][new_id] = []
            _state["pending_dialogs"][new_id] = []
            _attach_page_listeners(page, new_id)
            _state["pages"][new_id] = page
            _state["current_page_id"] = new_id
            return _tool_response(
                json.dumps(
                    {
                        "ok": True,
                        "page_id": new_id,
                        "tabs": list(_state["pages"].keys()),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        except Exception as e:
            return _tool_response(
                json.dumps(
                    {"ok": False, "error": f"New tab failed: {e!s}"},
                    ensure_ascii=False,
                    indent=2,
                ),
            )
    if tab_action == "close":
        target_id = page_ids[index] if 0 <= index < len(page_ids) else page_id
        return await _action_close(target_id)
    if tab_action == "select":
        target_id = page_ids[index] if 0 <= index < len(page_ids) else page_id
        _state["current_page_id"] = target_id
        _state["current_page_id"] = target_id
        return _tool_response(
            json.dumps(
                {
                    "ok": True,
                    "message": f"Use page_id={target_id} for later actions",
                    "page_id": target_id,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    return _tool_response(
        json.dumps(
            {"ok": False, "error": f"Unknown tab_action: {tab_action}"},
            ensure_ascii=False,
            indent=2,
        ),
    )


async def _action_wait_for(
    page_id: str,
    wait_time: float,
    text: str,
    text_gone: str,
) -> ToolResponse:
    page = _get_page(page_id)
    if not page:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Page '{page_id}' not found"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    try:
        if wait_time and wait_time > 0:
            await asyncio.sleep(wait_time)
        text = (text or "").strip()
        text_gone = (text_gone or "").strip()
        if text:
            await page.get_by_text(text).wait_for(
                state="visible",
                timeout=30000,
            )
        if text_gone:
            await page.get_by_text(text_gone).wait_for(
                state="hidden",
                timeout=30000,
            )
        return _tool_response(
            json.dumps(
                {"ok": True, "message": "Wait completed"},
                ensure_ascii=False,
                indent=2,
            ),
        )
    except Exception as e:
        return _tool_response(
            json.dumps(
                {"ok": False, "error": f"Wait failed: {e!s}"},
                ensure_ascii=False,
                indent=2,
            ),
        )
