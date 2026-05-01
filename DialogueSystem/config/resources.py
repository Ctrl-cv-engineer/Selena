"""统一加载 prompt、工具定义与技能元数据。

对话运行时依赖几类静态资源：
- `MdFile/` 下的 prompt markdown
- `tools/` 下的普通工具定义
- `skills/` 下的技能 manifest 及其附带工具

本模块把这些资源的读取、校验与规整逻辑集中起来，避免主流程文件重复处理。
"""

import copy
import json
import os
import re
from functools import lru_cache


# `summarizeToolResults` 是结束工具规划阶段的“收尾工具”，不应被当成普通意图能力写入
# 意图库；否则召回库容易被训练成“总想尽快结束规划”。
INTENTION_EXCLUDED_TOOL_NAMES = {"summarizeToolResults"}
SKILL_MARKDOWN_FILE = "SKILL.md"
SKILL_RESOURCE_DIRS = ("scripts", "references", "assets")
PROMPT_NAME_ALIASES = {
    "DeepLove": "RolePlay",
    "SummaryMermory": "SummaryMemory",
}

_LAST_SKILL_DIAGNOSTICS = []
_LAST_TOOL_DIAGNOSTICS = []

try:
    from .paths import PROMPTS_DIR, SKILLS_DIR, TOOLS_DIR
except ImportError:
    from DialogueSystem.config.paths import PROMPTS_DIR, SKILLS_DIR, TOOLS_DIR


def invalidate_resource_caches():
    """Clear cached prompt/skill/tool metadata so runtime-generated assets become visible."""
    load_prompt_text.cache_clear()


@lru_cache(maxsize=None)
def load_prompt_text(prompt_name: str):
    """按逻辑名称加载一个 markdown prompt。"""
    candidate_names = []
    normalized_prompt_name = str(prompt_name or "").strip()
    if normalized_prompt_name:
        candidate_names.append(normalized_prompt_name)
    alias_prompt_name = PROMPT_NAME_ALIASES.get(normalized_prompt_name)
    if alias_prompt_name and alias_prompt_name not in candidate_names:
        candidate_names.append(alias_prompt_name)

    for candidate_name in candidate_names:
        filename = f"{candidate_name}Prompt.md"
        prompt_path = os.path.join(PROMPTS_DIR, filename)
        if os.path.exists(prompt_path):
            with open(prompt_path, "r", encoding="utf-8") as file:
                return file.read().lstrip("\ufeff")
        for dirpath, _, filenames in os.walk(PROMPTS_DIR):
            if filename in filenames:
                full_path = os.path.join(dirpath, filename)
                with open(full_path, "r", encoding="utf-8") as file:
                    return file.read().lstrip("\ufeff")

    raise FileNotFoundError(f"Prompt not found for name: {prompt_name}")


def _get_character_replacements():
    """延迟加载角色配置变量，避免循环导入。"""
    try:
        from project_config import get_character_replacements
        return get_character_replacements()
    except Exception:
        return {}


def render_prompt_text(prompt_name: str, replacements: dict = None):
    """加载 prompt 模板，并用 `{{KEY}}` 占位符进行简单替换。

    自动注入角色配置变量（CHAR_NAME, USER_TITLE 等），
    调用方传入的 replacements 优先级高于角色配置。
    """
    prompt_text = load_prompt_text(prompt_name)
    character_vars = _get_character_replacements()
    merged = {**character_vars, **(replacements or {})}
    for key, value in merged.items():
        placeholder = f"{{{{{key}}}}}"
        prompt_text = prompt_text.replace(placeholder, str(value))
    return prompt_text


def _iter_json_files(directory: str):
    """返回目录下按文件名排序的 `.json` 文件列表。"""
    if not os.path.isdir(directory):
        return []
    return [
        os.path.join(directory, filename)
        for filename in sorted(os.listdir(directory))
        if filename.endswith(".json")
    ]


def _iter_relative_files(directory: str, *, root: str):
    if not os.path.isdir(directory):
        return []
    rows = []
    for current_root, _, file_names in os.walk(directory):
        for file_name in sorted(file_names):
            file_path = os.path.join(current_root, file_name)
            rows.append(os.path.relpath(file_path, root).replace("\\", "/"))
    return sorted(rows)


