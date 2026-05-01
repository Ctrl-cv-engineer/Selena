import logging

try:
    from DialogueSystem.browser.chrome_browser import (
        ChromeBrowserAutomationError,
        ChromeBrowserController,
    )
except ImportError:
    from DialogueSystem.browser.chrome_browser import (
        ChromeBrowserAutomationError,
        ChromeBrowserController,
    )


logger = logging.getLogger(__name__)
CHROME_BROWSER_CONTROLLER_ATTR = "_chrome_browser_controller"


def _get_controller(registry):
    controller = getattr(registry.owner, CHROME_BROWSER_CONTROLLER_ATTR, None)
    if controller is None:
        controller = ChromeBrowserController()
        setattr(registry.owner, CHROME_BROWSER_CONTROLLER_ATTR, controller)
    return controller


def _error_payload(action: str, error: Exception, **details):
    payload = {
        "ok": False,
        "browser": "chrome",
        "visible": True,
        "action": action,
        "error": str(error),
    }
    if isinstance(error, ChromeBrowserAutomationError) or getattr(error, "code", None):
        payload["error_code"] = error.code
    payload.update({key: value for key, value in details.items() if value not in (None, "")})
    return payload


def _run(registry, action: str, handler_name: str, *, details=None, **kwargs):
    controller = _get_controller(registry)
    handler = getattr(controller, handler_name)
    try:
        return handler(**kwargs)
    except Exception as error:
        logger.warning("%s failed | kwargs=%s | error=%s", handler_name, kwargs, error)
        return _error_payload(action, error, **(details or {}))


def register_tools(registry):
    registry.register(
        "browserNavigate",
        lambda Url: _run(
            registry,
            "navigate",
            "navigate",
            details={"url": str(Url or "").strip()},
            url=Url,
        ),
    )
    registry.register(
        "browserSearch",
        lambda Query: _run(
            registry,
            "search",
            "search",
            details={"query": str(Query or "").strip()},
            query=Query,
        ),
    )
    registry.register(
        "browserSnapshot",
        lambda MaxElements=None, MaxTextLength=None: _run(
            registry,
            "snapshot",
            "snapshot",
            max_elements=MaxElements,
            max_text_length=MaxTextLength,
        ),
    )
    registry.register(
        "browserClick",
        lambda Ref: _run(
            registry,
            "click",
            "click",
            details={"ref": str(Ref or "").strip()},
            ref=Ref,
        ),
    )
    registry.register(
        "browserType",
        lambda Ref, Text, Submit=False: _run(
            registry,
            "type",
            "type_text",
            details={"ref": str(Ref or "").strip()},
            ref=Ref,
            text=Text,
            submit=Submit,
        ),
    )
    registry.register(
        "browserScroll",
        lambda Direction="down", Amount=800: _run(
            registry,
            "scroll",
            "scroll",
            details={"direction": str(Direction or "").strip(), "amount": Amount},
            direction=Direction,
            amount=Amount,
        ),
    )
    registry.register(
        "browserGoBack",
        lambda: _run(registry, "go_back", "go_back"),
    )
    registry.register(
        "browserListTabs",
        lambda: _run(registry, "list_tabs", "list_tabs"),
    )
    registry.register(
        "browserSelectTab",
        lambda TabId: _run(
            registry,
            "select_tab",
            "select_tab",
            details={"tab_id": str(TabId or "").strip()},
            tab_id=TabId,
        ),
    )
    registry.register(
        "browserCloseTab",
        lambda TabId="": _run(
            registry,
            "close_tab",
            "close_tab",
            details={"tab_id": str(TabId or "").strip()},
            tab_id=TabId,
        ),
    )
    registry.register(
        "browserWait",
        lambda Ref="", TextContains="", UrlContains="", TitleContains="", TimeoutMs=5000: _run(
            registry,
            "wait",
            "wait",
            details={
                "ref": str(Ref or "").strip(),
                "text_contains": str(TextContains or "").strip(),
                "url_contains": str(UrlContains or "").strip(),
                "title_contains": str(TitleContains or "").strip(),
                "timeout_ms": TimeoutMs,
            },
            ref=Ref,
            text_contains=TextContains,
            url_contains=UrlContains,
            title_contains=TitleContains,
            timeout_ms=TimeoutMs,
        ),
    )
    registry.register(
        "browserPressKey",
        lambda Key: _run(
            registry,
            "press_key",
            "press_key",
            details={"key": str(Key or "").strip()},
            key=Key,
        ),
    )
    registry.register(
        "browserScreenshot",
        lambda FullPage=False, FileName="": _run(
            registry,
            "screenshot",
            "screenshot",
            details={"full_page": bool(FullPage), "file_name": str(FileName or "").strip()},
            full_page=FullPage,
            file_name=FileName,
        ),
    )
