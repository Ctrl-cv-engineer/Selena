"""Skill self-management helpers for DialogueSystem."""

from __future__ import annotations

import json
import logging
import os
import re

try:
    from ..config.paths import SKILLS_DIR
except ImportError:
    from DialogueSystem.config.paths import SKILLS_DIR


logger = logging.getLogger(__name__)
SKILL_MARKDOWN_FILE = "SKILL.md"
SKILL_OPTIONAL_DIRS = ("scripts", "references", "assets")


def _sanitize_skill_folder_name(name: str) -> str:
    raw = str(name or "").strip()
    if not raw:
        raise ValueError("Skill name is required.")
    normalized = re.sub(r"[^A-Za-z0-9\-]+", "-", raw).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized).lower()
    if not normalized:
        raise ValueError("Skill name is required.")
    return normalized


def _ensure_skill_dir(skill_name: str) -> str:
    folder_name = _sanitize_skill_folder_name(skill_name)
    skill_dir = os.path.join(SKILLS_DIR, folder_name)
    os.makedirs(skill_dir, exist_ok=True)
    os.makedirs(os.path.join(skill_dir, "tools"), exist_ok=True)
    for optional_dir in SKILL_OPTIONAL_DIRS:
        os.makedirs(os.path.join(skill_dir, optional_dir), exist_ok=True)
    return skill_dir


