import socket

try:
    from DialogueSystem.browser.chrome_browser import ChromeBrowserController
except ImportError:
    from chrome_browser import ChromeBrowserController


FIREFOX_READY_TIMEOUT_SECONDS = 20.0
LIKED_PLAYLIST_ALIASES = {
    "\u6211\u559c\u6b22\u7684\u97f3\u4e50",
    "\u559c\u6b22\u7684\u97f3\u4e50",
    "liked songs",
    "liked music",
    "my liked music",
    "my favorite songs",
}


class FirefoxMediaAutomationError(RuntimeError):
    def __init__(self, message: str, *, code: str = "firefox_media_error"):
        super().__init__(message)
        self.code = code


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        return int(server.getsockname()[1])


class FirefoxMediaController:
    """
    Compatibility adapter for legacy media calls.

    All media actions now route through the generic Chrome browser controller.
    """

    def __init__(self, *, profile_dir=None, ready_timeout_seconds: float = FIREFOX_READY_TIMEOUT_SECONDS):
        self._profile_dir = profile_dir
        self._ready_timeout_seconds = ready_timeout_seconds
        self._browser = None

    def _get_browser(self) -> ChromeBrowserController:
        if self._browser is None:
            self._browser = ChromeBrowserController()
        return self._browser

    def _normalize_query(self, query: str, *, action: str) -> str:
        normalized_query = str(query or "").strip()
        if normalized_query:
            return normalized_query
        raise FirefoxMediaAutomationError(
            f"Query is required for {action}.",
            code="invalid_query",
        )

    def _run_browser_action(self, handler_name: str, **kwargs) -> dict:
        browser = self._get_browser()
        try:
            handler = getattr(browser, handler_name)
            return handler(**kwargs)
        except Exception as error:
            raise FirefoxMediaAutomationError(
                str(error),
                code=str(getattr(error, "code", None) or "generic_browser_error"),
            ) from error

    def _search_site(self, query: str, *, site: str, action: str, search_prefix: str = "") -> dict:
        normalized_query = self._normalize_query(query, action=action)
        search_query = f"site:{site} {search_prefix}{normalized_query}".strip()
        result = self._run_browser_action("search", query=search_query)
        result.update(
            {
                "site": site,
                "action": action,
                "query": normalized_query,
                "search_query": search_query,
                "route": "generic_browser",
            }
        )
        return result

    def play_bilibili_video(self, query: str) -> dict:
        return self._search_site(
            query,
            site="www.bilibili.com",
            action="play_video",
        )

    def play_douyin_video(self, query: str) -> dict:
        return self._search_site(
            query,
            site="www.douyin.com",
            action="play_video",
        )

    def play_netease_music(self, query: str) -> dict:
        return self._search_site(
            query,
            site="music.163.com",
            action="play_music",
        )

    def play_netease_playlist(self, query: str) -> dict:
        normalized_query = self._normalize_query(query, action="play_playlist")
        if normalized_query.lower() in LIKED_PLAYLIST_ALIASES:
            target_url = "https://music.163.com/#/my/"
            result = self._run_browser_action("navigate", url=target_url)
            result.update(
                {
                    "site": "music.163.com",
                    "action": "play_playlist",
                    "query": normalized_query,
                    "url": target_url,
                    "route": "generic_browser",
                }
            )
            return result

        return self._search_site(
            normalized_query,
            site="music.163.com",
            action="play_playlist",
            search_prefix="\u6b4c\u5355 ",
        )
