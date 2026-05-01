import json
import os
from functools import lru_cache


# 当前文件所在目录，作为项目根路径使用。
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
# 配置文件绝对路径（默认读取 config.json）。
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")

# Qdrant 集合默认配置：
# 当 config.json 中未声明对应集合时，使用这里的兜底值。
DEFAULT_QDRANT_COLLECTIONS = {
    "intention": {
        "name": "IntentionSelection_512",
        "vector_size": 512
    },
    "rag": {
        "name": "localEmbeddingTest_512",
        "vector_size": 512
    },
    "memory": {
        "name": "SelenaMemory_512_25_3_31",
        "vector_size": 512
    },
    "web_embedding": {
        "name": "webEmbeddingTest_1024",
        "vector_size": 1024
    }
}

DEFAULT_FRONTEND_CONFIG = {
    "enabled": True,
    "auto_start": True,
    "host": "127.0.0.1",
    "port": 5173,
    "api_port": 8000,
    "package_manager": "pnpm"
}

DEFAULT_MCP_CONFIG = {
    "enabled": False,
    "servers": []
}

DEFAULT_REASONING_EFFORT = "high"
ALLOWED_REASONING_EFFORTS = {"high", "max"}

DEFAULT_MODEL_SELECT_TASK_CONFIG = {}

MODEL_SELECT_LEGACY_ALIASES = {
    "RolePlay": ("DeepLove",),
    "DeepLove": ("RolePlay",),
    "SkillEvolutionEval": ("Agent",),
}


DEFAULT_CHARACTER_CONFIG = {
    "char_name": "助手",
    "user_title": "用户",
    "char_role": "",
    "char_style": "",
    "dialogue_examples": "",
    "response_notes": "",
}


def load_project_config(config_path: str = CONFIG_PATH):
    """从 JSON 文件加载项目配置。

    参数:
        config_path (str): 配置文件路径，默认使用项目根目录下的 config.json。

    返回:
        dict: 解析后的完整配置字典。

    异常:
        FileNotFoundError: 配置文件不存在时抛出。
        json.JSONDecodeError: JSON 格式非法时抛出。
        OSError: 文件读取过程中的其他系统错误。
    """
    with open(os.fspath(config_path), "r", encoding="utf-8") as file:
        return json.load(file)


@lru_cache(maxsize=1)
def get_project_config():
    """获取项目配置（带单例缓存）。

    参数:
        无。

    返回:
        dict: 项目配置字典。

    说明:
        使用 lru_cache(maxsize=1) 缓存首次读取结果，避免重复 I/O。
    """
    return load_project_config()


def reset_project_config_cache():
    """清空项目配置缓存。

    参数:
        无。

    返回:
        None

    说明:
        当外部修改了 config.json 后，可调用此函数强制下次重新读取配置。
    """
    get_project_config.cache_clear()


def save_project_config(config_data: dict, config_path: str = CONFIG_PATH):
    """将项目配置写回 JSON 文件，并刷新进程内缓存。"""
    with open(os.fspath(config_path), "w", encoding="utf-8") as file:
        json.dump(config_data, file, ensure_ascii=False, indent=4)
        file.write("\n")
    reset_project_config_cache()


def get_character_config(config_data: dict = None):
    """获取角色配置，并补齐默认值。"""
    effective_config = config_data or get_project_config()
    configured_character = effective_config.get("Character", {})
    merged = {**DEFAULT_CHARACTER_CONFIG, **configured_character}
    char_name = merged["char_name"]
    user_title = merged["user_title"]
    if "{{CHAR_NAME}}" in merged.get("char_role", ""):
        merged["char_role"] = merged["char_role"].replace("{{CHAR_NAME}}", char_name)
    if "{{USER_TITLE}}" in merged.get("char_role", ""):
        merged["char_role"] = merged["char_role"].replace("{{USER_TITLE}}", user_title)
    return merged


def _render_character_field(value, char_name: str, user_title: str) -> str:
    """将角色配置字段渲染为最终字符串，支持 string 和 list 两种格式。"""
    if isinstance(value, str):
        text = value
    elif isinstance(value, list):
        text = "\n".join(f"- {item}" for item in value)
    else:
        text = str(value) if value else ""
    return text.replace("{{CHAR_NAME}}", char_name).replace("{{USER_TITLE}}", user_title)


