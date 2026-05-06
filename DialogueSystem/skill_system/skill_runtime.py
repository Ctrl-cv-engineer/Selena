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


def _normalize_arg_token(value) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _map_handler_arguments(handler, arguments: dict) -> dict:
    import inspect

    raw_args = dict(arguments or {})
    try:
        sig = inspect.signature(handler)
    except (ValueError, TypeError):
        return raw_args

    param_lookup = {
        _normalize_arg_token(name): name
        for name in sig.parameters
        if _normalize_arg_token(name)
    }
    timeout_like_tokens = {
        "timeout",
        "timeoutms",
        "timeoutmillis",
        "timeoutmilliseconds",
        "timeoutsec",
        "timeoutsecs",
        "timeoutsecond",
        "timeoutseconds",
    }
    timeout_alias_tokens = {
        "time",
        "timems",
        "timemillis",
        "timemilliseconds",
        "timesec",
        "timesecs",
        "timesecond",
        "timeseconds",
        "wait",
        "waitms",
        "delay",
        "delayms",
        "duration",
        "durationms",
        "ms",
        "milliseconds",
        "seconds",
        "secs",
    }
    timeout_like_params = [
        name
        for token, name in param_lookup.items()
        if token in timeout_like_tokens
    ]

    mapped_args = {}
    for key, value in raw_args.items():
        incoming_token = _normalize_arg_token(key)
        canonical = param_lookup.get(incoming_token)
        if (
            canonical is None
            and incoming_token in timeout_alias_tokens
            and len(timeout_like_params) == 1
        ):
            canonical = timeout_like_params[0]
        mapped_args[canonical if canonical else key] = value
    return mapped_args


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
        normalized_tool_name = str(tool_name or "").strip()
        handler = self._handlers.get(normalized_tool_name)
        if handler is None:
            raise KeyError(normalized_tool_name)

        return handler(**_map_handler_arguments(handler, arguments))


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