def _load_json_file(file_path: str):
    """读取并解析 JSON 文件，在格式错误时抛出更可读的异常。"""
    with open(file_path, "r", encoding="utf-8") as file:
        try:
            return json.load(file)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {file_path}: {exc}") from exc


def _load_text_file(file_path: str):
    with open(file_path, "r", encoding="utf-8") as file:
        return file.read().lstrip("\ufeff")


def _parse_frontmatter_value(value: str):
    value = str(value or "").strip()
    if not value:
        return ""
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value.startswith("[") and value.endswith("]"):
        try:
            parsed = json.loads(value)
            return parsed
        except Exception:
            return value
    return value


def _parse_skill_markdown(skill_path: str):
    """Parse the Agent Skills SKILL.md frontmatter without requiring PyYAML.

    Supports the agentskills.io spec fields: name, description, license,
    compatibility, metadata (nested key-value map), and allowed-tools
    (space-separated string → list).
    """
    if not os.path.exists(skill_path):
        return {"frontmatter": {}, "body": "", "raw": ""}
    raw_text = _load_text_file(skill_path)
    if not raw_text.startswith("---"):
        return {"frontmatter": {}, "body": raw_text.strip(), "raw": raw_text}

    lines = raw_text.splitlines()
    frontmatter_lines = []
    body_start = 0
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            body_start = index + 1
            break
        frontmatter_lines.append(line)
    else:
        return {"frontmatter": {}, "body": raw_text.strip(), "raw": raw_text}

    frontmatter = {}
    current_key = ""
    current_map_key = ""
    for raw_line in frontmatter_lines:
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent >= 2 and current_map_key:
            nested_match = re.match(r"^\s+([A-Za-z0-9_\-]+)\s*:\s*(.*)$", line)
            if nested_match:
                frontmatter.setdefault(current_map_key, {})
                if isinstance(frontmatter[current_map_key], dict):
                    frontmatter[current_map_key][nested_match.group(1).strip()] = (
                        _parse_frontmatter_value(nested_match.group(2))
                    )
                continue
        list_match = re.match(r"^\s*-\s*(.+)$", line)
        if list_match and current_key:
            if isinstance(frontmatter.get(current_key), dict) and not frontmatter[current_key]:
                frontmatter[current_key] = []
                current_map_key = ""
            frontmatter.setdefault(current_key, [])
            if isinstance(frontmatter[current_key], list):
                frontmatter[current_key].append(_parse_frontmatter_value(list_match.group(1)))
            continue
        key_match = re.match(r"^([A-Za-z0-9_\-]+)\s*:\s*(.*)$", line)
        if not key_match:
            continue
        current_key = key_match.group(1).strip()
        raw_value = key_match.group(2).strip()
        current_map_key = current_key if raw_value == "" else ""
        if current_map_key:
            frontmatter[current_key] = {}
        else:
            frontmatter[current_key] = _parse_frontmatter_value(raw_value)

    if "allowed-tools" in frontmatter and isinstance(frontmatter["allowed-tools"], str):
        frontmatter["allowed-tools"] = frontmatter["allowed-tools"].split()

    body = "\n".join(lines[body_start:]).strip()
    return {"frontmatter": frontmatter, "body": body, "raw": raw_text}


def _register_tool(tool_definitions: list, seen_names: set, tool_definition: dict, source: str):
    """校验工具名并在无重复时注册一条工具定义。"""
    function_name = str(tool_definition.get("function", {}).get("name", "")).strip()
    if not function_name:
        raise ValueError(f"Tool definition in {source} is missing function.name")
    if function_name in seen_names:
        raise ValueError(f"Duplicate tool definition detected: {function_name}")
    seen_names.add(function_name)
    tool_definitions.append(tool_definition)


def _normalize_string_list(values):
    """把输入规整成有序、去重、非空的字符串列表。"""
    normalized_values = []
    seen_values = set()
    for value in values or []:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen_values:
            continue
        seen_values.add(normalized)
        normalized_values.append(normalized)
    return normalized_values


