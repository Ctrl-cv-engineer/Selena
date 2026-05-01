import base64
import json
import logging
import os
import re
import socket
import struct
import subprocess
import time
from urllib.parse import quote, urlparse

import requests

try:
    from DialogueSystem.browser.browser_control import build_browser_search_url, normalize_browser_url
    from DialogueSystem.config.paths import DATA_DIR
except ImportError:
    from browser_control import build_browser_search_url, normalize_browser_url
    from DialogueSystem.config.paths import DATA_DIR


CHROME_CANDIDATE_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
)
CHROME_DEFAULT_USER_DATA_DIR = os.path.normpath(
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")
)
CHROME_PROFILE_DIR = os.path.join(DATA_DIR, "chrome-browser-profile")
CHROME_DEBUG_PORT = 9222
CHROME_READY_TIMEOUT_SECONDS = 20.0
CHROME_PAGE_TIMEOUT_SECONDS = 25.0
CHROME_NAVIGATION_SETTLE_SECONDS = 2.0


class ChromeBrowserAutomationError(RuntimeError):
    def __init__(self, message: str, *, code: str = "chrome_browser_error"):
        super().__init__(message)
        self.code = code


BROWSER_REF_ATTRIBUTE = "data-dialogue-agent-ref"
DEFAULT_SNAPSHOT_ELEMENT_LIMIT = 40
DEFAULT_SNAPSHOT_TEXT_LIMIT = 2000
MAX_SNAPSHOT_ELEMENT_LIMIT = 120
MAX_SNAPSHOT_TEXT_LIMIT = 8000
CHROME_SCREENSHOT_DIR = os.path.join(DATA_DIR, "browser-screenshots")

KEYBOARD_MODIFIER_FLAGS = {
    "alt": 1,
    "ctrl": 2,
    "control": 2,
    "meta": 4,
    "cmd": 4,
    "command": 4,
    "shift": 8,
}
KEYBOARD_KEY_ALIASES = {
    "enter": ("Enter", "Enter"),
    "return": ("Enter", "Enter"),
    "escape": ("Escape", "Escape"),
    "esc": ("Escape", "Escape"),
    "tab": ("Tab", "Tab"),
    "space": (" ", "Space"),
    "spacebar": (" ", "Space"),
    "backspace": ("Backspace", "Backspace"),
    "delete": ("Delete", "Delete"),
    "arrowup": ("ArrowUp", "ArrowUp"),
    "up": ("ArrowUp", "ArrowUp"),
    "arrowdown": ("ArrowDown", "ArrowDown"),
    "down": ("ArrowDown", "ArrowDown"),
    "arrowleft": ("ArrowLeft", "ArrowLeft"),
    "left": ("ArrowLeft", "ArrowLeft"),
    "arrowright": ("ArrowRight", "ArrowRight"),
    "right": ("ArrowRight", "ArrowRight"),
    "home": ("Home", "Home"),
    "end": ("End", "End"),
    "pageup": ("PageUp", "PageUp"),
    "pagedown": ("PageDown", "PageDown"),
}
KEYBOARD_VIRTUAL_KEY_CODES = {
    "Enter": 13,
    "Escape": 27,
    "Tab": 9,
    " ": 32,
    "Backspace": 8,
    "Delete": 46,
    "ArrowUp": 38,
    "ArrowDown": 40,
    "ArrowLeft": 37,
    "ArrowRight": 39,
    "Home": 36,
    "End": 35,
    "PageUp": 33,
    "PageDown": 34,
}


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as temporary_socket:
        temporary_socket.bind(("127.0.0.1", 0))
        temporary_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(temporary_socket.getsockname()[1])


def _normalize_ref(ref: str) -> str:
    normalized_ref = str(ref or "").strip()
    if not normalized_ref:
        raise ChromeBrowserAutomationError("Ref is required.", code="invalid_ref")
    return normalized_ref


def _normalize_tab_id(tab_id: str) -> str:
    normalized_tab_id = str(tab_id or "").strip()
    if not normalized_tab_id:
        raise ChromeBrowserAutomationError("TabId is required.", code="invalid_tab_id")
    return normalized_tab_id


def _normalize_positive_int(value, *, default: int, minimum: int, maximum: int) -> int:
    if value in (None, ""):
        return default
    try:
        integer_value = int(value)
    except (TypeError, ValueError) as exc:
        raise ChromeBrowserAutomationError(
            f"Expected an integer value, got {value!r}.",
            code="invalid_integer",
        ) from exc
    return max(minimum, min(maximum, integer_value))


def _normalize_scroll_direction(direction: str) -> str:
    normalized_direction = str(direction or "").strip().lower()
    if normalized_direction not in {"up", "down"}:
        raise ChromeBrowserAutomationError(
            "Direction must be 'up' or 'down'.",
            code="invalid_direction",
        )
    return normalized_direction


def _sanitize_browser_filename(file_name: str, *, default_name: str = "") -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", str(file_name or "").strip()).strip("-")
    normalized = normalized or default_name or f"browser-{int(time.time() * 1000)}"
    if not normalized.lower().endswith(".png"):
        normalized = f"{normalized}.png"
    return normalized


def _build_keyboard_shortcut_payload(shortcut: str) -> dict:
    normalized_shortcut = str(shortcut or "").strip()
    if not normalized_shortcut:
        raise ChromeBrowserAutomationError("Key is required.", code="invalid_key")
    parts = [part.strip() for part in normalized_shortcut.split("+") if part.strip()]
    modifier_flags = 0
    while len(parts) > 1 and parts[0].strip().lower() in KEYBOARD_MODIFIER_FLAGS:
        modifier_flags |= KEYBOARD_MODIFIER_FLAGS[parts.pop(0).strip().lower()]
    key_token = str(parts[0] if parts else normalized_shortcut).strip()
    normalized_key_token = key_token.lower().replace(" ", "")
    key, code = KEYBOARD_KEY_ALIASES.get(normalized_key_token, (key_token, key_token))
    text = ""
    if len(key_token) == 1:
        key = key_token
        if key_token.isalpha():
            code = f"Key{key_token.upper()}"
        elif key_token.isdigit():
            code = f"Digit{key_token}"
        if not modifier_flags:
            text = key_token
    virtual_key_code = 0
    if len(key) == 1:
        virtual_key_code = ord(key.upper())
    else:
        virtual_key_code = int(KEYBOARD_VIRTUAL_KEY_CODES.get(key, 0) or 0)
    return {
        "shortcut": normalized_shortcut,
        "key": key,
        "code": code,
        "text": text,
        "modifiers": modifier_flags,
        "virtual_key_code": virtual_key_code,
    }


def _truncate_text(value: str, *, limit: int = 120) -> str:
    normalized_value = str(value or "").strip()
    if len(normalized_value) <= limit:
        return normalized_value
    return f"{normalized_value[: max(0, limit - 3)].rstrip()}..."


def _format_element_line(element_payload: dict) -> str:
    ref = str(element_payload.get("ref", "")).strip()
    role = str(element_payload.get("role", "")).strip() or str(element_payload.get("tag", "")).strip() or "element"
    label = _truncate_text(element_payload.get("label", ""), limit=120)
    extras = []

    input_type = _truncate_text(element_payload.get("type", ""), limit=30)
    if input_type:
        extras.append(f"type={input_type}")

    placeholder = _truncate_text(element_payload.get("placeholder", ""), limit=60)
    if placeholder:
        extras.append(f"placeholder={placeholder!r}")

    value = _truncate_text(element_payload.get("value", ""), limit=60)
    if value:
        extras.append(f"value={value!r}")

    href = str(element_payload.get("href", "")).strip()
    if href:
        parsed_href = urlparse(href)
        short_href = parsed_href.netloc + parsed_href.path if parsed_href.netloc else href
        extras.append(f"href={_truncate_text(short_href, limit=80)!r}")

    if element_payload.get("disabled"):
        extras.append("disabled=true")

    extra_text = f" ({', '.join(extras)})" if extras else ""
    return f"[{ref}] {role}: {label or '(no label)'}{extra_text}"


def _build_snapshot_text(snapshot_payload: dict) -> str:
    lines = [
        f"Title: {str(snapshot_payload.get('title', '')).strip()}",
        f"URL: {str(snapshot_payload.get('url', '')).strip()}",
    ]

    frame_count = snapshot_payload.get("frame_count")
    if isinstance(frame_count, int) and frame_count > 0:
        lines.append(f"Frames scanned: {frame_count}")

    page_text = str(snapshot_payload.get("page_text", "")).strip()
    if page_text:
        lines.append("Visible text:")
        lines.append(page_text)

    lines.append("Interactive elements:")
    elements = snapshot_payload.get("elements") or []
    if elements:
        lines.extend(_format_element_line(element_payload) for element_payload in elements)
    else:
        lines.append("(none)")

    if snapshot_payload.get("truncated"):
        lines.append("Note: element list was truncated. Increase MaxElements if needed.")

    return "\n".join(lines)


