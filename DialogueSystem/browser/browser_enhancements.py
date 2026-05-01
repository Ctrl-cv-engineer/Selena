"""Additional browser helpers exposed as richer web tools."""

from __future__ import annotations

try:
    from .chrome_browser import ChromeBrowserController
except ImportError:
    from chrome_browser import ChromeBrowserController


def build_browser_enhancement_handlers(owner):
    def _normalize_bool(value, default: bool = False) -> bool:
        if value is None:
            return bool(default)
        if isinstance(value, bool):
            return value
        normalized = str(value or "").strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return bool(default)

    def _normalize_candidate_limit(value, default: int = 5) -> int:
        try:
            normalized = int(value if value not in (None, "") else default)
        except (TypeError, ValueError):
            normalized = default
        return max(1, min(normalized, 12))

    def _normalize_preview_text_limit(value, default: int = 2400) -> int:
        try:
            normalized = int(value if value not in (None, "") else default)
        except (TypeError, ValueError):
            normalized = default
        return max(400, min(normalized, 2400))

    def _collect_link_candidates(snapshot_payload, *, max_candidates: int = 5):
        max_candidates = _normalize_candidate_limit(max_candidates, default=5)
        candidates = []
        seen = set()
        for item in list((snapshot_payload or {}).get("elements") or []):
            ref = str(item.get("ref") or "").strip()
            href = str(item.get("href") or "").strip()
            if not ref or not href:
                continue
            role = str(item.get("role") or "").strip().lower()
            if role != "link":
                continue
            label = str(item.get("label") or item.get("text") or "").strip()
            dedupe_key = (href, label)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            candidates.append(
                {
                    "ref": ref,
                    "label": label,
                    "href": href,
                    "text": str(item.get("text") or "").strip(),
                }
            )
            if len(candidates) >= max_candidates:
                break
        return candidates

    def _controller():
        controller = getattr(owner, "_chrome_browser_controller", None)
        if controller is None:
            controller = ChromeBrowserController()
            setattr(owner, "_chrome_browser_controller", controller)
        return controller

    def browser_extract_page(MaxTextLength: int = 5000):
        snapshot = _controller().snapshot(max_elements=120, max_text_length=MaxTextLength)
        snapshot["action"] = "extract_page"
        return snapshot

    def browser_open_tab(Url: str):
        return _controller().open_tab(Url)

    def browser_read_linked_page(
        Query: str = "",
        Ref: str = "",
        MaxTextLength: int = 5000,
        AutoOpenFirst: bool = False,
        MaxCandidates: int = 5,
    ):
        controller = _controller()
        normalized_query = str(Query or "").strip()
        normalized_ref = str(Ref or "").strip()
        auto_open_first = _normalize_bool(AutoOpenFirst, False)
        max_candidates = _normalize_candidate_limit(MaxCandidates, default=5)
        preview_text_limit = _normalize_preview_text_limit(MaxTextLength, default=2400)

        search_result = {}
        if normalized_query:
            search_result = controller.search(normalized_query)

        snapshot = controller.snapshot(max_elements=30, max_text_length=preview_text_limit)
        candidates = _collect_link_candidates(snapshot, max_candidates=max_candidates)

        if not normalized_ref:
            if not candidates:
                return {
                    "ok": False,
                    "action": "read_linked_page",
                    "query": normalized_query,
                    "error": "No visible linked page candidate was found in the current snapshot.",
                    "snapshot": snapshot.get("snapshot", ""),
                    "candidates": [],
                    "candidate_count": 0,
                }
            if not auto_open_first:
                return {
                    "ok": True,
                    "action": "read_linked_page",
                    "query": normalized_query,
                    "opened": False,
                    "requires_selection": True,
                    "candidate_count": len(candidates),
                    "candidates": candidates,
                    "snapshot": snapshot.get("snapshot", ""),
                    "search_result": search_result,
                }
            normalized_ref = str(candidates[0].get("ref") or "").strip()

        click_result = controller.click(normalized_ref)
        page_snapshot = controller.snapshot(max_elements=40, max_text_length=MaxTextLength)
        return {
            "ok": True,
            "action": "read_linked_page",
            "query": normalized_query,
            "opened": True,
            "requires_selection": False,
            "clicked_ref": normalized_ref,
            "click_result": click_result,
            "candidate_count": len(candidates),
            "candidates": candidates,
            "snapshot": snapshot.get("snapshot", ""),
            "search_result": search_result,
            "page": {
                "url": page_snapshot.get("url", ""),
                "title": page_snapshot.get("title", ""),
                "page_text": page_snapshot.get("page_text", ""),
                "snapshot": page_snapshot.get("snapshot", ""),
                "tab_id": page_snapshot.get("tab_id", ""),
            },
        }

    return {
        "browserExtractPage": browser_extract_page,
        "browserOpenTab": browser_open_tab,
        "browserReadLinkedPage": browser_read_linked_page,
    }