def _normalize_optional_boolean(value, *, default: bool = False) -> bool:
    if value in (None, ""):
        return bool(default)
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    return bool(value)


def _normalize_path_patterns(values):
    if values in (None, ""):
        return []
    if isinstance(values, str):
        values = [values]
    elif not isinstance(values, (list, tuple, set)):
        values = [values]
    normalized_patterns = []
    seen_patterns = set()
    for value in values:
        normalized = str(value or "").strip().replace("\\", "/")
        if normalized.startswith("./"):
            normalized = normalized[2:]
        if not normalized or normalized in seen_patterns:
            continue
        seen_patterns.add(normalized)
        normalized_patterns.append(normalized)
    return normalized_patterns


def _summarize_tool_parameters(tool_definition: dict):
    """为工具参数生成简洁的人类可读摘要。"""
    parameter_summaries = []
    properties = (
        tool_definition.get("function", {})
        .get("parameters", {})
        .get("properties", {})
    )
    for parameter_name, parameter_schema in properties.items():
        schema = parameter_schema or {}
        parameter_type = str(schema.get("type", "")).strip()
        description = str(schema.get("description", "")).strip()
        summary = str(parameter_name).strip()
        if parameter_type:
            summary = f"{summary} ({parameter_type})"
        if description:
            summary = f"{summary}: {description}"
        parameter_summaries.append(summary)
    return parameter_summaries


def _diagnostic(skill_folder: str, message: str, *, severity: str = "warning", path: str = ""):
    return {
        "severity": severity,
        "folder_name": str(skill_folder or "").strip(),
        "path": str(path or "").strip(),
        "message": str(message or "").strip(),
    }


def _normalize_skill_name(value: str, fallback: str):
    normalized = str(value or "").strip()
    return normalized or str(fallback or "").strip()