def get_character_replacements(config_data: dict = None):
    """返回角色配置中用于模板替换的变量字典。"""
    char_config = get_character_config(config_data)
    char_name = char_config["char_name"]
    user_title = char_config["user_title"]

    return {
        "CHAR_NAME": char_name,
        "USER_TITLE": user_title,
        "CHAR_ROLE": char_config["char_role"],
        "CHAR_STYLE": char_config.get("char_style", ""),
        "DIALOGUE_EXAMPLES": _render_character_field(
            char_config.get("dialogue_examples", ""), char_name, user_title
        ),
        "RESPONSE_NOTES": _render_character_field(
            char_config.get("response_notes", ""), char_name, user_title
        ),
    }


def get_frontend_config(config_data: dict = None):
    """获取前端运行配置，并补齐默认值。"""
    effective_config = config_data or get_project_config()
    configured_frontend = effective_config.get("Frontend", {})
    return {
        **DEFAULT_FRONTEND_CONFIG,
        **configured_frontend,
    }


def get_mcp_config(config_data: dict = None):
    """Return normalized MCP runtime config."""
    effective_config = config_data or get_project_config()
    configured = effective_config.get("MCP", {})
    if not isinstance(configured, dict):
        configured = {}
    merged = {
        **DEFAULT_MCP_CONFIG,
        **configured,
    }
    servers = merged.get("servers", [])
    if not isinstance(servers, list):
        servers = []
    merged["servers"] = servers
    merged["enabled"] = _coerce_config_bool(merged.get("enabled"), False)
    return merged