BROWSER_DOCUMENT_HELPERS_JS = r"""
  const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const safeGetDocumentUrl = (doc) => {
    try {
      return String((doc && doc.location && doc.location.href) || '');
    } catch (error) {
      return '';
    }
  };
  const getElementWindow = (element) => {
    try {
      return (element && element.ownerDocument && element.ownerDocument.defaultView) || window;
    } catch (error) {
      return window;
    }
  };
  const collectAccessibleDocuments = () => {
    const entries = [];
    const seenDocuments = new Set();
    const addDocument = (doc, framePath = []) => {
      if (!doc || seenDocuments.has(doc)) {
        return;
      }
      seenDocuments.add(doc);
      let docWindow = window;
      try {
        docWindow = doc.defaultView || window;
      } catch (error) {}
      entries.push({ doc, win: docWindow, framePath });
      let frameElements = [];
      try {
        frameElements = Array.from(doc.querySelectorAll('iframe, frame'));
      } catch (error) {}
      frameElements.forEach((frameElement, index) => {
        try {
          const childDocument = frameElement.contentDocument;
          if (childDocument) {
            addDocument(childDocument, framePath.concat(index));
          }
        } catch (error) {}
      });
    };
    addDocument(document, []);
    return entries;
  };
  const clearRefAttributes = (refAttr, documents) => {
    for (const entry of documents) {
      for (const node of entry.doc.querySelectorAll(`[${refAttr}]`)) {
        node.removeAttribute(refAttr);
      }
    }
  };
  const isVisible = (element) => {
    if (!element) return false;
    let currentElement = element;
    while (currentElement) {
      if (!currentElement.isConnected) return false;
      if (currentElement.closest('[hidden], [aria-hidden="true"]')) return false;
      const currentWindow = getElementWindow(currentElement);
      let style = null;
      try {
        style = currentWindow.getComputedStyle(currentElement);
      } catch (error) {
        return false;
      }
      if (!style) return false;
      if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') < 0.05) {
        return false;
      }
      const rect = currentElement.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) {
        return false;
      }
      const frameElement = currentWindow.frameElement;
      if (!frameElement) {
        return true;
      }
      currentElement = frameElement;
    }
    return true;
  };
  const absoluteRectFor = (element) => {
    const rect = element.getBoundingClientRect();
    let left = rect.left;
    let top = rect.top;
    let currentWindow = getElementWindow(element);
    while (currentWindow && currentWindow.frameElement) {
      const frameRect = currentWindow.frameElement.getBoundingClientRect();
      left += frameRect.left;
      top += frameRect.top;
      currentWindow = currentWindow.frameElement.ownerDocument.defaultView || window;
    }
    return {
      left,
      top,
      width: rect.width,
      height: rect.height,
    };
  };
  const framePathFor = (entry) => (entry.framePath.length ? entry.framePath.join('.') : 'root');
  const findElementByRef = (refAttr, ref, documents) => {
    for (const entry of documents) {
      const element = entry.doc.querySelector(`[${refAttr}="${ref}"]`);
      if (element) {
        return { element, entry };
      }
    }
    return null;
  };
  const scrollFrameChainIntoView = (element) => {
    const frameElements = [];
    let currentWindow = getElementWindow(element);
    while (currentWindow && currentWindow.frameElement) {
      frameElements.push(currentWindow.frameElement);
      currentWindow = currentWindow.frameElement.ownerDocument.defaultView || window;
    }
    frameElements.reverse();
    for (const frameElement of frameElements) {
      try {
        frameElement.scrollIntoView({ block: 'center', inline: 'center' });
      } catch (error) {}
    }
    try {
      element.scrollIntoView({ block: 'center', inline: 'center' });
    } catch (error) {}
  };
  const buildPageState = (documents) => {
    const entries = Array.isArray(documents) ? documents : collectAccessibleDocuments();
    const pageTextParts = [];
    const seenPageText = new Set();
    const documentStates = [];
    for (const entry of entries) {
      const bodyText = normalize((entry.doc.body && entry.doc.body.innerText) || '');
      if (bodyText && !seenPageText.has(bodyText)) {
        seenPageText.add(bodyText);
        pageTextParts.push(bodyText);
      }
      documentStates.push({
        framePath: framePathFor(entry),
        url: safeGetDocumentUrl(entry.doc),
        title: entry.doc.title || '',
        readyState: entry.doc.readyState || '',
        bodyTextLength: bodyText.length,
      });
    }
    return {
      url: location.href,
      title: document.title || '',
      readyState: document.readyState,
      bodyTextLength: normalize(pageTextParts.join('\n')).length,
      frameCount: Math.max(0, entries.length - 1),
      documents: documentStates,
    };
  };
""".strip()


