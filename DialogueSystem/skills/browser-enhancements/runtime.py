try:
    from DialogueSystem.browser.browser_enhancements import build_browser_enhancement_handlers
except ImportError:
    from DialogueSystem.browser.browser_enhancements import build_browser_enhancement_handlers


def register_tools(registry):
    handlers = build_browser_enhancement_handlers(registry.owner)
    for tool_name, handler in handlers.items():
        registry.register(tool_name, handler)