def _coerce_config_bool(value, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
        return bool(default)
    return bool(value)


def normalize_reasoning_effort(value, default: str = DEFAULT_REASONING_EFFORT) -> str:
    normalized_default = str(default or DEFAULT_REASONING_EFFORT).strip().lower()
    if normalized_default not in ALLOWED_REASONING_EFFORTS:
        normalized_default = DEFAULT_REASONING_EFFORT
    if value is None:
        return normalized_default
    normalized_value = str(value).strip().lower()
    if normalized_value in ALLOWED_REASONING_EFFORTS:
        return normalized_value
    return normalized_default


def _normalize_model_select_entry(entry, defaults: dict):
    normalized = dict(defaults or {})
    if isinstance(entry, str):
        normalized["model"] = entry
    elif isinstance(entry, dict):
        normalized.update(entry)
        normalized["model"] = (
            entry.get("model")
            or entry.get("model_key")
            or entry.get("name")
            or normalized.get("model")
        )
    elif entry is not None:
        normalized["model"] = str(entry)

    normalized["enabled"] = _coerce_config_bool(normalized.get("enabled"), True)
    normalized["thinking"] = _coerce_config_bool(normalized.get("thinking"), False)
    normalized["json_mode"] = _coerce_config_bool(normalized.get("json_mode"), False)
    normalized["reasoning_effort"] = normalize_reasoning_effort(normalized.get("reasoning_effort"))
    normalized["model"] = str(normalized.get("model") or "").strip()
    return normalized


def get_model_select_task_config(task_name: str, config_data: dict = None, fallback_task_names=None):
    """Return normalized ModelSelect task config.

    Preferred format:
    {"enabled": true, "model": "qwen", "thinking": true, "json_mode": true}
    Plain string entries such as "qwen" are still accepted for compatibility.
    """
    effective_config = config_data or get_project_config()
    model_select = effective_config.get("ModelSelect", {})
    if not isinstance(model_select, dict):
        model_select = {}

    normalized_task_name = str(task_name or "").strip()
    defaults = dict(DEFAULT_MODEL_SELECT_TASK_CONFIG.get(normalized_task_name, {}))
    candidate_names = [normalized_task_name]
    if fallback_task_names is None:
        candidate_names.extend(MODEL_SELECT_LEGACY_ALIASES.get(normalized_task_name, ()))
    else:
        candidate_names.extend(fallback_task_names)

    entry = None
    for candidate_name in candidate_names:
        if candidate_name in model_select:
            entry = model_select[candidate_name]
            break

    legacy_postprocess = effective_config.get("TopicPostprocessLLM", {})
    if entry is None and isinstance(legacy_postprocess, dict):
        legacy_entry = legacy_postprocess.get(normalized_task_name)
        if isinstance(legacy_entry, dict):
            entry = legacy_entry
        if legacy_postprocess.get("enabled") is False:
            defaults["enabled"] = False

    return _normalize_model_select_entry(entry, defaults)


def get_model_select_model_key(task_name: str, config_data: dict = None, fallback_task_names=None) -> str:
    task_config = get_model_select_task_config(
        task_name,
        config_data=config_data,
        fallback_task_names=fallback_task_names,
    )
    if not task_config.get("enabled", True):
        return ""
    return task_config.get("model", "")


def iter_model_select_model_keys(config_data: dict = None):
    effective_config = config_data or get_project_config()
    model_select = effective_config.get("ModelSelect", {})
    seen = set()
    if isinstance(model_select, dict):
        for task_name in model_select:
            task_config = get_model_select_task_config(task_name, effective_config, fallback_task_names=())
            model_key = task_config.get("model", "")
            if task_config.get("enabled", True) and model_key and model_key not in seen:
                seen.add(model_key)
                yield model_key

    for task_name in DEFAULT_MODEL_SELECT_TASK_CONFIG:
        task_config = get_model_select_task_config(task_name, effective_config)
        model_key = task_config.get("model", "")
        if task_config.get("enabled", True) and model_key and model_key not in seen:
            seen.add(model_key)
            yield model_key


def _normalize_llm_providers(llm_setting: dict):
    """返回 LLM providers 配置字典。"""
    providers = llm_setting.get("providers")
    if isinstance(providers, dict):
        return providers
    return {}


def _normalize_model_config(model_key: str, provider_name: str, provider_config: dict, model_config):
    """把单个模型配置标准化为统一结构。

    参数:
        model_key (str): 模型别名（如 qwen、kimi_thinking）。
        provider_name (str): 提供商名称（如 qwen、kimi）。
        provider_config (dict): 提供商级配置，通常包含 api_key/base_url/models。
        model_config (str | dict): 模型配置，可为字符串（仅模型名）或字典。

    返回:
        dict: 统一后的模型配置:
            {
                "provider": str,
                "url": str,
                "modelName": str,
                "API": str
            }

    异常:
        TypeError: model_config 既不是 str 也不是 dict。
        KeyError: 缺少 model name / url / api key 任一关键字段。
    """
    if isinstance(model_config, str):
        model_config = {"model": model_config}
    elif not isinstance(model_config, dict):
        raise TypeError(f"Unsupported model config type for {model_key}: {type(model_config)!r}")

    model_name = model_config.get("model") or model_config.get("modelName") or model_config.get("name")
    url = model_config.get("url") or provider_config.get("base_url") or provider_config.get("url")
    api_key = model_config.get("api_key") or provider_config.get("api_key")

    if not model_name:
        raise KeyError(f"LLM model '{model_key}' is missing a model name")
    if not url:
        raise KeyError(f"LLM model '{model_key}' is missing a request url")
    if not api_key:
        raise KeyError(f"LLM model '{model_key}' is missing an api key")

    return {
        "provider": provider_name,
        "url": url,
        "modelName": model_name,
        "API": api_key
    }


def _normalize_model_capabilities(model_config, provider_config: dict | None = None) -> dict:
    """Normalize optional per-model capability flags."""
    provider_config = provider_config or {}
    payload = model_config if isinstance(model_config, dict) else {}
    capabilities = payload.get("capabilities")
    if capabilities is None:
        capabilities = provider_config.get("capabilities")

    supports_vision = False
    if isinstance(capabilities, dict):
        supports_vision = _coerce_config_bool(
            capabilities.get("vision") or capabilities.get("image_input"),
            False,
        )
    elif isinstance(capabilities, (list, tuple, set)):
        normalized_capabilities = {
            str(item or "").strip().lower()
            for item in capabilities
            if str(item or "").strip()
        }
        supports_vision = bool(
            normalized_capabilities
            & {"vision", "image", "images", "image_input", "multimodal"}
        )

    supports_vision = supports_vision or _coerce_config_bool(
        payload.get("supports_vision", payload.get("supports_images")),
        False,
    )
    return {
        "supports_vision": bool(supports_vision),
    }


def build_llm_dict(config_data: dict):
    """从项目配置中构建“模型别名 -> 标准配置”的映射。

    参数:
        config_data (dict): 完整项目配置字典。

    返回:
        dict: LLM 配置映射，键为模型别名，值为标准化后的模型配置。
    """
    llm_setting = config_data.get("LLM_Setting", {})
    llm_configs = {}
    for provider_name, provider_config in _normalize_llm_providers(llm_setting).items():
        models = provider_config.get("models", {})
        for model_key, model_config in models.items():
            llm_configs[model_key] = _normalize_model_config(
                model_key,
                provider_name,
                provider_config,
                model_config
            )
    return llm_configs


def build_llm_capabilities_dict(config_data: dict):
    """Build ``model_key -> capability flags`` for optional runtime features."""
    llm_setting = config_data.get("LLM_Setting", {})
    capability_map = {}
    for _, provider_config in _normalize_llm_providers(llm_setting).items():
        models = provider_config.get("models", {})
        for model_key, model_config in models.items():
            capability_map[model_key] = _normalize_model_capabilities(
                model_config,
                provider_config,
            )
    return capability_map


def get_default_llm_key(config_data: dict = None):
    """获取默认 LLM 模型键名。

    参数:
        config_data (dict | None): 可选配置字典；为空时自动读取缓存配置。

    返回:
        str: 默认模型键名；若未配置则回退为 "qwen"。
    """
    effective_config = config_data or get_project_config()
    return effective_config.get("LLM_Setting", {}).get("default_model", "qwen")


def get_llm_config(model_key: str = None, config_data: dict = None):
    """按模型键名获取标准化后的 LLM 请求配置。

    参数:
        model_key (str | None): 目标模型键名；为空时使用默认模型键名。
        config_data (dict | None): 可选配置字典；为空时自动读取缓存配置。

    返回:
        dict: 指定模型的标准配置，包含 provider/url/modelName/API。

    异常:
        KeyError: model_key 不存在于可用模型映射中。
    """
    effective_config = config_data or get_project_config()
    if isinstance(model_key, dict):
        model_key = model_key.get("model") or model_key.get("model_key") or model_key.get("name")
    resolved_model_key = model_key or get_default_llm_key(effective_config)
    llm_configs = build_llm_dict(effective_config)
    if resolved_model_key not in llm_configs:
        raise KeyError(f"Unknown llm model key: {resolved_model_key}")
    return llm_configs[resolved_model_key]


def get_llm_capabilities(model_key: str = None, config_data: dict = None) -> dict:
    """Return normalized capability flags for one configured model."""
    effective_config = config_data or get_project_config()
    if isinstance(model_key, dict):
        model_key = model_key.get("model") or model_key.get("model_key") or model_key.get("name")
    resolved_model_key = model_key or get_default_llm_key(effective_config)
    capability_map = build_llm_capabilities_dict(effective_config)
    return dict(capability_map.get(resolved_model_key) or {"supports_vision": False})


def get_qdrant_collection_config(collection_key: str, config_data: dict = None):
    """按集合键名获取 Qdrant 集合配置。

    参数:
        collection_key (str): 集合键名（如 intention、rag、memory）。
        config_data (dict | None): 可选配置字典；为空时自动读取缓存配置。

    返回:
        dict: 集合配置，至少包含:
            - name (str): 集合名称
            - vector_size (int): 向量维度

    异常:
        KeyError: collection_key 在默认配置和用户配置中都不存在。
        ValueError: vector_size 无法转换为 int。
    """
    effective_config = config_data or get_project_config()
    configured_collections = effective_config.get("Qdrant_Setting", {}).get("collections", {})
    collections = {
        **DEFAULT_QDRANT_COLLECTIONS,
        **configured_collections
    }
    if collection_key not in collections:
        raise KeyError(f"Unknown qdrant collection key: {collection_key}")

    collection_config = dict(collections[collection_key])
    collection_config["vector_size"] = int(collection_config["vector_size"])
    return collection_config