def _build_snapshot_script(max_elements: int, max_text_length: int) -> str:
    return f"""
(() => {{
  const REF_ATTR = {json.dumps(BROWSER_REF_ATTRIBUTE)};
  const MAX_ELEMENTS = {int(max_elements)};
  const MAX_TEXT_LENGTH = {int(max_text_length)};
  const ROLE_NAMES = new Set([
    'button', 'link', 'textbox', 'searchbox', 'combobox', 'menuitem',
    'option', 'tab', 'checkbox', 'radio', 'switch'
  ]);
  const ACTION_KEYWORDS = [
    'play', 'pause', 'resume', 'start', 'login', 'sign in', 'register',
    'search', '搜索', 'submit', '提交', 'confirm', '确认', 'open', '打开',
    'continue', '继续', 'next', 'previous', 'save', '保存', 'apply',
    'download', 'upload', 'checkout', 'pay', 'close'
  ];
  const CANDIDATE_SELECTOR = [
    'a[href]',
    'button',
    'input',
    'select',
    'textarea',
    'summary',
    '[contenteditable="true"]',
    '[role]',
    '[tabindex]',
    '[onclick]'
  ].join(',');

  {BROWSER_DOCUMENT_HELPERS_JS}
  const semanticRole = (element) => {{
    const tag = (element.tagName || '').toLowerCase();
    const inputType = normalize(element.getAttribute('type')).toLowerCase();
    const explicitRole = normalize(element.getAttribute('role')).toLowerCase();
    if (explicitRole) return explicitRole;
    if (tag === 'a') return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'select') return 'combobox';
    if (tag === 'summary') return 'button';
    if (tag === 'input') {{
      if (['checkbox'].includes(inputType)) return 'checkbox';
      if (['radio'].includes(inputType)) return 'radio';
      return 'textbox';
    }}
    return tag || 'element';
  }};
  const isInteractive = (element) => {{
    const tag = (element.tagName || '').toLowerCase();
    const explicitRole = normalize(element.getAttribute('role')).toLowerCase();
    if (['a', 'button', 'input', 'select', 'textarea', 'summary'].includes(tag)) return true;
    if (explicitRole && ROLE_NAMES.has(explicitRole)) return true;
    if (element.isContentEditable) return true;
    if (typeof element.onclick === 'function') return true;
    return Number.isFinite(element.tabIndex) && element.tabIndex >= 0;
  }};
  const labelFor = (element) => {{
    const labelTexts = [];
    const pushValue = (value) => {{
      const normalized = normalize(value);
      if (normalized && !labelTexts.includes(normalized)) {{
        labelTexts.push(normalized);
      }}
    }};

    pushValue(element.getAttribute('aria-label'));
    pushValue(element.getAttribute('title'));
    pushValue(element.getAttribute('placeholder'));
    pushValue(element.getAttribute('alt'));

    if (element.labels) {{
      for (const label of element.labels) {{
        pushValue(label.innerText || label.textContent || '');
      }}
    }}

    pushValue(element.innerText || element.textContent || '');

    if ('value' in element && typeof element.value === 'string') {{
      pushValue(element.value);
    }}

    pushValue(element.getAttribute('name'));
    return labelTexts[0] || '';
  }};
  const describe = (element, entry) => {{
    const rect = absoluteRectFor(element);
    const tag = (element.tagName || '').toLowerCase();
    const inputType = normalize(element.getAttribute('type')).toLowerCase();
    const role = semanticRole(element);
    const href = tag === 'a' ? normalize(element.href || element.getAttribute('href')) : '';
    const label = labelFor(element);
    const placeholder = normalize(element.getAttribute('placeholder'));
    const value =
      tag === 'input' || tag === 'textarea' || element.isContentEditable
        ? normalize(element.value || element.innerText || element.textContent || '')
        : '';
    return {{
      ref: '',
      tag,
      role,
      type: inputType,
      label,
      text: normalize(element.innerText || element.textContent || ''),
      placeholder,
      value,
      href,
      disabled: Boolean(element.disabled || element.getAttribute('aria-disabled') === 'true'),
      x: Math.round(rect.left),
      y: Math.round(rect.top),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
      framePath: framePathFor(entry),
      frameUrl: safeGetDocumentUrl(entry.doc)
    }};
  }};
  const computePriority = (description) => {{
    const role = normalize(description.role).toLowerCase();
    const tag = normalize(description.tag).toLowerCase();
    const labelText = normalize([
      description.label,
      description.text,
      description.placeholder,
      description.value,
      description.href
    ].filter(Boolean).join(' ')).toLowerCase();
    let score = 0;

    if (description.disabled) {{
      return -1000;
    }}

    if (role === 'button') score += 140;
    else if (role === 'link') score += 90;
    else if (role === 'textbox' || role === 'searchbox' || role === 'combobox') score += 80;
    else if (role === 'tab' || role === 'menuitem') score += 50;
    else score += 20;

    if (tag === 'button') score += 20;
    if (description.label) score += 25;
    if (description.text) score += 10;
    if (description.href) score += 8;
    if (description.framePath === 'root') score += 6;

    for (const keyword of ACTION_KEYWORDS) {{
      const normalizedKeyword = normalize(keyword).toLowerCase();
      if (!normalizedKeyword) continue;
      if (labelText.includes(normalizedKeyword)) {{
        score += normalizedKeyword.length <= 2 ? 35 : 75;
      }}
    }}

    if (labelText.includes('play') || labelText.includes('播放')) score += 90;
    if (labelText.includes('login') || labelText.includes('登录') || labelText.includes('sign in')) score += 120;
    if (labelText.includes('submit') || labelText.includes('提交') || labelText.includes('confirm') || labelText.includes('确认')) score += 110;
    if (labelText.includes('search') || labelText.includes('搜索')) score += 100;

    const y = Number(description.y || 0);
    const x = Number(description.x || 0);
    const width = Number(description.width || 0);
    const height = Number(description.height || 0);
    const viewportWidth = Math.max(window.innerWidth || 0, 1);
    const viewportHeight = Math.max(window.innerHeight || 0, 1);
    const centerX = x + (width / 2);
    const centerY = y + (height / 2);
    const withinViewport =
      centerX >= 0 &&
      centerX <= viewportWidth &&
      centerY >= 0 &&
      centerY <= viewportHeight;
    if (withinViewport) {{
      score += 90;
    }} else if (centerY >= -200 && centerY <= viewportHeight + 300) {{
      score += 35;
    }}

    if (y >= 0) {{
      score += Math.max(0, 70 - Math.min(y, 1400) / 20);
    }} else {{
      score += Math.max(0, 40 + y / 20);
    }}

    const area = width * height;
    score += Math.min(area, 24000) / 2400;
    return score;
  }};

  const documents = collectAccessibleDocuments();
  clearRefAttributes(REF_ATTR, documents);

  const candidateEntries = [];
  let discoveryIndex = 0;
  for (const entry of documents) {{
    for (const element of entry.doc.querySelectorAll(CANDIDATE_SELECTOR)) {{
      if (!isInteractive(element) || !isVisible(element)) continue;
      const parentCandidate = element.parentElement ? element.parentElement.closest(CANDIDATE_SELECTOR) : null;
      if (parentCandidate && parentCandidate !== element && isInteractive(parentCandidate) && isVisible(parentCandidate)) {{
        continue;
      }}
      const description = describe(element, entry);
      description.priority = computePriority(description);
      description.discoveryIndex = discoveryIndex++;
      candidateEntries.push({{ element, description }});
    }}
  }}

  candidateEntries.sort((left, right) => {{
    const scoreDelta = (right.description.priority || 0) - (left.description.priority || 0);
    if (scoreDelta !== 0) return scoreDelta;
    const yDelta = (left.description.y || 0) - (right.description.y || 0);
    if (yDelta !== 0) return yDelta;
    const xDelta = (left.description.x || 0) - (right.description.x || 0);
    if (xDelta !== 0) return xDelta;
    return (left.description.discoveryIndex || 0) - (right.description.discoveryIndex || 0);
  }});

  const elements = [];
  for (const [index, candidate] of candidateEntries.slice(0, MAX_ELEMENTS).entries()) {{
    const description = candidate.description;
    description.ref = `e${{index + 1}}`;
    candidate.element.setAttribute(REF_ATTR, description.ref);
    delete description.priority;
    delete description.discoveryIndex;
    elements.push(description);
  }}

  const pageTextParts = [];
  const seenPageText = new Set();
  for (const entry of documents) {{
    const bodyText = normalize((entry.doc.body && entry.doc.body.innerText) || '');
    if (bodyText && !seenPageText.has(bodyText)) {{
      seenPageText.add(bodyText);
      pageTextParts.push(bodyText);
    }}
  }}
  const pageText = normalize(pageTextParts.join('\\n')).slice(0, MAX_TEXT_LENGTH);
  const pageState = buildPageState(documents);
  return {{
    ok: true,
    title: pageState.title,
    url: pageState.url,
    pageText,
    elements,
    truncated: candidateEntries.length > MAX_ELEMENTS,
    frameCount: pageState.frameCount,
    documents: pageState.documents
  }};
}})()
""".strip()


def _build_dom_click_script(ref: str) -> str:
    return f"""
(() => (async () => {{
  const REF_ATTR = {json.dumps(BROWSER_REF_ATTRIBUTE)};
  const REF = {json.dumps(ref)};
  {BROWSER_DOCUMENT_HELPERS_JS}
  const semanticRole = (element) => {{
    const tag = (element.tagName || '').toLowerCase();
    const explicitRole = normalize(element.getAttribute('role')).toLowerCase();
    if (explicitRole) return explicitRole;
    if (tag === 'a') return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'input') return 'textbox';
    return tag || 'element';
  }};

  const documents = collectAccessibleDocuments();
  const resolved = findElementByRef(REF_ATTR, REF, documents);
  if (!resolved) {{
    return {{ ok: false, error: `Element ref ${{REF}} was not found on the page.`, errorCode: 'ref_not_found', ref: REF }};
  }}
  const element = resolved.element;
  if (!isVisible(element)) {{
    return {{ ok: false, error: `Element ref ${{REF}} is no longer visible.`, errorCode: 'ref_not_visible', ref: REF }};
  }}

  const role = semanticRole(element);
  const label = normalize(element.innerText || element.textContent || element.getAttribute('aria-label') || '');
  const targetWindow = getElementWindow(element);

  scrollFrameChainIntoView(element);
  await new Promise((resolve) => setTimeout(resolve, 120));
  try {{ element.focus({{ preventScroll: true }}); }} catch (error) {{}}

  const tag = (element.tagName || '').toLowerCase();
  if (tag === 'option' && element.parentElement) {{
    element.selected = true;
    element.parentElement.dispatchEvent(new targetWindow.Event('change', {{ bubbles: true }}));
  }} else if (typeof element.click === 'function') {{
    element.click();
  }} else {{
    element.dispatchEvent(new targetWindow.MouseEvent('click', {{ bubbles: true, cancelable: true, view: targetWindow }}));
  }}

  await new Promise((resolve) => setTimeout(resolve, 800));
  const pageState = buildPageState();
  return {{
    ok: true,
    ref: REF,
    role,
    label,
    title: pageState.title,
    url: pageState.url,
    framePath: framePathFor(resolved.entry),
    frameUrl: safeGetDocumentUrl(resolved.entry.doc)
  }};
}})())()
""".strip()


def _build_click_target_script(ref: str) -> str:
    return f"""
(() => (async () => {{
  const REF_ATTR = {json.dumps(BROWSER_REF_ATTRIBUTE)};
  const REF = {json.dumps(ref)};
  {BROWSER_DOCUMENT_HELPERS_JS}
  const semanticRole = (element) => {{
    const tag = (element.tagName || '').toLowerCase();
    const explicitRole = normalize(element.getAttribute('role')).toLowerCase();
    if (explicitRole) return explicitRole;
    if (tag === 'a') return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'input') return 'textbox';
    return tag || 'element';
  }};

  const documents = collectAccessibleDocuments();
  const resolved = findElementByRef(REF_ATTR, REF, documents);
  if (!resolved) {{
    return {{ ok: false, error: `Element ref ${{REF}} was not found on the page.`, errorCode: 'ref_not_found', ref: REF }};
  }}

  const element = resolved.element;
  if (!isVisible(element)) {{
    return {{ ok: false, error: `Element ref ${{REF}} is no longer visible.`, errorCode: 'ref_not_visible', ref: REF }};
  }}

  scrollFrameChainIntoView(element);
  await new Promise((resolve) => setTimeout(resolve, 120));
  try {{ element.focus({{ preventScroll: true }}); }} catch (error) {{}}

  const rect = absoluteRectFor(element);
  const tag = (element.tagName || '').toLowerCase();
  return {{
    ok: true,
    ref: REF,
    tag,
    role: semanticRole(element),
    label: normalize(element.innerText || element.textContent || element.getAttribute('aria-label') || ''),
    x: Math.round(rect.left + (rect.width / 2)),
    y: Math.round(rect.top + (rect.height / 2)),
    width: Math.round(rect.width),
    height: Math.round(rect.height),
    clickMethod: tag === 'option' ? 'dom' : 'mouse',
    framePath: framePathFor(resolved.entry),
    frameUrl: safeGetDocumentUrl(resolved.entry.doc)
  }};
}})())()
""".strip()