def _load_skill_records():
    skills = []
    diagnostics = []
    if not os.path.isdir(SKILLS_DIR):
        return skills, diagnostics

    for skill_folder in sorted(os.listdir(SKILLS_DIR)):
        skill_dir = os.path.join(SKILLS_DIR, skill_folder)
        if not os.path.isdir(skill_dir):
            continue

        manifest_path = os.path.join(skill_dir, "manifest.json")
        skill_path = os.path.join(skill_dir, SKILL_MARKDOWN_FILE)
        if not os.path.exists(manifest_path) and not os.path.exists(skill_path):
            continue

        manifest = {}
        if os.path.exists(manifest_path):
            try:
                manifest = _load_json_file(manifest_path)
            except Exception as error:
                diagnostics.append(
                    _diagnostic(skill_folder, f"Failed to load manifest: {error}", severity="error", path=manifest_path)
                )
                continue
        if not manifest.get("enabled", True):
            continue

        parsed_markdown = {"frontmatter": {}, "body": "", "raw": ""}
        if os.path.exists(skill_path):
            try:
                parsed_markdown = _parse_skill_markdown(skill_path)
            except Exception as error:
                diagnostics.append(
                    _diagnostic(skill_folder, f"Failed to parse SKILL.md: {error}", severity="error", path=skill_path)
                )

        frontmatter = dict(parsed_markdown.get("frontmatter") or {})
        skill_name = _normalize_skill_name(
            frontmatter.get("name") or manifest.get("name"),
            skill_folder,
        )
        if skill_name != skill_folder:
            diagnostics.append(
                _diagnostic(
                    skill_folder,
                    f"Skill name '{skill_name}' does not match folder '{skill_folder}'.",
                    path=skill_path if os.path.exists(skill_path) else manifest_path,
                )
            )

        tool_dir = os.path.join(skill_dir, "tools")
        tool_definitions = []
        for tool_path in _iter_json_files(tool_dir):
            try:
                tool_definitions.append(_load_json_file(tool_path))
            except Exception as error:
                diagnostics.append(
                    _diagnostic(skill_folder, f"Failed to load tool definition: {error}", severity="error", path=tool_path)
                )

        description = str(
            frontmatter.get("description")
            or manifest.get("description")
            or ""
        ).strip()
        when_to_use = _normalize_string_list(manifest.get("when_to_use", []))
        frontmatter_when = frontmatter.get("when_to_use") or frontmatter.get("when") or []
        if isinstance(frontmatter_when, str):
            frontmatter_when = [frontmatter_when]
        elif not isinstance(frontmatter_when, (list, tuple)):
            frontmatter_when = [frontmatter_when]
        when_to_use = _normalize_string_list(when_to_use + list(frontmatter_when or []))

        intent_examples = _normalize_string_list(manifest.get("intent_examples", []))

        compatibility = str(
            frontmatter.get("compatibility") or manifest.get("compatibility") or ""
        ).strip()
        license_text = str(
            frontmatter.get("license") or manifest.get("license") or ""
        ).strip()
        skill_metadata = frontmatter.get("metadata") or manifest.get("metadata") or {}
        if not isinstance(skill_metadata, dict):
            skill_metadata = {}
        allowed_tools = frontmatter.get("allowed-tools")
        if allowed_tools in (None, ""):
            allowed_tools = manifest.get("allowed_tools") or []
        if isinstance(allowed_tools, str):
            allowed_tools = allowed_tools.split()
        disable_model_invocation = _normalize_optional_boolean(
            frontmatter.get("disable-model-invocation")
            if "disable-model-invocation" in frontmatter
            else manifest.get("disable_model_invocation"),
            default=False,
        )
        user_invocable = _normalize_optional_boolean(
            frontmatter.get("user-invocable")
            if "user-invocable" in frontmatter
            else manifest.get("user_invocable"),
            default=True,
        )
        path_patterns = frontmatter.get("paths")
        if path_patterns in (None, ""):
            path_patterns = manifest.get("paths") or []
        normalized_allowed_tools = _normalize_string_list(allowed_tools)
        normalized_path_patterns = _normalize_path_patterns(path_patterns)
        governance = {
            "allowed_tools": normalized_allowed_tools,
            "disable_model_invocation": disable_model_invocation,
            "user_invocable": user_invocable,
            "paths": normalized_path_patterns,
            "requires_activation": bool(
                normalized_allowed_tools or disable_model_invocation or normalized_path_patterns
            ),
        }

        resource_files = {
            resource_dir: _iter_relative_files(os.path.join(skill_dir, resource_dir), root=skill_dir)
            for resource_dir in SKILL_RESOURCE_DIRS
        }

        skills.append(
            {
                "name": skill_name,
                "skill_name": skill_name,
                "folder_name": skill_folder,
                "skill_dir": skill_dir,
                "manifest_path": manifest_path if os.path.exists(manifest_path) else "",
                "skill_path": skill_path if os.path.exists(skill_path) else "",
                "has_skill_md": os.path.exists(skill_path),
                "description": description,
                "when_to_use": when_to_use,
                "intent_examples": intent_examples,
                "instructions": str(parsed_markdown.get("body") or "").strip(),
                "frontmatter": frontmatter,
                "compatibility": compatibility,
                "license": license_text,
                "metadata": skill_metadata,
                "allowed_tools": normalized_allowed_tools,
                "disable_model_invocation": disable_model_invocation,
                "user_invocable": user_invocable,
                "paths": normalized_path_patterns,
                "governance": governance,
                "resources": resource_files,
                "tool_names": [
                    str(item.get("function", {}).get("name", "")).strip()
                    for item in tool_definitions
                    if str(item.get("function", {}).get("name", "")).strip()
                ],
                "tools": tool_definitions,
                "runtime_mode": str(manifest.get("runtime_mode") or "").strip(),
                "trusted_runtime": bool(manifest.get("trusted_runtime", False)),
            }
        )
    return skills, diagnostics


def load_skill_definitions():
    """加载所有已启用技能，包括无工具的 instruction-only Agent Skills。"""
    global _LAST_SKILL_DIAGNOSTICS
    skills, diagnostics = _load_skill_records()
    _LAST_SKILL_DIAGNOSTICS = diagnostics
    return skills


