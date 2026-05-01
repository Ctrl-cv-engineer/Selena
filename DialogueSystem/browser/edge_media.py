try:
    from DialogueSystem.browser.firefox_media import (
        FIREFOX_READY_TIMEOUT_SECONDS,
        FirefoxMediaAutomationError,
        FirefoxMediaController,
    )
except ImportError:
    from firefox_media import (
        FIREFOX_READY_TIMEOUT_SECONDS,
        FirefoxMediaAutomationError,
        FirefoxMediaController,
    )


EDGE_READY_TIMEOUT_SECONDS = FIREFOX_READY_TIMEOUT_SECONDS


class EdgeMediaAutomationError(FirefoxMediaAutomationError):
    def __init__(self, message: str, *, code: str = "edge_media_error"):
        super().__init__(message, code=code)


class EdgeMediaController(FirefoxMediaController):
    """Compatibility wrapper that now routes all media work through Chrome."""