def _build_type_script(ref: str, text: str, submit: bool) -> str:
    return f"""
(() => (async () => {{
  const REF_ATTR = {json.dumps(BROWSER_REF_ATTRIBUTE)};
  const REF = {json.dumps(ref)};
  const INPUT_TEXT = {json.dumps(text)};
  const SHOULD_SUBMIT = {json.dumps(bool(submit))};
  {BROWSER_DOCUMENT_HELPERS_JS}
  const TYPEABLE_INPUT_TYPES = new Set(['', 'email', 'number', 'password', 'search', 'tel', 'text', 'url']);
  const documents = collectAccessibleDocuments();
  const resolved = findElementByRef(REF_ATTR, REF, documents);

  if (!resolved) {{
    return {{ ok: false, error: `Element ref ${{REF}} was not found on the page.`, errorCode: 'ref_not_found', ref: REF }};
  }}
  const element = resolved.element;

  const tag = (element.tagName || '').toLowerCase();
  const inputType = normalize(element.getAttribute('type')).toLowerCase();
  const isTypeable =
    element.isContentEditable ||
    tag === 'textarea' ||
    (tag === 'input' && TYPEABLE_INPUT_TYPES.has(inputType));

  if (!isTypeable) {{
    return {{ ok: false, error: `Element ref ${{REF}} is not text-editable.`, errorCode: 'ref_not_typeable', ref: REF }};
  }}

  const setElementValue = (target, value) => {{
    if (target.isContentEditable) {{
      target.textContent = value;
      return;
    }}
    const prototypeChain = [
      target,
      Object.getPrototypeOf(target),
      target.constructor && target.constructor.prototype
    ].filter(Boolean);
    for (const candidate of prototypeChain) {{
      const descriptor = Object.getOwnPropertyDescriptor(candidate, 'value');
      if (descriptor && typeof descriptor.set === 'function') {{
        descriptor.set.call(target, value);
        return;
      }}
    }}
    target.value = value;
  }};

  const targetWindow = getElementWindow(element);
  scrollFrameChainIntoView(element);
  await new Promise((resolve) => setTimeout(resolve, 120));
  try {{ element.focus({{ preventScroll: true }}); }} catch (error) {{}}
  if (typeof element.select === 'function') {{
    try {{ element.select(); }} catch (error) {{}}
  }}

  setElementValue(element, '');
  element.dispatchEvent(new targetWindow.Event('input', {{ bubbles: true }}));
  element.dispatchEvent(new targetWindow.Event('change', {{ bubbles: true }}));
  setElementValue(element, INPUT_TEXT);
  element.dispatchEvent(new targetWindow.Event('input', {{ bubbles: true }}));
  element.dispatchEvent(new targetWindow.Event('change', {{ bubbles: true }}));

  if (SHOULD_SUBMIT) {{
    if (element.form && typeof element.form.requestSubmit === 'function') {{
      element.form.requestSubmit();
    }} else {{
      element.dispatchEvent(new targetWindow.KeyboardEvent('keydown', {{ key: 'Enter', code: 'Enter', bubbles: true }}));
      element.dispatchEvent(new targetWindow.KeyboardEvent('keyup', {{ key: 'Enter', code: 'Enter', bubbles: true }}));
    }}
  }}

  await new Promise((resolve) => setTimeout(resolve, 800));
  const pageState = buildPageState();
  return {{
    ok: true,
    ref: REF,
    submitted: SHOULD_SUBMIT,
    value: element.isContentEditable
      ? normalize(element.innerText || element.textContent || '')
      : normalize(element.value || ''),
    title: pageState.title,
    url: pageState.url,
    framePath: framePathFor(resolved.entry),
    frameUrl: safeGetDocumentUrl(resolved.entry.doc)
  }};
}})())()
""".strip()


def _build_scroll_script(direction: str, amount: int) -> str:
    delta = amount if direction == "down" else -amount
    return f"""
(() => (async () => {{
  const DELTA = {int(delta)};
  {BROWSER_DOCUMENT_HELPERS_JS}
  const documents = collectAccessibleDocuments();
  const scrollCandidates = documents
    .map((entry) => {{
      const scrollingElement = entry.doc.scrollingElement || entry.doc.documentElement || entry.doc.body;
      if (!scrollingElement) {{
        return null;
      }}
      const currentScrollTop = Number(scrollingElement.scrollTop || 0);
      const maxScrollTop = Math.max(
        0,
        Number((scrollingElement.scrollHeight || 0) - (scrollingElement.clientHeight || 0))
      );
      const remaining =
        DELTA >= 0
          ? Math.max(0, maxScrollTop - currentScrollTop)
          : Math.max(0, currentScrollTop);
      return {{
        entry,
        scrollingElement,
        currentScrollTop,
        maxScrollTop,
        remaining,
      }};
    }})
    .filter(Boolean)
    .sort((left, right) => {{
      const leftScore = (left.remaining > 0 ? 1_000_000 : 0) + left.maxScrollTop + left.entry.framePath.length;
      const rightScore = (right.remaining > 0 ? 1_000_000 : 0) + right.maxScrollTop + right.entry.framePath.length;
      return rightScore - leftScore;
    }});

  const target = scrollCandidates[0];
  if (target) {{
    const targetFrameElement = target.entry.win && target.entry.win.frameElement;
    if (targetFrameElement) {{
      try {{
        targetFrameElement.scrollIntoView({{ block: 'center', inline: 'center' }});
      }} catch (error) {{}}
    }}
    if (typeof target.entry.win.scrollBy === 'function') {{
      target.entry.win.scrollBy({{ top: DELTA, left: 0, behavior: 'auto' }});
    }} else {{
      target.scrollingElement.scrollTop = target.currentScrollTop + DELTA;
    }}
  }}
  await new Promise((resolve) => setTimeout(resolve, 200));
  const pageState = buildPageState();
  return {{
    ok: true,
    direction: {json.dumps(direction)},
    amount: {int(amount)},
    scrollX: Math.round((target && target.entry.win && target.entry.win.scrollX) || window.scrollX || 0),
    scrollY: Math.round((target && target.entry.win && target.entry.win.scrollY) || window.scrollY || 0),
    title: pageState.title,
    url: pageState.url,
    framePath: target ? framePathFor(target.entry) : 'root',
    frameUrl: target ? safeGetDocumentUrl(target.entry.doc) : safeGetDocumentUrl(document)
  }};
}})())()
""".strip()


def _build_page_state_script() -> str:
    return f"""
(() => {{
  {BROWSER_DOCUMENT_HELPERS_JS}
  return buildPageState();
}})()
""".strip()


def _build_page_preview_script(max_text_length: int) -> str:
    return f"""
(() => {{
  const MAX_TEXT_LENGTH = {int(max_text_length)};
  {BROWSER_DOCUMENT_HELPERS_JS}
  const documents = collectAccessibleDocuments();
  const pageTextParts = [];
  const seenPageText = new Set();
  for (const entry of documents) {{
    const bodyText = normalize((entry.doc.body && entry.doc.body.innerText) || '');
    if (bodyText && !seenPageText.has(bodyText)) {{
      seenPageText.add(bodyText);
      pageTextParts.push(bodyText);
    }}
  }}
  const pageState = buildPageState(documents);
  return {{
    ok: true,
    title: pageState.title,
    url: pageState.url,
    pageText: normalize(pageTextParts.join('\\n')).slice(0, MAX_TEXT_LENGTH),
    frameCount: pageState.frameCount
  }};
}})()
""".strip()