def load_skill_diagnostics():
    """Return skill/tool discovery diagnostics without failing the whole runtime."""
    skills, diagnostics = _load_skill_records()
    _ = skills
    return list(diagnostics) + list(_LAST_TOOL_DIAGNOSTICS)


def _skill_matches_name(skill: dict, skill_name: str):
    normalized = str(skill_name or "").strip().lower().replace("_", "-")
    if not normalized:
        return False
    candidates = set()
    for key in ("name", "skill_name", "folder_name"):
        value = str(skill.get(key) or "").strip().lower()
        if value:
            candidates.add(value)
            candidates.add(value.replace("_", "-"))
            candidates.add(value.replace("-", "_"))
    return normalized in candidates


def build_skill_activation_payload(skill_name: str):
    """Load detailed instructions/resources for one skill on demand."""
    normalized_skill_name = str(skill_name or "").strip()
    if not normalized_skill_name:
        return {"ok": False, "error": "SkillName is required."}
    skills = load_skill_definitions()
    matched = next((skill for skill in skills if _skill_matches_name(skill, normalized_skill_name)), None)
    if matched is None:
        return {
            "ok": False,
            "error": f"Skill not found: {normalized_skill_name}",
            "available_skills": [skill["name"] for skill in skills],
        }

    instructions = str(matched.get("instructions") or "").strip()
    if not instructions:
        lines = []
        if matched.get("description"):
            lines.append(str(matched["description"]))
        for item in matched.get("when_to_use", []) or []:
            lines.append(f"- Use when: {item}")
        if matched.get("tool_names"):
            lines.append(f"- Registered tools: {', '.join(matched['tool_names'])}")
        instructions = "\n".join(lines).strip()

    return {
        "ok": True,
        "skill": {
            "name": matched.get("name", ""),
            "folder_name": matched.get("folder_name", ""),
            "description": matched.get("description", ""),
            "when_to_use": list(matched.get("when_to_use", []) or []),
            "intent_examples": list(matched.get("intent_examples", []) or []),
            "tool_names": list(matched.get("tool_names", []) or []),
            "skill_path": matched.get("skill_path", ""),
            "manifest_path": matched.get("manifest_path", ""),
            "governance": dict(matched.get("governance") or {}),
            "resources": dict(matched.get("resources") or {}),
        },
        "instructions": instructions,
        "tools": [
            {
                "name": str((tool.get("function") or {}).get("name", "")),
                "description": str((tool.get("function") or {}).get("description", "")),
                "parameters": (tool.get("function") or {}).get("parameters", {}),
            }
            for tool in matched.get("tools", []) or []
        ],
        "note": "Use these instructions as task context. Resource files are listed but not automatically loaded.",
    }


def build_agent_skill_prompt(skills=None):
    """生成一段运行时提示词，让 Agent 知道当前有哪些技能可用。"""
    skills = list(skills) if skills is not None else load_skill_definitions()
    if not skills:
        return ""

    lines = []
    for skill in skills:
        governance = dict(skill.get("governance") or {})
        description = skill["description"] or "No description provided."
        lines.append(f"- {skill['name']}: {description}")
        if skill["tool_names"]:
            lines.append(f"  Registered tools: {', '.join(skill['tool_names'])}")
        if skill.get("has_skill_md") or skill.get("instructions"):
            lines.append("  Detailed instructions: call activateSkill with this skill name before using it for a complex task.")
        if governance.get("paths"):
            lines.append(f"  Auto-activation paths: {', '.join(governance['paths'])}")
        if governance.get("allowed_tools"):
            lines.append(f"  Pre-approved tools when activated: {', '.join(governance['allowed_tools'])}")
        for instruction in skill["when_to_use"]:
            lines.append(f"  Use it when: {instruction}")
    return render_prompt_text(
        "AgentSkillMap",
        {"SKILL_LINES": "\n".join(lines)},
    )


