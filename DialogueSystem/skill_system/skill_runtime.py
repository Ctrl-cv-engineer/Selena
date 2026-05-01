import importlib.util
import json
import logging
import os
import sys

try:
    from ..config.paths import SKILLS_DIR
except ImportError:
    from DialogueSystem.config.paths import SKILLS_DIR


logger = logging.getLogger(__name__)


class SkillToolRegistry:
    def __init__(self, owner):
        self.owner = owner
        self._handlers = {}

    def register(self, tool_name: str, handler):
        normalized_tool_name = str(tool_name or "").strip()
        if not normalized_tool_name:
            raise ValueError("tool_name is required")
        if not callable(handler):
            raise TypeError(f"Handler for {normalized_tool_name} must be callable")
        if normalized_tool_name in self._handlers:
            raise ValueError(f"Duplicate skill tool handler: {normalized_tool_name}")
        self._handlers[normalized_tool_name] = handler

    def has(self, tool_name: str) -> bool:
        return str(tool_name or "").strip() in self._handlers

    def execute(self, tool_name: str, arguments: dict):
        import inspect

        normalized_tool_name = str(tool_name or "").strip()
        handler = self._handlers.get(normalized_tool_name)
        if handler is None:
            raise KeyError(normalized_tool_name)

        raw_args = dict(arguments or {})
        # LLM may send parameter names with different casing (e.g. "url" vs "Url").
        # Build a case-insensitive mapping from the handler's actual parameter names.
        try:
            sig = inspect.signature(handler)
            param_lookup = {name.lower(): name for name in sig.parameters}
            mapped_args = {}
            for key, value in raw_args.items():
                canonical = param_lookup.get(key.lower())
                if canonical is not None:
                    mapped_args[canonical] = value
                else:
                    mapped_args[key] = value
        except (ValueError, TypeError):
            mapped_args = raw_args

        return handler(**mapped_args)


def _load_json_file(file_path: str):
    with open(file_path, "r", encoding="utf-8") as file:
        return json.load(file)


def _iter_enabled_skill_runtime_paths():
    if not os.path.isdir(SKILLS_DIR):
        return

    for skill_folder in sorted(os.listdir(SKILLS_DIR)):
        skill_dir = os.path.join(SKILLS_DIR, skill_folder)
        if not os.path.isdir(skill_dir):
            continue

        manifest_path = os.path.join(skill_dir, "manifest.json")
        runtime_path = os.path.join(skill_dir, "runtime.py")
        if not os.path.exists(manifest_path) or not os.path.exists(runtime_path):
            continue

        try:
            manifest = _load_json_file(manifest_path)
        except Exception:
            logger.exception("Failed to load skill manifest for runtime discovery | path=%s", manifest_path)
            continue

        if not manifest.get("enabled", True):
            continue

        runtime_mode = str(manifest.get("runtime_mode") or "").strip().lower()
        trusted_runtime = bool(manifest.get("trusted_runtime", False))
        if runtime_mode == "disabled" or not trusted_runtime:
            logger.info(
                "Skip untrusted skill runtime | skill=%s | runtime_mode=%s | trusted_runtime=%s",
                skill_folder,
                runtime_mode or "default",
                trusted_runtime,
            )
            continue

        skill_name = str(manifest.get("name") or skill_folder).strip() or skill_folder
        module_safe_name = skill_name.replace("-", "_")
        yield module_safe_name, runtime_path


def _load_module_from_path(module_name: str, module_path: str):
    module_spec = importlib.util.spec_from_file_location(module_name, module_path)
    if module_spec is None or module_spec.loader is None:
        raise ImportError(f"Unable to create module spec for {module_path}")

    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_name] = module
    module_spec.loader.exec_module(module)
    return module


def load_skill_tool_registry(owner):
    registry = SkillToolRegistry(owner)

    for skill_name, runtime_path in _iter_enabled_skill_runtime_paths():
        module_name = f"dialogue_system_skill_runtime_{skill_name}"
        try:
            runtime_module = _load_module_from_path(module_name, runtime_path)
            register_tools = getattr(runtime_module, "register_tools", None)
            if not callable(register_tools):
                logger.warning(
                    "Skip skill runtime without register_tools() | skill=%s | path=%s",
                    skill_name,
                    runtime_path,
                )
                continue
            register_tools(registry)
            logger.info("Skill runtime loaded | skill=%s | path=%s", skill_name, runtime_path)
        except Exception:
            logger.exception("Failed to load skill runtime | skill=%s | path=%s", skill_name, runtime_path)

    return registry