def _build_wait_condition_script(*, ref: str = "", text_contains: str = "", url_contains: str = "", title_contains: str = "") -> str:
    return f"""
(() => {{
  const REF_ATTR = {json.dumps(BROWSER_REF_ATTRIBUTE)};
  const TARGET_REF = {json.dumps(str(ref or "").strip())};
  const TEXT_CONTAINS = {json.dumps(str(text_contains or "").strip().lower())};
  const URL_CONTAINS = {json.dumps(str(url_contains or "").strip().lower())};
  const TITLE_CONTAINS = {json.dumps(str(title_contains or "").strip().lower())};
  {BROWSER_DOCUMENT_HELPERS_JS}

  const documents = collectAccessibleDocuments();
  const matched = [];
  if (TARGET_REF) {{
    const resolved = findElementByRef(REF_ATTR, TARGET_REF, documents);
    if (resolved && isVisible(resolved.element)) {{
      matched.push(`ref:${{TARGET_REF}}`);
    }}
  }}
  if (TEXT_CONTAINS) {{
    const combinedText = normalize(
      documents.map((entry) => (entry.doc.body && entry.doc.body.innerText) || '').join('\\n')
    ).toLowerCase();
    if (combinedText.includes(TEXT_CONTAINS)) {{
      matched.push(`text:${{TEXT_CONTAINS}}`);
    }}
  }}
  if (URL_CONTAINS && String(location.href || '').toLowerCase().includes(URL_CONTAINS)) {{
    matched.push(`url:${{URL_CONTAINS}}`);
  }}
  if (TITLE_CONTAINS && String(document.title || '').toLowerCase().includes(TITLE_CONTAINS)) {{
    matched.push(`title:${{TITLE_CONTAINS}}`);
  }}
  if (!matched.length) {{
    return null;
  }}
  const pageState = buildPageState(documents);
  return {{
    ok: true,
    matched,
    title: pageState.title,
    url: pageState.url,
    frameCount: pageState.frameCount
  }};
}})()
""".strip()


GO_BACK_SCRIPT = """
(() => (async () => {
  const previousUrl = location.href;
  if (window.history.length > 1) {
    window.history.back();
  }
  await new Promise((resolve) => setTimeout(resolve, 900));
  return {
    ok: true,
    previousUrl,
    title: document.title || '',
    url: location.href
  };
})())()
""".strip()


def _extract_remote_value(response_payload: dict):
    remote_result = ((response_payload.get("result") or {}).get("result") or {})
    if "value" in remote_result:
        return remote_result["value"]
    if remote_result.get("subtype") == "null":
        return None
    if "description" in remote_result:
        return remote_result["description"]
    return remote_result


class _ChromeDevToolsWebSocket:
    def __init__(self, websocket_url: str, *, timeout_seconds: float = 30.0):
        parsed_url = urlparse(websocket_url)
        self._socket = socket.create_connection(
            (parsed_url.hostname, parsed_url.port),
            timeout=timeout_seconds,
        )
        self._socket.settimeout(timeout_seconds)
        self._next_message_id = 1
        self._handshake(parsed_url)

    def _handshake(self, parsed_url):
        websocket_key = base64.b64encode(os.urandom(16)).decode("ascii")
        request_path = parsed_url.path or "/"
        if parsed_url.query:
            request_path = f"{request_path}?{parsed_url.query}"
        request_text = (
            f"GET {request_path} HTTP/1.1\r\n"
            f"Host: {parsed_url.hostname}:{parsed_url.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {websocket_key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._socket.sendall(request_text.encode("utf-8"))
        response_buffer = b""
        while b"\r\n\r\n" not in response_buffer:
            response_buffer += self._socket.recv(4096)
        status_line = response_buffer.split(b"\r\n", 1)[0]
        if b"101" not in status_line:
            raise ChromeBrowserAutomationError(
                f"WebSocket handshake failed: {status_line.decode(errors='replace')}",
                code="websocket_handshake_failed",
            )

    def _read_exact(self, size: int) -> bytes:
        chunks = []
        remaining = size
        while remaining > 0:
            try:
                chunk = self._socket.recv(remaining)
            except socket.timeout as error:
                raise ChromeBrowserAutomationError(
                    "Timed out while waiting for a Chrome DevTools response.",
                    code="websocket_timeout",
                ) from error
            if not chunk:
                raise ChromeBrowserAutomationError(
                    "WebSocket closed unexpectedly.",
                    code="websocket_closed",
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _send_text(self, text: str):
        payload = text.encode("utf-8")
        header = bytearray([0x81])
        payload_length = len(payload)
        if payload_length < 126:
            header.append(0x80 | payload_length)
        elif payload_length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", payload_length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", payload_length))

        mask = os.urandom(4)
        masked_payload = bytes(
            byte ^ mask[index % 4]
            for index, byte in enumerate(payload)
        )
        self._socket.sendall(bytes(header) + mask + masked_payload)

    def _receive_message(self):
        while True:
            first_byte, second_byte = self._read_exact(2)
            opcode = first_byte & 0x0F
            masked = bool(second_byte & 0x80)
            payload_length = second_byte & 0x7F

            if payload_length == 126:
                payload_length = struct.unpack("!H", self._read_exact(2))[0]
            elif payload_length == 127:
                payload_length = struct.unpack("!Q", self._read_exact(8))[0]

            masking_key = self._read_exact(4) if masked else None
            payload = self._read_exact(payload_length) if payload_length else b""
            if masked and masking_key:
                payload = bytes(
                    byte ^ masking_key[index % 4]
                    for index, byte in enumerate(payload)
                )

            if opcode == 0x9:
                self._socket.sendall(b"\x8A\x00")
                continue
            if opcode == 0x8:
                raise ChromeBrowserAutomationError(
                    "Chrome DevTools target closed the WebSocket connection.",
                    code="websocket_closed",
                )
            if opcode == 0x1:
                return json.loads(payload.decode("utf-8"))

    def call(self, method: str, params: dict = None):
        message_id = self._next_message_id
        self._next_message_id += 1
        self._send_text(
            json.dumps(
                {
                    "id": message_id,
                    "method": method,
                    "params": params or {},
                },
                ensure_ascii=False,
            )
        )
        while True:
            message = self._receive_message()
            if message.get("id") != message_id:
                continue
            if "error" in message:
                error_payload = message.get("error") or {}
                raise ChromeBrowserAutomationError(
                    f"DevTools call failed for {method}: {error_payload}",
                    code="devtools_call_failed",
                )
            return message

    def evaluate(self, expression: str, *, await_promise: bool = True):
        response_payload = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
                "userGesture": True,
            },
        )
        return _extract_remote_value(response_payload)

    def close(self):
        try:
            self._socket.close()
        except OSError:
            pass


class _ChromeDevToolsPage:
    def __init__(self, websocket_url: str):
        self._websocket = _ChromeDevToolsWebSocket(websocket_url)

    def call(self, method: str, params: dict = None):
        return self._websocket.call(method, params)

    def prepare(self, *, bring_to_front: bool = True):
        self.call("Page.enable")
        self.call("Runtime.enable")
        self.call("Network.enable")
        if bring_to_front:
            self.call("Page.bringToFront")

    def navigate(self, url: str):
        self.call("Page.navigate", {"url": url})

    def bring_to_front(self):
        self.call("Page.bringToFront")

    def click_at(self, x: float, y: float):
        self.call(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseMoved",
                "x": float(x),
                "y": float(y),
                "button": "none",
                "buttons": 0,
                "pointerType": "mouse",
            },
        )
        self.call(
            "Input.dispatchMouseEvent",
            {
                "type": "mousePressed",
                "x": float(x),
                "y": float(y),
                "button": "left",
                "buttons": 1,
                "clickCount": 1,
                "pointerType": "mouse",
            },
        )
        time.sleep(0.05)
        self.call(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseReleased",
                "x": float(x),
                "y": float(y),
                "button": "left",
                "buttons": 0,
                "clickCount": 1,
                "pointerType": "mouse",
            },
        )

    def press_key(self, shortcut: str):
        key_payload = _build_keyboard_shortcut_payload(shortcut)
        base_payload = {
            "key": key_payload["key"],
            "code": key_payload["code"],
            "modifiers": int(key_payload["modifiers"]),
            "windowsVirtualKeyCode": int(key_payload["virtual_key_code"]),
            "nativeVirtualKeyCode": int(key_payload["virtual_key_code"]),
        }
        key_down_payload = dict(base_payload)
        key_down_payload["type"] = "keyDown" if key_payload["text"] else "rawKeyDown"
        if key_payload["text"]:
            key_down_payload["text"] = key_payload["text"]
            key_down_payload["unmodifiedText"] = key_payload["text"]
        self.call("Input.dispatchKeyEvent", key_down_payload)
        self.call(
            "Input.dispatchKeyEvent",
            {
                **base_payload,
                "type": "keyUp",
            },
        )

    def capture_screenshot(self, *, full_page: bool = False) -> str:
        response_payload = self.call(
            "Page.captureScreenshot",
            {
                "format": "png",
                "captureBeyondViewport": bool(full_page),
                "fromSurface": True,
            },
        )
        encoded = str((response_payload.get("result") or {}).get("data") or "").strip()
        if not encoded:
            raise ChromeBrowserAutomationError(
                "Chrome did not return screenshot data.",
                code="screenshot_missing",
            )
        return encoded

    def evaluate(self, expression: str, *, await_promise: bool = True):
        return self._websocket.evaluate(expression, await_promise=await_promise)

    def wait_for_value(
        self,
        expression: str,
        *,
        timeout_seconds: float = CHROME_PAGE_TIMEOUT_SECONDS,
        interval_seconds: float = 0.5,
        description: str = "value",
        validator=None,
    ):
        validator = validator or (lambda candidate: candidate not in (None, "", False))
        deadline = time.time() + timeout_seconds
        last_value = None
        while time.time() < deadline:
            last_value = self.evaluate(expression)
            if validator(last_value):
                return last_value
            time.sleep(interval_seconds)
        raise ChromeBrowserAutomationError(
            f"Timed out waiting for {description}. Last value: {last_value!r}",
            code="wait_timeout",
        )

    def wait_for_document_complete(self, *, timeout_seconds: float = CHROME_PAGE_TIMEOUT_SECONDS):
        self.wait_for_value(
            "document.readyState",
            timeout_seconds=timeout_seconds,
            interval_seconds=0.5,
            description="document readyState complete",
            validator=lambda value: value == "complete",
        )

    def wait_for_navigation_settle(
        self,
        *,
        timeout_seconds: float = CHROME_PAGE_TIMEOUT_SECONDS,
        settle_seconds: float = CHROME_NAVIGATION_SETTLE_SECONDS,
    ):
        self.wait_for_document_complete(timeout_seconds=timeout_seconds)
        page_state_script = _build_page_state_script()
        deadline = time.time() + max(1.0, float(timeout_seconds or 0))
        required_stable_seconds = max(0.75, min(float(settle_seconds or 0), timeout_seconds))
        stable_since = None
        last_state = None
        while time.time() < deadline:
            current_state = self.evaluate(page_state_script)
            current_signature = json.dumps(current_state, ensure_ascii=False, sort_keys=True)
            if current_signature == last_state:
                if stable_since is None:
                    stable_since = time.time()
                if time.time() - stable_since >= required_stable_seconds:
                    return current_state
            else:
                last_state = current_signature
                stable_since = time.time()
            time.sleep(0.25)
        return self.evaluate(page_state_script)

    def close(self):
        self._websocket.close()