def _build_tool_intention_ability(tool_definition: dict, *, source: str, skill_context: dict = None):
    """把工具定义转换成可写入意图库的能力描述。"""
    function_definition = tool_definition.get("function", {})
    tool_name = str(function_definition.get("name", "")).strip()
    if not tool_name or tool_name in INTENTION_EXCLUDED_TOOL_NAMES:
        return None

    skill_context = skill_context or {}
    description = str(function_definition.get("description", "")).strip() or str(
        skill_context.get("description", "")
    ).strip()
    when_to_use = _normalize_string_list(skill_context.get("when_to_use", []))

    return {
        "ability_key": f"tool:{tool_name}",
        "ability_type": "tool",
        "name": tool_name,
        "display_name": tool_name,
        "description": description,
        "when_to_use": when_to_use,
        "manual_examples": [],
        "parameter_summaries": _summarize_tool_parameters(tool_definition),
        "registered_tools": [tool_name],
        "source": source,
        "minimum_examples": 6
    }


def load_intention_ability_definitions():
    """汇总所有应写入意图召回库的技能/工具能力定义。"""
    abilities = []
    seen_ability_keys = set()

    def register_ability(ability):
        if ability is None:
            return
        ability_key = str(ability.get("ability_key", "")).strip()
        if not ability_key or ability_key in seen_ability_keys:
            return
        seen_ability_keys.add(ability_key)
        abilities.append(ability)

    for file_path in _iter_json_files(TOOLS_DIR):
        tool_definition = _load_json_file(file_path)
        ability = _build_tool_intention_ability(tool_definition, source=file_path)
        register_ability(ability)

    for skill in load_skill_definitions():
        if bool((skill.get("governance") or {}).get("disable_model_invocation", False)):
            continue
        register_ability(
            {
                "ability_key": f"skill:{skill['name']}",
                "ability_type": "skill",
                "name": skill["name"],
                "display_name": skill["name"],
                "description": skill["description"],
                "when_to_use": _normalize_string_list(skill["when_to_use"]),
                "manual_examples": _normalize_string_list(skill.get("intent_examples", [])),
                "parameter_summaries": [],
                "registered_tools": _normalize_string_list(skill["tool_names"]),
                "source": f"skill:{skill['name']}",
                "minimum_examples": 8
            }
        )

        for tool_definition in skill["tools"]:
            ability = _build_tool_intention_ability(
                tool_definition,
                source=f"skill:{skill['name']}",
                skill_context=skill
            )
            register_ability(ability)

    return abilities


def load_tool_definitions():
    """加载 Agent 运行时最终可见的有序工具定义列表。"""
    global _LAST_TOOL_DIAGNOSTICS
    tool_definitions = []
    seen_names = set()
    diagnostics = []

    for file_path in _iter_json_files(TOOLS_DIR):
        try:
            tool_definition = _load_json_file(file_path)
            _register_tool(tool_definitions, seen_names, tool_definition, file_path)
        except Exception as error:
            diagnostics.append(_diagnostic("core_tools", str(error), severity="error", path=file_path))

    for skill in load_skill_definitions():
        for tool_definition in skill["tools"]:
            tool_name = tool_definition.get("function", {}).get("name", "unknown")
            source = f"skill:{skill['name']}:{tool_name}"
            try:
                governed_tool_definition = copy.deepcopy(tool_definition)
                governed_tool_definition["x-dialogue-skill"] = {
                    "name": skill["name"],
                    "description": skill.get("description", ""),
                    "allowed_tools": list(skill.get("allowed_tools", []) or []),
                    "disable_model_invocation": bool(skill.get("disable_model_invocation", False)),
                    "user_invocable": bool(skill.get("user_invocable", True)),
                    "paths": list(skill.get("paths", []) or []),
                    "requires_activation": bool(
                        (skill.get("governance") or {}).get("requires_activation", False)
                    ),
                }
                _register_tool(tool_definitions, seen_names, governed_tool_definition, source)
            except Exception as error:
                diagnostics.append(
                    _diagnostic(skill.get("folder_name", skill["name"]), str(error), severity="error", path=source)
                )

    _LAST_TOOL_DIAGNOSTICS = diagnostics
    return tool_definitions
