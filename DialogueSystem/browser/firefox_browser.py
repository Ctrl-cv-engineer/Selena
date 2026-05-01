try:
    from DialogueSystem.browser.chrome_browser import (
        BROWSER_REF_ATTRIBUTE,
        DEFAULT_SNAPSHOT_ELEMENT_LIMIT,
        DEFAULT_SNAPSHOT_TEXT_LIMIT,
        GO_BACK_SCRIPT,
        MAX_SNAPSHOT_ELEMENT_LIMIT,
        MAX_SNAPSHOT_TEXT_LIMIT,
        ChromeBrowserAutomationError,
        ChromeBrowserController,
        _build_click_script,
        _build_scroll_script,
        _build_snapshot_script,
        _build_snapshot_text,
        _build_type_script,
        _normalize_positive_int,
        _normalize_ref,
        _normalize_scroll_direction,
    )
except ImportError:
    from chrome_browser import (
        BROWSER_REF_ATTRIBUTE,
        DEFAULT_SNAPSHOT_ELEMENT_LIMIT,
        DEFAULT_SNAPSHOT_TEXT_LIMIT,
        GO_BACK_SCRIPT,
        MAX_SNAPSHOT_ELEMENT_LIMIT,
        MAX_SNAPSHOT_TEXT_LIMIT,
        ChromeBrowserAutomationError,
        ChromeBrowserController,
        _build_click_script,
        _build_scroll_script,
        _build_snapshot_script,
        _build_snapshot_text,
        _build_type_script,
        _normalize_positive_int,
        _normalize_ref,
        _normalize_scroll_direction,
    )


class FirefoxBrowserAutomationError(ChromeBrowserAutomationError):
    def __init__(self, message: str, *, code: str = "firefox_browser_error"):
        super().__init__(message, code=code)


class FirefoxBrowserController(ChromeBrowserController):
    """Compatibility wrapper that now routes all browser work through Chrome."""


__all__ = [
    "BROWSER_REF_ATTRIBUTE",
    "DEFAULT_SNAPSHOT_ELEMENT_LIMIT",
    "DEFAULT_SNAPSHOT_TEXT_LIMIT",
    "GO_BACK_SCRIPT",
    "MAX_SNAPSHOT_ELEMENT_LIMIT",
    "MAX_SNAPSHOT_TEXT_LIMIT",
    "FirefoxBrowserAutomationError",
    "FirefoxBrowserController",
    "_build_click_script",
    "_build_scroll_script",
    "_build_snapshot_script",
    "_build_snapshot_text",
    "_build_type_script",
    "_normalize_positive_int",
    "_normalize_ref",
    "_normalize_scroll_direction",
]
