import os
import re
from urllib.parse import quote_plus, urlparse


ALLOWED_BROWSER_URL_SCHEMES = {"http", "https"}
BROWSER_SEARCH_TEMPLATE = "https://www.bing.com/search?q={query}"
BROWSER_CANDIDATE_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
)


class BrowserLaunchError(RuntimeError):
    pass


def _looks_like_browser_host(value: str) -> bool:
    normalized_value = str(value or "").strip()
    if not normalized_value or any(character.isspace() for character in normalized_value):
        return False
    if normalized_value.lower().startswith("localhost"):
        return True
    if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?", normalized_value):
        return True
    return "." in normalized_value


def normalize_browser_url(raw_url: str) -> str:
    normalized_url = str(raw_url or "").strip()
    if not normalized_url:
        raise ValueError("Url is required.")

    parsed_url = urlparse(normalized_url)
    if not parsed_url.scheme:
        host_candidate = normalized_url.split("/", 1)[0]
        if not _looks_like_browser_host(host_candidate):
            raise ValueError(
                "Url must be an http(s) address or domain. Use browserSearch for search keywords."
            )
        normalized_url = f"https://{normalized_url}"
        parsed_url = urlparse(normalized_url)

    scheme = parsed_url.scheme.lower()
    if scheme not in ALLOWED_BROWSER_URL_SCHEMES:
        raise ValueError(
            f"Unsupported URL scheme: {parsed_url.scheme}. Only http and https are allowed."
        )
    if not parsed_url.netloc:
        raise ValueError("Url is missing a host.")
    return normalized_url


def build_browser_search_url(query: str) -> str:
    normalized_query = str(query or "").strip()
    if not normalized_query:
        raise ValueError("Query is required.")
    return BROWSER_SEARCH_TEMPLATE.format(query=quote_plus(normalized_query))


def find_browser_executable() -> str:
    for candidate_path in BROWSER_CANDIDATE_PATHS:
        if os.path.isfile(candidate_path):
            return candidate_path
    raise BrowserLaunchError("Google Chrome is not installed at a supported path.")


def find_firefox_executable() -> str:
    """Legacy compatibility name; returns the supported generic browser path."""
    return find_browser_executable()


def open_url_in_browser(url: str) -> dict:
    normalized_url = normalize_browser_url(url)
    try:
        from DialogueSystem.browser.chrome_browser import ChromeBrowserController
    except ImportError:
        from chrome_browser import ChromeBrowserController
    return ChromeBrowserController().navigate(normalized_url)


def search_in_browser(query: str) -> dict:
    normalized_query = str(query or "").strip()
    if not normalized_query:
        raise ValueError("Query is required.")
    try:
        from DialogueSystem.browser.chrome_browser import ChromeBrowserController
    except ImportError:
        from chrome_browser import ChromeBrowserController
    return ChromeBrowserController().search(normalized_query)


def open_url_in_firefox(url: str) -> dict:
    """Legacy compatibility name; now always uses the generic browser route."""
    return open_url_in_browser(url)


def search_in_firefox(query: str) -> dict:
    """Legacy compatibility name; now always uses the generic browser route."""
    return search_in_browser(query)