def _write_json(path: str, payload: dict):
    with open(path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")


def _write_text(path: str, content: str):
    with open(path, "w", encoding="utf-8") as file:
        file.write(str(content or "").rstrip() + "\n")


def _normalize_string_list(values):
    normalized = []
    seen = set()
    for item in values or []:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _build_skill_markdown(
    *,
    skill_name: str,
    description: str,
    when_to_use: list,
    intent_examples: list,
    tool_names: list,
    skill_instructions: str = "",
    compatibility: str = "",
    license_text: str = "",
    metadata: dict = None,
    allowed_tools: list = None,
    disable_model_invocation: bool = False,
    user_invocable: bool = True,
    paths: list = None,
) -> str:
    body = str(skill_instructions or "").strip()
    if not body:
        display_name = skill_name.replace("-", " ").title()
        sections = [
            f"# {display_name}",
            "",
            str(description or "").strip() or "Managed DialogueSystem skill.",
        ]
        if when_to_use:
            sections.extend(["", "## When To Use"])
            sections.extend(f"- {item}" for item in when_to_use)
        if intent_examples:
            sections.extend(["", "## Example Intents"])
            sections.extend(f"- {item}" for item in intent_examples)
        if tool_names:
            sections.extend(["", "## Tools"])
            sections.extend(f"- `{tool_name}`" for tool_name in tool_names)
        body = "\n".join(sections)
    normalized_name = _sanitize_skill_folder_name(skill_name)
    normalized_description = str(description or "").strip()
    frontmatter_lines = [
        "---",
        f"name: {normalized_name}",
        f"description: {normalized_description}",
    ]
    if license_text:
        frontmatter_lines.append(f"license: {license_text}")
    if compatibility:
        frontmatter_lines.append(f"compatibility: {compatibility}")
    if metadata:
        frontmatter_lines.append("metadata:")
        for key, value in metadata.items():
            frontmatter_lines.append(f"  {key}: \"{value}\"")
    if allowed_tools:
        frontmatter_lines.append(f"allowed-tools: {' '.join(allowed_tools)}")
    if disable_model_invocation:
        frontmatter_lines.append("disable-model-invocation: true")
    if not user_invocable:
        frontmatter_lines.append("user-invocable: false")
    if paths:
        frontmatter_lines.append("paths:")
        for pattern in paths:
            frontmatter_lines.append(f"  - {pattern}")
    frontmatter_lines.append("---")
    return "\n".join(frontmatter_lines + ["", body])


def _parse_skill_markdown_metadata(skill_path: str):
    try:
        try:
            from ..config.resources import _parse_skill_markdown
        except ImportError:
            from DialogueSystem.config.resources import _parse_skill_markdown
        if not os.path.exists(skill_path):
            return {}
        parsed = _parse_skill_markdown(skill_path)
        return dict(parsed.get("frontmatter") or {})
    except Exception:
        return {}


def create_or_update_skill(
    *,
    skill_name: str,
    description: str,
    when_to_use=None,
    intent_examples=None,
    tool_definitions=None,
    runtime_code: str = "",
    enabled: bool = True,
    skill_instructions: str = "",
    compatibility: str = "",
    license_text: str = "",
    metadata: dict = None,
    allowed_tools: list = None,
    disable_model_invocation: bool = False,
    user_invocable: bool = True,
    paths: list = None,
) -> dict:
    skill_dir = _ensure_skill_dir(skill_name)
    manifest_path = os.path.join(skill_dir, "manifest.json")
    skill_markdown_path = os.path.join(skill_dir, SKILL_MARKDOWN_FILE)
    runtime_path = os.path.join(skill_dir, "runtime.py")
    existing_runtime_path = runtime_path if os.path.exists(runtime_path) else ""
    if str(runtime_code or "").strip():
        raise ValueError("RuntimeCode is disabled for managed skills. Use trusted built-in runtimes only.")
    normalized_when_to_use = _normalize_string_list(when_to_use)
    normalized_intent_examples = _normalize_string_list(intent_examples)
    normalized_name = _sanitize_skill_folder_name(skill_name)
    manifest = {
        "name": normalized_name,
        "version": "1.0.0",
        "enabled": bool(enabled),
        "description": str(description or "").strip(),
        "when_to_use": normalized_when_to_use,
        "intent_examples": normalized_intent_examples,
        "runtime_mode": "disabled",
        "trusted_runtime": False,
        "source_format": "agent_skill",
        "allowed_tools": _normalize_string_list(allowed_tools),
        "disable_model_invocation": bool(disable_model_invocation),
        "user_invocable": bool(user_invocable),
        "paths": _normalize_string_list(paths),
    }
    _write_json(manifest_path, manifest)

    tool_paths = []
    tool_names = []
    if tool_definitions is not None:
        tools_dir = os.path.join(skill_dir, "tools")
        for file_name in os.listdir(tools_dir):
            if file_name.endswith(".json"):
                os.remove(os.path.join(tools_dir, file_name))

    for tool_definition in list(tool_definitions or []):
        function_name = str((tool_definition.get("function") or {}).get("name", "")).strip()
        if not function_name:
            raise ValueError("Every managed skill tool must include function.name")
        tool_path = os.path.join(skill_dir, "tools", f"{function_name}.json")
        _write_json(tool_path, tool_definition)
        tool_paths.append(tool_path)
        tool_names.append(function_name)

    _write_text(
        skill_markdown_path,
        _build_skill_markdown(
            skill_name=normalized_name,
            description=str(description or "").strip(),
            when_to_use=normalized_when_to_use,
            intent_examples=normalized_intent_examples,
            tool_names=tool_names,
            skill_instructions=skill_instructions,
            compatibility=str(compatibility or "").strip(),
            license_text=str(license_text or "").strip(),
            metadata=metadata,
            allowed_tools=allowed_tools,
            disable_model_invocation=bool(disable_model_invocation),
            user_invocable=bool(user_invocable),
            paths=_normalize_string_list(paths),
        ),
    )

    if existing_runtime_path and os.path.exists(existing_runtime_path):
        os.remove(existing_runtime_path)

    return {
        "ok": True,
        "skill_dir": skill_dir,
        "manifest_path": manifest_path,
        "skill_path": skill_markdown_path,
        "runtime_path": "",
        "tool_paths": tool_paths,
    }


def delete_skill(skill_name: str) -> dict:
    folder_name = _sanitize_skill_folder_name(skill_name)
    skill_dir = os.path.join(SKILLS_DIR, folder_name)
    if not os.path.isdir(skill_dir):
        return {"ok": False, "error": f"Skill not found: {skill_name}"}
    for root, dirs, files in os.walk(skill_dir, topdown=False):
        for file_name in files:
            os.remove(os.path.join(root, file_name))
        for dir_name in dirs:
            os.rmdir(os.path.join(root, dir_name))
    os.rmdir(skill_dir)
    return {"ok": True, "skill_dir": skill_dir}


def list_skills() -> dict:
    rows = []
    diagnostics = []
    if not os.path.isdir(SKILLS_DIR):
        return {"ok": True, "count": 0, "skills": rows, "diagnostics": diagnostics}
    for folder_name in sorted(os.listdir(SKILLS_DIR)):
        skill_dir = os.path.join(SKILLS_DIR, folder_name)
        manifest_path = os.path.join(skill_dir, "manifest.json")
        skill_path = os.path.join(skill_dir, SKILL_MARKDOWN_FILE)
        if not os.path.isdir(skill_dir) or (not os.path.exists(manifest_path) and not os.path.exists(skill_path)):
            continue
        manifest = {}
        try:
            if os.path.exists(manifest_path):
                with open(manifest_path, "r", encoding="utf-8") as file:
                    manifest = json.load(file)
        except Exception as error:
            logger.exception("Failed to load managed skill manifest | path=%s | error=%s", manifest_path, error)
            diagnostics.append({"folder_name": folder_name, "severity": "error", "message": str(error), "path": manifest_path})
            continue
        markdown_metadata = _parse_skill_markdown_metadata(skill_path)
        skill_name = str(markdown_metadata.get("name") or manifest.get("name") or folder_name).strip()
        if skill_name != folder_name:
            diagnostics.append(
                {
                    "folder_name": folder_name,
                    "severity": "warning",
                    "message": f"Skill name '{skill_name}' does not match folder '{folder_name}'.",
                    "path": skill_path if os.path.exists(skill_path) else manifest_path,
                }
            )
        tool_names = []
        tools_dir = os.path.join(skill_dir, "tools")
        if os.path.isdir(tools_dir):
            for file_name in sorted(os.listdir(tools_dir)):
                if file_name.endswith(".json"):
                    try:
                        with open(os.path.join(tools_dir, file_name), "r", encoding="utf-8") as file:
                            tool_definition = json.load(file)
                        function_name = str((tool_definition.get("function") or {}).get("name") or "").strip()
                    except Exception:
                        function_name = ""
                    tool_names.append(function_name or os.path.splitext(file_name)[0])
        runtime_path = os.path.join(skill_dir, "runtime.py")
        rows.append(
            {
                "folder_name": folder_name,
                "skill_name": skill_name,
                "enabled": bool(manifest.get("enabled", True)),
                "description": str(markdown_metadata.get("description") or manifest.get("description") or "").strip(),
                "tool_names": tool_names,
                "manifest_path": manifest_path if os.path.exists(manifest_path) else "",
                "skill_path": skill_path if os.path.exists(skill_path) else "",
                "has_skill_md": os.path.exists(skill_path),
                "runtime_path": runtime_path if os.path.exists(runtime_path) else "",
                "runtime_mode": str(manifest.get("runtime_mode") or "").strip(),
                "trusted_runtime": bool(manifest.get("trusted_runtime", False)),
                "allowed_tools": _normalize_string_list(
                    markdown_metadata.get("allowed-tools") or manifest.get("allowed_tools") or []
                ),
                "disable_model_invocation": bool(
                    markdown_metadata.get("disable-model-invocation", manifest.get("disable_model_invocation", False))
                ),
                "user_invocable": bool(
                    markdown_metadata.get("user-invocable", manifest.get("user_invocable", True))
                ),
                "paths": _normalize_string_list(markdown_metadata.get("paths") or manifest.get("paths") or []),
            }
        )
    try:
        try:
            from ..config.resources import load_skill_diagnostics
        except ImportError:
            from DialogueSystem.config.resources import load_skill_diagnostics
        diagnostics.extend(load_skill_diagnostics())
    except Exception:
        logger.exception("Failed to load skill diagnostics")
    return {"ok": True, "count": len(rows), "skills": rows, "diagnostics": diagnostics}