class ChromeBrowserController:
    def __init__(
        self,
        *,
        profile_dir: str = CHROME_PROFILE_DIR,
        ready_timeout_seconds: float = CHROME_READY_TIMEOUT_SECONDS,
        debug_port: int = CHROME_DEBUG_PORT,
    ):
        self._profile_dir = profile_dir
        self._ready_timeout_seconds = ready_timeout_seconds
        self._chrome_path = None
        self._debug_port = int(debug_port)
        self._chrome_process = None
        self._page_target_id = None
        self._logger = logging.getLogger(__name__)

    def _find_chrome_executable(self) -> str:
        if self._chrome_path:
            return self._chrome_path
        for candidate_path in CHROME_CANDIDATE_PATHS:
            if os.path.exists(candidate_path):
                self._chrome_path = candidate_path
                return candidate_path
        raise ChromeBrowserAutomationError(
            "Google Chrome was not found on this machine.",
            code="chrome_not_found",
        )

    def _devtools_endpoint(self) -> str:
        return f"http://127.0.0.1:{self._debug_port}"

    def _uses_default_user_data_dir(self) -> bool:
        normalized_profile_dir = os.path.normcase(os.path.normpath(str(self._profile_dir or "")))
        default_profile_dir = os.path.normcase(CHROME_DEFAULT_USER_DATA_DIR)
        return normalized_profile_dir == default_profile_dir

    def _is_debug_endpoint_ready(self) -> bool:
        try:
            response = requests.get(
                f"{self._devtools_endpoint()}/json/version",
                timeout=1.5,
            )
            response.raise_for_status()
            browser_name = str((response.json() or {}).get("Browser", "")).lower()
            return "chrome" in browser_name or "headlesschrome" in browser_name
        except requests.RequestException:
            return False

    def _cleanup_failed_launch(self):
        failed_process = self._chrome_process
        self._chrome_process = None
        self._page_target_id = None
        if failed_process is None:
            return
        try:
            if failed_process.poll() is None:
                failed_process.terminate()
                failed_process.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            try:
                if failed_process.poll() is None:
                    failed_process.kill()
                    failed_process.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                self._logger.warning(
                    "Chrome launch cleanup failed | pid=%s | port=%s",
                    getattr(failed_process, "pid", None),
                    self._debug_port,
                )

    def _launch_chrome_with_port(self, debug_port: int):
        self._debug_port = int(debug_port)
        chrome_path = self._find_chrome_executable()
        chrome_arguments = [
            chrome_path,
            f"--remote-debugging-port={self._debug_port}",
            f"--user-data-dir={self._profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            "about:blank",
        ]
        creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        self._chrome_process = subprocess.Popen(
            chrome_arguments,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creation_flags,
        )

        deadline = time.time() + self._ready_timeout_seconds
        while time.time() < deadline:
            if self._is_debug_endpoint_ready():
                self._logger.info(
                    "Chrome browser session started | pid=%s | port=%s | profile=%s",
                    self._chrome_process.pid,
                    self._debug_port,
                    self._profile_dir,
                )
                return
            time.sleep(0.5)

        raise ChromeBrowserAutomationError(
            "Chrome launched but the DevTools endpoint did not become ready in time.",
            code="chrome_debug_timeout",
        )

    def _launch_chrome(self):
        if self._uses_default_user_data_dir():
            raise ChromeBrowserAutomationError(
                "Chrome no longer enables remote debugging against the real default user data directory. "
                "Use a non-standard Chrome profile directory for browser automation.",
                code="chrome_default_profile_not_debuggable",
            )
        os.makedirs(self._profile_dir, exist_ok=True)
        initial_port = int(self._debug_port)
        fallback_port = _pick_free_port()
        candidate_ports = [initial_port]
        if fallback_port not in candidate_ports:
            candidate_ports.append(fallback_port)

        last_error = None
        for attempt_index, candidate_port in enumerate(candidate_ports, start=1):
            if attempt_index > 1:
                self._logger.warning(
                    "Retrying Chrome launch with a fallback DevTools port | previous_port=%s | new_port=%s | profile=%s",
                    initial_port,
                    candidate_port,
                    self._profile_dir,
                )
            try:
                self._launch_chrome_with_port(candidate_port)
                return
            except ChromeBrowserAutomationError as error:
                last_error = error
                self._cleanup_failed_launch()
                if attempt_index >= len(candidate_ports):
                    raise

        if last_error is not None:
            raise last_error

    def _ensure_chrome_ready(self):
        if self._is_debug_endpoint_ready():
            if self._chrome_process is not None and self._chrome_process.poll() is not None:
                self._chrome_process = None
            return
        if self._chrome_process is not None and self._chrome_process.poll() is not None:
            self._chrome_process = None
        self._launch_chrome()

    def _list_targets(self):
        response = requests.get(
            f"{self._devtools_endpoint()}/json/list",
            timeout=5,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ChromeBrowserAutomationError(
                "Chrome DevTools did not return a valid target list.",
                code="invalid_target_list",
            )
        return payload

    def _list_page_targets(self) -> list[dict]:
        page_targets = []
        for index, target in enumerate(self._list_targets(), start=1):
            if str(target.get("type") or "").strip() != "page":
                continue
            target_id = str(target.get("id") or "").strip()
            if not target_id:
                continue
            page_targets.append(
                {
                    "tab_id": target_id,
                    "title": str(target.get("title") or "").strip(),
                    "url": str(target.get("url") or "").strip(),
                    "websocket_url": str(target.get("webSocketDebuggerUrl") or "").strip(),
                    "index": index,
                }
            )
        return page_targets

    def _create_target(self, initial_url: str = "about:blank") -> dict:
        encoded_url = quote(str(initial_url or "about:blank"), safe=":/?&=%#")
        response = requests.put(
            f"{self._devtools_endpoint()}/json/new?{encoded_url}",
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json() or {}
        target_id = str(payload.get("id") or "").strip()
        websocket_url = str(payload.get("webSocketDebuggerUrl") or "").strip()
        if not target_id or not websocket_url:
            raise ChromeBrowserAutomationError(
                "Chrome DevTools did not return a page target.",
                code="page_target_missing",
            )
        self._page_target_id = target_id
        return {
            "tab_id": target_id,
            "title": str(payload.get("title") or "").strip(),
            "url": str(payload.get("url") or "").strip(),
            "websocket_url": websocket_url,
        }

    def _close_target(self, tab_id: str):
        normalized_tab_id = _normalize_tab_id(tab_id)
        response = requests.get(
            f"{self._devtools_endpoint()}/json/close/{normalized_tab_id}",
            timeout=10,
        )
        response.raise_for_status()
        if self._page_target_id == normalized_tab_id:
            self._page_target_id = None

    def _resolve_existing_page_websocket_url(self):
        if not self._page_target_id:
            return None
        try:
            for target in self._list_page_targets():
                if str(target.get("tab_id") or "").strip() != self._page_target_id:
                    continue
                websocket_url = str(target.get("websocket_url") or "").strip()
                if websocket_url:
                    return websocket_url
                break
        except requests.RequestException:
            return None
        self._page_target_id = None
        return None

    def _resolve_page_websocket_url(self) -> str:
        self._ensure_chrome_ready()
        websocket_url = self._resolve_existing_page_websocket_url()
        if websocket_url:
            return websocket_url
        created = self._create_target()
        return str(created.get("websocket_url") or "").strip()

    def _open_page(self) -> _ChromeDevToolsPage:
        websocket_url = self._resolve_page_websocket_url()
        page = _ChromeDevToolsPage(websocket_url)
        try:
            page.prepare()
            return page
        except Exception:
            page.close()
            raise

    def _open_existing_page(self, *, bring_to_front: bool = False):
        websocket_url = self._resolve_existing_page_websocket_url()
        if not websocket_url:
            return None
        page = _ChromeDevToolsPage(websocket_url)
        try:
            page.prepare(bring_to_front=bring_to_front)
            return page
        except Exception:
            page.close()
            raise

    def _current_tab_payload(self) -> dict:
        normalized_tab_id = str(self._page_target_id or "").strip()
        if not normalized_tab_id:
            return {}
        for target in self._list_page_targets():
            if str(target.get("tab_id") or "").strip() == normalized_tab_id:
                return dict(target)
        return {}

    def _normalize_action_result(self, action: str, result):
        if not isinstance(result, dict):
            raise ChromeBrowserAutomationError(
                f"Chrome returned an invalid result for {action}.",
                code="invalid_browser_result",
            )
        if result.get("ok") is False:
            raise ChromeBrowserAutomationError(
                str(result.get("error") or f"Chrome failed to execute {action}."),
                code=str(result.get("errorCode") or f"{action}_failed"),
            )
        result.setdefault("ok", True)
        result.setdefault("browser", "chrome")
        result.setdefault("visible", True)
        result.setdefault("action", action)
        return result

    @staticmethod
    def _attach_page_preview(result_payload: dict, preview_payload: dict | None = None) -> dict:
        payload = dict(result_payload or {})
        preview_payload = dict(preview_payload or {})
        if preview_payload:
            payload["page_preview"] = {
                "url": str(preview_payload.get("url") or "").strip(),
                "title": str(preview_payload.get("title") or "").strip(),
                "page_text": str(preview_payload.get("page_text") or "").strip(),
                "frame_count": int(preview_payload.get("frame_count", 0) or 0),
            }
        return payload

    def _read_page_preview_from_page(self, page: _ChromeDevToolsPage, *, max_text_length: int = 1200) -> dict:
        raw_preview = page.evaluate(_build_page_preview_script(max_text_length))
        preview_payload = self._normalize_action_result("peek_page", raw_preview)
        return {
            "url": str(preview_payload.get("url", "") or "").strip(),
            "title": str(preview_payload.get("title", "") or "").strip(),
            "page_text": str(preview_payload.pop("pageText", "") or "").strip(),
            "frame_count": int(preview_payload.pop("frameCount", 0) or 0),
        }

    def navigate(self, url: str) -> dict:
        normalized_url = normalize_browser_url(url)
        page = self._open_page()
        try:
            page.navigate(normalized_url)
            settled_state = page.wait_for_navigation_settle()
            page_title = str((settled_state or {}).get("title") or "").strip()
            current_url = str((settled_state or {}).get("url") or "").strip()
            preview_payload = self._read_page_preview_from_page(page)
            result_payload = {
                "ok": True,
                "browser": "chrome",
                "visible": True,
                "action": "navigate",
                "url": str(current_url or normalized_url).strip(),
                "title": str(page_title or "").strip(),
                "tab_id": str(self._page_target_id or "").strip(),
            }
            return self._attach_page_preview(result_payload, preview_payload)
        finally:
            page.close()

    def search(self, query: str) -> dict:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            raise ChromeBrowserAutomationError("Query is required.", code="invalid_query")
        search_url = build_browser_search_url(normalized_query)
        result = self.navigate(search_url)
        result["action"] = "search"
        result["query"] = normalized_query
        result["search_url"] = search_url
        return result

    def peek_page(self, *, max_text_length: int = 800) -> dict:
        normalized_max_text_length = _normalize_positive_int(
            max_text_length,
            default=800,
            minimum=200,
            maximum=MAX_SNAPSHOT_TEXT_LIMIT,
        )
        if not self._is_debug_endpoint_ready():
            return {}
        page = self._open_existing_page(bring_to_front=False)
        if page is None:
            return {}
        try:
            preview_payload = self._read_page_preview_from_page(page, max_text_length=normalized_max_text_length)
            return {
                "url": str(preview_payload.get("url", "") or "").strip(),
                "title": str(preview_payload.get("title", "") or "").strip(),
                "page_text": str(preview_payload.get("page_text", "") or "").strip(),
                "frame_count": int(preview_payload.get("frame_count", 0) or 0),
                "tab_id": str(self._page_target_id or "").strip(),
            }
        finally:
            page.close()

    def list_tabs(self) -> dict:
        self._ensure_chrome_ready()
        tabs = self._list_page_targets()
        current_tab_id = str(self._page_target_id or "").strip()
        return {
            "ok": True,
            "action": "list_tabs",
            "tab_count": len(tabs),
            "current_tab_id": current_tab_id,
            "tabs": [
                {
                    "tab_id": str(tab.get("tab_id") or "").strip(),
                    "index": int(tab.get("index", 0) or 0),
                    "title": str(tab.get("title") or "").strip(),
                    "url": str(tab.get("url") or "").strip(),
                    "is_current": str(tab.get("tab_id") or "").strip() == current_tab_id,
                }
                for tab in tabs
            ],
        }

    def open_tab(self, url: str) -> dict:
        normalized_url = normalize_browser_url(url)
        created_target = self._create_target(normalized_url)
        page = self._open_existing_page(bring_to_front=True)
        if page is None:
            raise ChromeBrowserAutomationError(
                "Failed to open the newly created Chrome tab.",
                code="open_tab_failed",
            )
        try:
            settled_state = page.wait_for_navigation_settle()
            preview_payload = self._read_page_preview_from_page(page)
            return self._attach_page_preview(
                {
                    "ok": True,
                    "action": "open_tab",
                    "tab_id": str(created_target.get("tab_id") or self._page_target_id or "").strip(),
                    "url": str((settled_state or {}).get("url") or normalized_url).strip(),
                    "title": str((settled_state or {}).get("title") or created_target.get("title") or "").strip(),
                },
                preview_payload,
            )
        finally:
            page.close()

    def select_tab(self, tab_id: str) -> dict:
        normalized_tab_id = _normalize_tab_id(tab_id)
        target_lookup = {
            str(target.get("tab_id") or "").strip(): dict(target)
            for target in self._list_page_targets()
        }
        target_payload = target_lookup.get(normalized_tab_id)
        if target_payload is None:
            raise ChromeBrowserAutomationError(
                f"Tab {normalized_tab_id} was not found.",
                code="tab_not_found",
            )
        self._page_target_id = normalized_tab_id
        page = self._open_existing_page(bring_to_front=True)
        if page is None:
            raise ChromeBrowserAutomationError(
                f"Chrome tab {normalized_tab_id} is not available.",
                code="tab_not_found",
            )
        try:
            preview_payload = self._read_page_preview_from_page(page)
            return self._attach_page_preview(
                {
                    "ok": True,
                    "action": "select_tab",
                    "selected_tab_id": normalized_tab_id,
                    "url": str(target_payload.get("url") or "").strip(),
                    "title": str(target_payload.get("title") or "").strip(),
                },
                preview_payload,
            )
        finally:
            page.close()

    def close_tab(self, tab_id: str = "") -> dict:
        normalized_tab_id = _normalize_tab_id(tab_id or self._page_target_id)
        target_lookup = {
            str(target.get("tab_id") or "").strip(): dict(target)
            for target in self._list_page_targets()
        }
        target_payload = target_lookup.get(normalized_tab_id)
        if target_payload is None:
            raise ChromeBrowserAutomationError(
                f"Tab {normalized_tab_id} was not found.",
                code="tab_not_found",
            )
        self._close_target(normalized_tab_id)
        remaining_tabs = self._list_page_targets()
        if remaining_tabs:
            self._page_target_id = str(remaining_tabs[0].get("tab_id") or "").strip()
        current_payload = self._current_tab_payload()
        return {
            "ok": True,
            "action": "close_tab",
            "closed_tab_id": normalized_tab_id,
            "closed_title": str(target_payload.get("title") or "").strip(),
            "tab_count": len(remaining_tabs),
            "current_tab_id": str(current_payload.get("tab_id") or "").strip(),
            "title": str(current_payload.get("title") or "").strip(),
            "url": str(current_payload.get("url") or "").strip(),
        }

    def snapshot(self, *, max_elements: int = DEFAULT_SNAPSHOT_ELEMENT_LIMIT, max_text_length: int = DEFAULT_SNAPSHOT_TEXT_LIMIT) -> dict:
        normalized_max_elements = _normalize_positive_int(
            max_elements,
            default=DEFAULT_SNAPSHOT_ELEMENT_LIMIT,
            minimum=1,
            maximum=MAX_SNAPSHOT_ELEMENT_LIMIT,
        )
        normalized_max_text_length = _normalize_positive_int(
            max_text_length,
            default=DEFAULT_SNAPSHOT_TEXT_LIMIT,
            minimum=200,
            maximum=MAX_SNAPSHOT_TEXT_LIMIT,
        )
        page = self._open_page()
        try:
            raw_snapshot = page.evaluate(
                _build_snapshot_script(normalized_max_elements, normalized_max_text_length)
            )
            snapshot_payload = self._normalize_action_result("snapshot", raw_snapshot)
            snapshot_payload["page_text"] = str(snapshot_payload.pop("pageText", "") or "").strip()
            snapshot_payload["frame_count"] = int(snapshot_payload.pop("frameCount", 0) or 0)
            snapshot_payload["element_count"] = len(snapshot_payload.get("elements") or [])
            snapshot_payload["snapshot"] = _build_snapshot_text(snapshot_payload)
            snapshot_payload["tab_id"] = str(self._page_target_id or "").strip()
            return snapshot_payload
        finally:
            page.close()

    def click(self, ref: str) -> dict:
        normalized_ref = _normalize_ref(ref)
        page = self._open_page()
        try:
            target_payload = page.evaluate(_build_click_target_script(normalized_ref))
            resolved_target = self._normalize_action_result("click", target_payload)
            click_method = str(resolved_target.get("clickMethod") or "mouse").strip().lower()

            if click_method == "dom":
                action_result = page.evaluate(_build_dom_click_script(normalized_ref))
                normalized_result = self._normalize_action_result("click", action_result)
            else:
                page.click_at(resolved_target.get("x", 0), resolved_target.get("y", 0))
                normalized_result = resolved_target

            settled_state = page.wait_for_navigation_settle(
                timeout_seconds=min(6.0, CHROME_PAGE_TIMEOUT_SECONDS),
                settle_seconds=1.0,
            )
            if isinstance(settled_state, dict):
                normalized_result["title"] = str(
                    settled_state.get("title") or normalized_result.get("title") or ""
                ).strip()
                normalized_result["url"] = str(
                    settled_state.get("url") or normalized_result.get("url") or ""
                ).strip()
            normalized_result["tab_id"] = str(self._page_target_id or "").strip()
            preview_payload = self._read_page_preview_from_page(page)
            normalized_result.pop("clickMethod", None)
            return self._attach_page_preview(
                self._normalize_action_result("click", normalized_result),
                preview_payload,
            )
        finally:
            page.close()

    def type_text(self, ref: str, text: str, *, submit: bool = False) -> dict:
        normalized_ref = _normalize_ref(ref)
        normalized_text = str(text or "")
        page = self._open_page()
        try:
            action_result = page.evaluate(
                _build_type_script(normalized_ref, normalized_text, bool(submit))
            )
            normalized_result = self._normalize_action_result("type", action_result)
            normalized_result["tab_id"] = str(self._page_target_id or "").strip()
            preview_payload = self._read_page_preview_from_page(page)
            return self._attach_page_preview(normalized_result, preview_payload)
        finally:
            page.close()

    def scroll(self, *, direction: str = "down", amount: int = 800) -> dict:
        normalized_direction = _normalize_scroll_direction(direction)
        normalized_amount = _normalize_positive_int(
            amount,
            default=800,
            minimum=100,
            maximum=3000,
        )
        page = self._open_page()
        try:
            action_result = page.evaluate(
                _build_scroll_script(normalized_direction, normalized_amount)
            )
            normalized_result = self._normalize_action_result("scroll", action_result)
            normalized_result["tab_id"] = str(self._page_target_id or "").strip()
            preview_payload = self._read_page_preview_from_page(page)
            return self._attach_page_preview(normalized_result, preview_payload)
        finally:
            page.close()

    def go_back(self) -> dict:
        page = self._open_page()
        try:
            action_result = page.evaluate(GO_BACK_SCRIPT)
            normalized_result = self._normalize_action_result("go_back", action_result)
            normalized_result["tab_id"] = str(self._page_target_id or "").strip()
            preview_payload = self._read_page_preview_from_page(page)
            return self._attach_page_preview(normalized_result, preview_payload)
        finally:
            page.close()

    def press_key(self, key: str) -> dict:
        normalized_key = str(key or "").strip()
        page = self._open_page()
        try:
            page.press_key(normalized_key)
            settled_state = page.wait_for_navigation_settle(
                timeout_seconds=min(6.0, CHROME_PAGE_TIMEOUT_SECONDS),
                settle_seconds=0.8,
            )
            preview_payload = self._read_page_preview_from_page(page)
            return self._attach_page_preview(
                {
                    "ok": True,
                    "action": "press_key",
                    "key": normalized_key,
                    "tab_id": str(self._page_target_id or "").strip(),
                    "url": str((settled_state or {}).get("url") or preview_payload.get("url") or "").strip(),
                    "title": str((settled_state or {}).get("title") or preview_payload.get("title") or "").strip(),
                },
                preview_payload,
            )
        finally:
            page.close()

    def wait(
        self,
        *,
        ref: str = "",
        text_contains: str = "",
        url_contains: str = "",
        title_contains: str = "",
        timeout_ms: int = 5000,
    ) -> dict:
        normalized_timeout_ms = _normalize_positive_int(
            timeout_ms,
            default=5000,
            minimum=250,
            maximum=60000,
        )
        normalized_ref = str(ref or "").strip()
        normalized_text = str(text_contains or "").strip()
        normalized_url = str(url_contains or "").strip()
        normalized_title = str(title_contains or "").strip()
        page = self._open_page()
        try:
            if not any([normalized_ref, normalized_text, normalized_url, normalized_title]):
                settled_state = page.wait_for_navigation_settle(
                    timeout_seconds=normalized_timeout_ms / 1000.0,
                    settle_seconds=min(1.5, normalized_timeout_ms / 1000.0),
                )
                preview_payload = self._read_page_preview_from_page(page)
                return self._attach_page_preview(
                    {
                        "ok": True,
                        "action": "wait",
                        "matched": ["settled"],
                        "tab_id": str(self._page_target_id or "").strip(),
                        "url": str((settled_state or {}).get("url") or preview_payload.get("url") or "").strip(),
                        "title": str((settled_state or {}).get("title") or preview_payload.get("title") or "").strip(),
                    },
                    preview_payload,
                )
            matched_payload = page.wait_for_value(
                _build_wait_condition_script(
                    ref=normalized_ref,
                    text_contains=normalized_text,
                    url_contains=normalized_url,
                    title_contains=normalized_title,
                ),
                timeout_seconds=normalized_timeout_ms / 1000.0,
                interval_seconds=0.25,
                description="browser wait condition",
                validator=lambda candidate: isinstance(candidate, dict) and bool(candidate.get("matched")),
            )
            normalized_result = self._normalize_action_result("wait", matched_payload)
            normalized_result["tab_id"] = str(self._page_target_id or "").strip()
            preview_payload = self._read_page_preview_from_page(page)
            return self._attach_page_preview(normalized_result, preview_payload)
        finally:
            page.close()

    def screenshot(self, *, full_page: bool = False, file_name: str = "") -> dict:
        page = self._open_page()
        try:
            encoded_png = page.capture_screenshot(full_page=bool(full_page))
            preview_payload = self._read_page_preview_from_page(page)
            os.makedirs(CHROME_SCREENSHOT_DIR, exist_ok=True)
            sanitized_name = _sanitize_browser_filename(file_name, default_name=f"browser-{int(time.time() * 1000)}")
            screenshot_path = os.path.join(CHROME_SCREENSHOT_DIR, sanitized_name)
            with open(screenshot_path, "wb") as file:
                file.write(base64.b64decode(encoded_png))
            return self._attach_page_preview(
                {
                    "ok": True,
                    "action": "screenshot",
                    "full_page": bool(full_page),
                    "screenshot_path": screenshot_path,
                    "mime_type": "image/png",
                    "tab_id": str(self._page_target_id or "").strip(),
                    "url": str(preview_payload.get("url") or "").strip(),
                    "title": str(preview_payload.get("title") or "").strip(),
                },
                preview_payload,
            )
        finally:
            page.close()
