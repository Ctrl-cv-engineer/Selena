import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  Bot,
  CheckCircle2,
  Clock,
  Code2,
  Cpu,
  Database,
  Eye,
  EyeOff,
  FileJson,
  GitBranch,
  KeyRound,
  Layers,
  Network,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  UserCircle,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import clsx from "clsx";

import { api } from "@/services/api";
import type { AppConfig, JsonValue } from "@/types";

type JsonObject = { [key: string]: JsonValue };
type ConfigValueType = "string" | "number" | "boolean" | "array" | "object" | "null";

interface ConfigItem {
  key: string;
  name: string;
  path: string;
  rootKey: string;
  value: JsonValue;
  valueType: ConfigValueType;
  description: string;
  sensitive: boolean;
}

interface SectionDefinition {
  label: string;
  summary: string;
  icon: LucideIcon;
  accent: "blue" | "emerald" | "amber" | "violet" | "rose" | "cyan" | "slate";
}

interface NoticeState {
  type: "success" | "warning" | "error" | "info";
  text: string;
}

const ALL_SECTION = "__all__";

const SECTION_ORDER = [
  "Character",
  "LLM_Setting",
  "Embedding_Setting",
  "Rerank_Setting",
  "ModelSelect",
  "SkillEvolution",
  "AgentRuntime",
  "IntentRouter",
  "ContextMemory",
  "AgentRetrievalCache",
  "Summary",
  "SilentTime",
  "Qdrant_Setting",
  "Frontend",
  "MCP",
  "Security",
  "ExecutionBackends",
  "SubAgentPolicy",
  "AutonomousTaskMode",
  "VectorSetting",
  "MemoryRecall",
];

const SECTION_DEFINITIONS: Record<string, SectionDefinition> = {
  Character: {
    label: "角色设定",
    summary: "角色名称、身份描述、对话示例和回复注意事项。",
    icon: UserCircle,
    accent: "rose",
  },
  LLM_Setting: {
    label: "LLM 供应商",
    summary: "模型供应商、API 地址、密钥和模型别名映射。",
    icon: Bot,
    accent: "blue",
  },
  Embedding_Setting: {
    label: "Embedding",
    summary: "向量化模型、接口地址和鉴权密钥。",
    icon: Layers,
    accent: "emerald",
  },
  Rerank_Setting: {
    label: "Rerank",
    summary: "召回后二次排序模型和接口配置。",
    icon: SlidersHorizontal,
    accent: "cyan",
  },
  ModelSelect: {
    label: "模型路由",
    summary: "不同对话、总结、意图判断任务使用的模型。",
    icon: GitBranch,
    accent: "violet",
  },
  SkillEvolution: {
    label: "技能演化",
    summary: "自动沉淀工具流程为 skill 的触发条件。",
    icon: Settings2,
    accent: "amber",
  },
  AgentRuntime: {
    label: "Agent 运行",
    summary: "单轮 Agent 工具调用上限和循环保护。",
    icon: Cpu,
    accent: "blue",
  },
  IntentRouter: {
    label: "意图路由",
    summary: "判断用户消息是否进入工具调用链路。",
    icon: GitBranch,
    accent: "emerald",
  },
  ContextMemory: {
    label: "关键记忆",
    summary: "始终可见的核心记忆层更新与裁剪规则。",
    icon: Layers,
    accent: "cyan",
  },
  AgentRetrievalCache: {
    label: "检索缓存",
    summary: "追问时复用最近检索型工具结果的缓存策略。",
    icon: Database,
    accent: "amber",
  },
  Summary: {
    label: "上下文摘要",
    summary: "对话上下文过长时的摘要和保留窗口。",
    icon: FileJson,
    accent: "slate",
  },
  SilentTime: {
    label: "静默跟进",
    summary: "用户长时间静默后的跟进触发时间。",
    icon: Clock,
    accent: "rose",
  },
  Qdrant_Setting: {
    label: "Qdrant",
    summary: "向量数据库连接和 collection 映射。",
    icon: Database,
    accent: "emerald",
  },
  Frontend: {
    label: "前端服务",
    summary: "前端与本地 API 的监听地址、端口和启动方式。",
    icon: SlidersHorizontal,
    accent: "blue",
  },
  MCP: {
    label: "MCP",
    summary: "动态 MCP 工具服务器列表和鉴权信息。",
    icon: Network,
    accent: "cyan",
  },
  Security: {
    label: "安全策略",
    summary: "工具集白名单、审批模式、文件访问根目录。",
    icon: ShieldCheck,
    accent: "rose",
  },
  ExecutionBackends: {
    label: "执行后端",
    summary: "终端工具默认使用隔离后端还是本地后端。",
    icon: Code2,
    accent: "slate",
  },
  SubAgentPolicy: {
    label: "SubAgent",
    summary: "委派任务的深度、并发、缓存和资源配额。",
    icon: Bot,
    accent: "violet",
  },
  AutonomousTaskMode: {
    label: "自主任务",
    summary: "空闲时自动规划、执行和分享后台任务的策略。",
    icon: Cpu,
    accent: "amber",
  },
  VectorSetting: {
    label: "向量记忆",
    summary: "长期记忆检索、去重、TTL 和 rerank 参数。",
    icon: SlidersHorizontal,
    accent: "emerald",
  },
  MemoryRecall: {
    label: "记忆召回",
    summary: "原子记忆进入上下文前的最低重排分数。",
    icon: Layers,
    accent: "cyan",
  },
};

const ACCENT_CLASSES: Record<SectionDefinition["accent"], { active: string; icon: string; marker: string }> = {
  blue: {
    active: "border-blue-200 bg-blue-50 text-blue-800",
    icon: "bg-blue-100 text-blue-700",
    marker: "bg-blue-500",
  },
  emerald: {
    active: "border-emerald-200 bg-emerald-50 text-emerald-800",
    icon: "bg-emerald-100 text-emerald-700",
    marker: "bg-emerald-500",
  },
  amber: {
    active: "border-amber-200 bg-amber-50 text-amber-800",
    icon: "bg-amber-100 text-amber-700",
    marker: "bg-amber-500",
  },
  violet: {
    active: "border-violet-200 bg-violet-50 text-violet-800",
    icon: "bg-violet-100 text-violet-700",
    marker: "bg-violet-500",
  },
  rose: {
    active: "border-rose-200 bg-rose-50 text-rose-800",
    icon: "bg-rose-100 text-rose-700",
    marker: "bg-rose-500",
  },
  cyan: {
    active: "border-cyan-200 bg-cyan-50 text-cyan-800",
    icon: "bg-cyan-100 text-cyan-700",
    marker: "bg-cyan-500",
  },
  slate: {
    active: "border-slate-300 bg-slate-100 text-slate-800",
    icon: "bg-slate-100 text-slate-700",
    marker: "bg-slate-500",
  },
};

const FIELD_DESCRIPTIONS: Record<string, string> = {
  "Character.char_name": "角色名称，用于模板中的 {{CHAR_NAME}} 替换。",
  "Character.user_title": "角色对用户的称呼，用于模板中的 {{USER_TITLE}} 替换。",
  "Character.char_role": "角色身份描述；支持 {{CHAR_NAME}} 和 {{USER_TITLE}} 占位符。",
  "Character.char_style": "角色表达风格的简短描述。",
  "Character.dialogue_examples": "对话示例与示例解析；用于 prompt 中的参考示例部分。",
  "Character.response_notes": "回复注意事项；用于 prompt 中的注意事项部分。",
  "LLM_Setting.default_model": "默认模型别名；当任务没有显式指定模型时作为兜底。",
  "Embedding_Setting.qwen_embedding_modelName": "生成向量时使用的 embedding 模型名称。",
  "Embedding_Setting.qwen_embedding_url": "embedding 请求的接口地址。",
  "Embedding_Setting.qwen_key": "embedding 接口鉴权密钥。",
  "Rerank_Setting.qwen_rerank_modelName": "向量召回后二次排序使用的 rerank 模型名称。",
  "Rerank_Setting.qwen_rerank_url": "rerank 请求的接口地址。",
  "Rerank_Setting.qwen_rerank_key": "rerank 接口鉴权密钥。",
  "SkillEvolution.enabled": "是否启用自动技能演化。",
  "SkillEvolution.min_tool_calls": "一轮流程至少调用多少次工具后，才进入 skill 沉淀评估。",
  "SkillEvolution.similarity_threshold": "候选 skill 与已有 skill 的相似度阈值，用于避免重复创建。",
  "AgentRuntime.max_tool_calls": "主 Agent 单轮工作流允许执行的普通工具调用总上限。",
  "AgentRuntime.max_consecutive_same_tool_calls": "同一个工具连续调用达到该次数后进入整理收尾，避免循环。",
  "IntentRouter.method": "意图路由模式；llm 为直接模型判断，vector 为向量意图库加灰区复核。",
  "IntentRouter.enabled": "是否启用混合意图路由开关。",
  "IntentRouter.high_confidence_threshold": "向量召回高置信阈值，高于该值直接进入 Agent。",
  "IntentRouter.low_confidence_threshold": "向量召回低置信阈值，低于该值直接走普通回复或 RAG。",
  "IntentRouter.candidate_limit": "送给灰区复核模型的候选能力数量上限。",
  "ContextMemory.enabled": "是否维护始终可见的关键记忆层。",
  "ContextMemory.update_trigger": "关键记忆刷新时机。",
  "ContextMemory.update_min_new_messages": "间隔刷新模式下，累计多少条新消息后允许更新关键记忆。",
  "ContextMemory.update_on_empty": "关键记忆为空时是否允许自动生成第一版。",
  "ContextMemory.max_chars": "关键记忆渲染进 prompt 时的总字符上限。",
  "ContextMemory.max_items_total": "关键记忆条目总数上限。",
  "ContextMemory.max_items_per_section": "单个 section 默认最多保留的关键记忆条数。",
  "ContextMemory.max_item_chars": "单条关键记忆的字符上限。",
  "ContextMemory.recent_message_limit": "更新关键记忆时最多回看多少条最近消息。",
  "AgentRetrievalCache.enabled": "是否启用 Agent 检索缓存。",
  "AgentRetrievalCache.match_model": "判断当前问题能否复用旧检索结果时使用的模型别名。",
  "AgentRetrievalCache.match_thinking": "缓存匹配判断阶段是否开启 thinking。",
  "AgentRetrievalCache.match_json_mode": "缓存匹配判断阶段是否要求 JSON 输出。",
  "AgentRetrievalCache.match_reasoning_effort": "缓存匹配判断阶段的推理强度。",
  "AgentRetrievalCache.max_cache_inject_chars": "命中缓存后最多注入当前上下文的检索文本字符数。",
  "AgentRetrievalCache.cacheable_tools": "允许写入检索缓存的工具白名单。",
  "Summary.Max_context": "live context 超过多少条消息后触发上下文摘要。",
  "Summary.Summary_context": "摘要后保留原始消息的裁剪位置。",
  "Summary.Agent_Max": "Agent 链路中可保留的上下文消息上限。",
  "SilentTime.First": "用户静默多久后触发第一阶段跟进，单位为秒。",
  "SilentTime.Last": "用户静默多久后触发最终阶段跟进，单位为秒。",
  "Qdrant_Setting.host": "Qdrant HTTP 服务主机地址。",
  "Qdrant_Setting.port": "Qdrant HTTP API 端口。",
  "Qdrant_Setting.grpc_port": "Qdrant gRPC 端口。",
  "Qdrant_Setting.prefer_grpc": "是否优先通过 gRPC 访问 Qdrant。",
  "Qdrant_Setting.local_data_path": "本地方式运行 Qdrant 时的数据目录。",
  "Qdrant_Setting.docker_data_path": "Docker 方式运行 Qdrant 时映射的数据目录。",
  "Frontend.enabled": "是否启用内置前端。",
  "Frontend.auto_start": "启动主程序时是否自动拉起前端服务。",
  "Frontend.host": "前端页面和本地 API 绑定的监听地址。",
  "Frontend.port": "浏览器访问前端页面时使用的端口。",
  "Frontend.api_port": "本地前端 API 端口。",
  "Frontend.package_manager": "自动启动前端时使用的包管理器命令。",
  "MCP.enabled": "是否启用 MCP 动态工具发现能力。",
  "MCP.servers": "MCP 服务器列表；每项包含名称、启用状态、URL 和可选 token。",
  "Security.is_admin": "当前会话是否默认具备管理员级工具能力。",
  "Security.approval_mode": "高风险工具的审批模式。",
  "Security.allow_local_terminal": "是否允许终端工具直接使用本机本地执行后端。",
  "Security.enabled_toolsets": "启用的工具集白名单。",
  "Security.approved_tools": "已经被用户永久批准的工具名列表。",
  "Security.file_roots": "文件读写类工具允许访问的根目录列表。",
  "ExecutionBackends.terminal.default_backend": "终端类工具默认执行后端；isolated 为隔离执行，local 为本地执行。",
  "SubAgentPolicy.max_depth": "SubAgent 递归创建 SubAgent 的最大深度。",
  "SubAgentPolicy.allow_admin_tools": "是否允许 SubAgent 使用管理员级工具。",
  "SubAgentPolicy.max_concurrent_tasks": "同时运行的委派任务最大数量。",
  "SubAgentPolicy.max_queue_size": "等待队列上限。",
  "SubAgentPolicy.default_priority": "委派任务默认优先级，数值越大通常越优先。",
  "SubAgentPolicy.result_cache_enabled": "是否启用委派结果缓存。",
  "SubAgentPolicy.result_cache_ttl_seconds": "委派结果缓存 TTL，单位为秒。",
  "SubAgentPolicy.result_cache_max_entries": "委派结果缓存最多保留多少条。",
  "SubAgentPolicy.toolsets": "SubAgent 全局工具集覆盖；null 表示沿用各类型默认配置。",
  "AutonomousTaskMode.enabled": "是否启用自主任务模式。",
  "AutonomousTaskMode.idle_threshold_seconds": "距离上次用户交互超过多少秒后允许进入自主模式。",
  "AutonomousTaskMode.max_daily_tasks": "单日最多执行多少个自主任务，包含结转任务。",
  "AutonomousTaskMode.max_task_attempts": "同一任务最多允许创建多少次执行 attempt。",
  "AutonomousTaskMode.max_daily_interrupts": "一天内最多允许被用户打断多少次。",
  "AutonomousTaskMode.cancel_wait_seconds": "请求取消后台任务后最多等待多久确认其进入终态。",
  "AutonomousTaskMode.stale_attempt_timeout_seconds": "attempt 多久没有心跳或终态更新后会被视为 stale。",
  "AutonomousTaskMode.summary_on_complete": "当天任务全部完成或预算耗尽后，是否自动生成自主经历总结。",
  "AutonomousTaskMode.token_limits.max_input_tokens_per_session": "单日自主会话输入 token 总上限。",
  "AutonomousTaskMode.token_limits.max_output_tokens_per_session": "单日自主会话输出 token 总上限。",
  "AutonomousTaskMode.token_limits.max_input_tokens_per_task": "单个自主任务输入 token 上限。",
  "AutonomousTaskMode.token_limits.max_output_tokens_per_task": "单个自主任务输出 token 上限。",
  "AutonomousTaskMode.task_execution.agent_type": "自主任务执行阶段使用的 agent 类型。",
  "AutonomousTaskMode.task_execution.max_tool_calls": "单个自主任务执行期允许调用的工具次数上限。",
  "AutonomousTaskMode.task_execution.timeout_seconds": "单个自主任务从发起到等待结束的总超时时长。",
  "AutonomousTaskMode.sharing.min_score": "自主经历进入候选注入池的最低分享分。",
  "AutonomousTaskMode.sharing.cooldown_days": "某条自主经历被提到后再次注入前需要冷却的天数。",
  "AutonomousTaskMode.sharing.max_inject_count": "单轮对话最多允许注入多少条自主经历。",
  "AutonomousTaskMode.sharing.max_recent_experiences": "近期经历与感受 system prompt 最多保留多少条。",
  "AutonomousTaskMode.sharing.recent_experience_lifetime_days": "近期经历与感受中单条内容最多保留多少天，超过后释放位置给排队内容。",
  "AutonomousTaskMode.sharing.mention_detection.embedding_threshold": "用向量相似度判断回复已经提及该经历时的阈值。",
  "AutonomousTaskMode.sharing.mention_detection.keyword_min_hits": "用关键词重叠判断已经提及时的最少命中数。",
  "VectorSetting.Top_k": "向量检索的初始召回条数。",
  "VectorSetting.Importance_Threshold": "高重要度保护阈值。",
  "VectorSetting.Max_SearchScore": "SearchScore 上限，避免反复命中无限膨胀。",
  "VectorSetting.Temperature_Weight.hot": "hot 记忆的温度权重。",
  "VectorSetting.Temperature_Weight.warm": "warm 记忆的温度权重。",
  "VectorSetting.Temperature_Weight.cold": "cold 记忆的温度权重。",
  "VectorSetting.Rerank_Scale": "检索重排公式的统一缩放系数。",
  "VectorSetting.Cold_SearchScore_Multiplier": "cold 状态记忆的 SearchScore 增益倍数。",
  "VectorSetting.Other_SearchScore_Multiplier": "非 cold 记忆在中间区间的 SearchScore 增益倍数。",
  "VectorSetting.Duplicate_Vector_Threshold": "向量去重阈值。",
  "VectorSetting.Upgrade_Score_Threshold": "TTL 到期后记忆被升级续命的 SearchScore 阈值。",
  "VectorSetting.Downgrade_Score_Threshold": "TTL 到期后记忆被降级的 SearchScore 阈值。",
  "VectorSetting.Upgrade_TTL_Multiplier": "记忆升级后新 TTL 相对于默认 TTL 的倍数。",
  "VectorSetting.Downgrade_TTL_Multiplier": "记忆降级后新 TTL 相对于默认 TTL 的倍数。",
  "VectorSetting.Default_TTL_Days": "新增记忆默认 TTL 天数。",
  "VectorSetting.Rank_Score": "rerank 后最终保留候选结果的基础阈值。",
  "VectorSetting.Rerank_Top_k": "初筛之后送入 rerank 的候选条数。",
  "MemoryRecall.rerank_min_score": "原子记忆进入最终上下文前的最低 rerank 分数。",
};

const MODEL_TASK_DESCRIPTIONS: Record<string, string> = {
  Agent: "Agent 主流程，包括规划、调用工具、整理结果和最终产出。",
  Simple: "Simple 风格回复链路。",
  RolePlay: "RolePlay 人设回复，以及部分面向最终用户的 RAG 风格答案。",
  LiteraryCreation: "偏创作、扩写和长文本表达的链路。",
  SummaryAndMermory: "历史总结、记忆提炼等后台摘要型任务。",
  topic_same: "判断当前消息是否仍属于同一话题。",
  topic_archive_summary: "旧话题归档前生成摘要。",
  context_summary: "当前 live context 太长时压缩上下文。",
  SilenceFollowUpPrompt: "用户静默较久后生成跟进文案。",
  core_memory_update: "更新 ContextMemory 关键记忆 state。",
  LLMIntentRouter: "当 IntentRouter.method 为 llm 时判断最后一条消息是否需要工具调用。",
  SkillEvolutionEval: "评估一轮工具流程是否值得沉淀成新 skill。",
  AgentTestJudge: "Agent 自动化测试时对结果做 LLM 判卷。",
};

const MODEL_FIELD_DESCRIPTIONS: Record<string, string> = {
  enabled: "是否启用该子任务链路。",
  model: "引用 LLM_Setting.providers.*.models 中定义的模型别名。",
  thinking: "是否向供应商请求 reasoning / thinking 能力。",
  json_mode: "是否要求模型按 JSON object 输出。",
  reasoning_effort: "thinking 模式下的推理强度。",
};

function isJsonObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function cloneConfig<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function formatConfig(value: unknown) {
  return JSON.stringify(value, null, 4);
}

function getValueType(value: JsonValue): ConfigValueType {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  if (isJsonObject(value)) return "object";
  return typeof value as ConfigValueType;
}

function isSensitivePath(path: string) {
  const leaf = path.split(".").pop()?.toLowerCase() ?? "";
  return (
    leaf.includes("api_key") ||
    leaf.endsWith("_key") ||
    leaf === "qwen_key" ||
    leaf.includes("auth_token") ||
    leaf.includes("secret") ||
    leaf.includes("password")
  );
}

function getSectionDefinition(rootKey: string): SectionDefinition {
  return (
    SECTION_DEFINITIONS[rootKey] ?? {
      label: rootKey,
      summary: "config.json 中的自定义配置区。",
      icon: Settings2,
      accent: "slate",
    }
  );
}

function getFieldDescription(path: string) {
  const exact = FIELD_DESCRIPTIONS[path];
  if (exact) return exact;

  const parts = path.split(".");
  if (parts[0] === "LLM_Setting" && parts[1] === "providers") {
    const provider = parts[2] ?? "provider";
    if (parts[3] === "api_key") return `${provider} 供应商的正式 API Key。`;
    if (parts[3] === "free_api_key") return `${provider} 兼容链路中的备用免费 Key。`;
    if (parts[3] === "base_url") return `${provider} 供应商默认的 OpenAI 兼容聊天接口地址。`;
    if (parts[3] === "models" && parts.length === 5) {
      return `模型别名 ${parts[4]} 对应的真实模型 ID。`;
    }
    if (parts[3] === "models" && parts.length === 6 && parts[5] === "model") {
      return `模型别名 ${parts[4]} 最终请求的真实模型 ID。`;
    }
    if (parts[3] === "models" && parts.length === 6 && parts[5] === "url") {
      return `模型别名 ${parts[4]} 专用接口地址；不填时沿用 provider base_url。`;
    }
  }

  if (parts[0] === "ModelSelect" && parts.length === 3) {
    const task = parts[1];
    const field = parts[2];
    return `${MODEL_TASK_DESCRIPTIONS[task] ?? "该子任务的模型调用配置"}；${MODEL_FIELD_DESCRIPTIONS[field] ?? "模型配置字段。"}`;
  }

  if (parts[0] === "IntentRouter" && parts[1] === "llm_fallback" && parts.length === 3) {
    return `意图路由灰区复核阶段的模型配置；${MODEL_FIELD_DESCRIPTIONS[parts[2]] ?? "模型配置字段。"}`;
  }

  if (parts[0] === "AutonomousTaskMode" && ["task_planning", "task_execution", "sharing_score"].includes(parts[1] ?? "") && parts.length === 3) {
    const phaseLabel =
      parts[1] === "task_planning"
        ? "自主任务规划阶段"
        : parts[1] === "task_execution"
          ? "自主任务执行阶段"
          : "自主经历分享评分阶段";
    return `${phaseLabel}的模型配置；${MODEL_FIELD_DESCRIPTIONS[parts[2]] ?? "执行参数。"}`;
  }

  if (parts[0] === "Qdrant_Setting" && parts[1] === "collections") {
    const collection = parts[2] ?? "collection";
    if (parts[3] === "name") return `${collection} collection 在 Qdrant 中的实际名称。`;
    if (parts[3] === "test") return `${collection} collection 的测试或备用集合名。`;
    if (parts[3] === "vector_size") return `${collection} collection 预期使用的向量维度。`;
  }

  if (parts[0] === "SubAgentPolicy" && parts[1] === "agent_type_configs") {
    const typeName = parts[2] ?? "agent";
    if (parts[3] === "toolsets") return `${typeName} 类型 SubAgent 默认允许使用的工具集白名单。`;
    if (parts[3] === "max_tool_calls") return `${typeName} 类型 SubAgent 单任务允许的默认工具调用次数。`;
    if (parts[3] === "resource_limits" && parts[4] === "max_file_reads") return `${typeName} 类型单任务最大文件读取次数。`;
    if (parts[3] === "resource_limits" && parts[4] === "max_file_writes") return `${typeName} 类型单任务最大文件写入次数。`;
    if (parts[3] === "resource_limits" && parts[4] === "max_network_calls") return `${typeName} 类型单任务最大网络请求次数。`;
  }

  return "当前 JSON 路径的配置值。";
}

function flattenConfig(value: JsonValue, prefix: string[] = []): ConfigItem[] {
  if (isJsonObject(value) && Object.keys(value).length > 0) {
    return Object.entries(value).flatMap(([key, childValue]) => flattenConfig(childValue, [...prefix, key]));
  }

  const path = prefix.join(".");
  const name = prefix[prefix.length - 1] ?? path;
  return [
    {
      key: path,
      name,
      path,
      rootKey: prefix[0] ?? "",
      value,
      valueType: getValueType(value),
      description: getFieldDescription(path),
      sensitive: isSensitivePath(path),
    },
  ];
}

function setValueAtPath(config: AppConfig, path: string, value: JsonValue): AppConfig {
  const keys = path.split(".");
  const next = cloneConfig(config);
  let current: JsonObject = next;

  keys.slice(0, -1).forEach((key) => {
    if (!isJsonObject(current[key])) {
      current[key] = {};
    }
    current = current[key] as JsonObject;
  });

  current[keys[keys.length - 1]] = value;
  return next;
}

function sortSectionKeys(keys: string[]) {
  const order = new Map(SECTION_ORDER.map((key, index) => [key, index]));
  return [...keys].sort((left, right) => {
    const leftIndex = order.get(left) ?? Number.MAX_SAFE_INTEGER;
    const rightIndex = order.get(right) ?? Number.MAX_SAFE_INTEGER;
    if (leftIndex !== rightIndex) return leftIndex - rightIndex;
    return left.localeCompare(right);
  });
}

function collectModelAliases(config: AppConfig | null) {
  if (!config) return [];
  const llmSetting = config.LLM_Setting;
  if (!isJsonObject(llmSetting) || !isJsonObject(llmSetting.providers)) return [];

  const aliases: string[] = [];
  Object.values(llmSetting.providers).forEach((provider) => {
    if (!isJsonObject(provider) || !isJsonObject(provider.models)) return;
    Object.keys(provider.models).forEach((alias) => aliases.push(alias));
  });
  return aliases.sort((left, right) => left.localeCompare(right));
}

function getSelectOptions(path: string) {
  if (path.endsWith(".reasoning_effort") || path === "AgentRetrievalCache.match_reasoning_effort") {
    return ["low", "high", "max"];
  }
  if (path === "IntentRouter.method") return ["llm", "vector"];
  if (path === "ContextMemory.update_trigger") return ["assistant_turn", "topic_switch", "topic_switch_or_interval"];
  if (path === "Frontend.package_manager") return ["pnpm", "npm", "yarn", "bun"];
  if (path === "Security.approval_mode") return ["manual", "off"];
  if (path === "ExecutionBackends.terminal.default_backend") return ["isolated", "local"];
  return null;
}

function isModelAliasPath(path: string) {
  return (
    path === "LLM_Setting.default_model" ||
    /^ModelSelect\.[^.]+\.model$/.test(path) ||
    path === "IntentRouter.llm_fallback.model" ||
    path === "AgentRetrievalCache.match_model" ||
    path === "AutonomousTaskMode.task_planning.model" ||
    path === "AutonomousTaskMode.task_execution.model" ||
    path === "AutonomousTaskMode.sharing_score.model"
  );
}

function isMultilineStringPath(path: string) {
  return (
    path === "Character.char_role" ||
    path === "Character.dialogue_examples" ||
    path === "Character.response_notes"
  );
}

function valueMatchesSearch(item: ConfigItem, query: string) {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return true;
  const valueText = item.sensitive ? "" : String(item.value ?? "");
  return [item.path, item.name, item.description, valueText].some((part) => part.toLowerCase().includes(normalized));
}

function ComplexValueEditor({
  path,
  value,
  onChange,
  onErrorChange,
}: {
  path: string;
  value: JsonValue;
  onChange: (value: JsonValue) => void;
  onErrorChange: (path: string, error: string | null) => void;
}) {
  const serialized = useMemo(() => formatConfig(value), [value]);
  const [text, setText] = useState(serialized);
  const [error, setError] = useState("");
  const [focused, setFocused] = useState(false);

  useEffect(() => {
    if (!focused) {
      setText(serialized);
      setError("");
      onErrorChange(path, null);
    }
  }, [focused, onErrorChange, path, serialized]);

  function handleChange(nextText: string) {
    setText(nextText);
    try {
      const parsed = JSON.parse(nextText) as JsonValue;
      setError("");
      onErrorChange(path, null);
      onChange(parsed);
    } catch {
      const message = "JSON 格式无效";
      setError(message);
      onErrorChange(path, message);
    }
  }

  return (
    <div className="space-y-2">
      <textarea
        value={text}
        onFocus={() => setFocused(true)}
        onBlur={() => {
          setFocused(false);
          if (!error) {
            setText(formatConfig(value));
          }
        }}
        onChange={(event) => handleChange(event.target.value)}
        spellCheck={false}
        className={clsx(
          "min-h-24 w-full resize-y rounded-lg border bg-white px-3 py-2 font-mono text-xs leading-5 text-slate-800 outline-none transition",
          error ? "border-rose-300 ring-2 ring-rose-100" : "border-slate-200 focus:border-blue-300 focus:ring-2 focus:ring-blue-100"
        )}
      />
      {error ? <p className="text-xs font-medium text-rose-600">{error}</p> : null}
    </div>
  );
}

function ConfigValueEditor({
  item,
  onChange,
  onComplexError,
}: {
  item: ConfigItem;
  onChange: (path: string, value: JsonValue) => void;
  onComplexError: (path: string, error: string | null) => void;
}) {
  const [revealed, setRevealed] = useState(false);
  const selectOptions = getSelectOptions(item.path);

  if (item.valueType === "boolean") {
    const checked = Boolean(item.value);
    return (
      <button
        type="button"
        aria-label={checked ? "enabled" : "disabled"}
        onClick={() => onChange(item.path, !checked)}
        className={clsx(
          "relative inline-flex h-6 w-11 items-center rounded-full border transition",
          checked ? "border-emerald-500 bg-emerald-500" : "border-slate-300 bg-slate-200"
        )}
      >
        <span
          className={clsx(
            "inline-block h-5 w-5 rounded-full bg-white shadow-sm transition",
            checked ? "translate-x-5" : "translate-x-0.5"
          )}
        />
      </button>
    );
  }

  if (item.valueType === "number") {
    return (
      <input
        type="number"
        step="any"
        value={Number(item.value)}
        onChange={(event) => onChange(item.path, Number(event.target.value || 0))}
        className="h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-800 outline-none transition focus:border-blue-300 focus:ring-2 focus:ring-blue-100"
      />
    );
  }

  if (selectOptions) {
    return (
      <select
        value={String(item.value ?? "")}
        onChange={(event) => onChange(item.path, event.target.value)}
        className="h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-800 outline-none transition focus:border-blue-300 focus:ring-2 focus:ring-blue-100"
      >
        {selectOptions.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    );
  }

  if (item.valueType === "string") {
    if (isMultilineStringPath(item.path)) {
      return (
        <textarea
          value={String(item.value ?? "")}
          onChange={(event) => onChange(item.path, event.target.value)}
          rows={10}
          className="min-h-32 w-full resize-y rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm leading-6 text-slate-800 outline-none transition focus:border-blue-300 focus:ring-2 focus:ring-blue-100"
        />
      );
    }
    return (
      <div className="flex items-center gap-2">
        <input
          type={item.sensitive && !revealed ? "password" : "text"}
          value={String(item.value ?? "")}
          list={isModelAliasPath(item.path) ? "config-model-aliases" : undefined}
          onChange={(event) => onChange(item.path, event.target.value)}
          className="h-10 min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-800 outline-none transition focus:border-blue-300 focus:ring-2 focus:ring-blue-100"
        />
        {item.sensitive ? (
          <button
            type="button"
            onClick={() => setRevealed((value) => !value)}
            className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 transition hover:border-slate-300 hover:text-slate-800"
            aria-label={revealed ? "hide secret" : "show secret"}
          >
            {revealed ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </button>
        ) : null}
      </div>
    );
  }

  return (
    <ComplexValueEditor
      path={item.path}
      value={item.value}
      onChange={(value) => onChange(item.path, value)}
      onErrorChange={onComplexError}
    />
  );
}

function Notice({ notice }: { notice: NoticeState }) {
  const className =
    notice.type === "success"
      ? "border-emerald-200 bg-emerald-50 text-emerald-700"
      : notice.type === "warning"
        ? "border-amber-200 bg-amber-50 text-amber-700"
        : notice.type === "error"
          ? "border-rose-200 bg-rose-50 text-rose-700"
          : "border-blue-200 bg-blue-50 text-blue-700";
  const Icon = notice.type === "success" ? CheckCircle2 : AlertCircle;

  return (
    <div className={clsx("flex items-center gap-2 rounded-lg border px-4 py-3 text-sm font-medium", className)}>
      <Icon className="h-4 w-4 shrink-0" />
      <span>{notice.text}</span>
    </div>
  );
}

export default function ConfigEditor() {
  const [savedConfig, setSavedConfig] = useState<AppConfig | null>(null);
  const [draftConfig, setDraftConfig] = useState<AppConfig | null>(null);
  const [selectedSection, setSelectedSection] = useState(ALL_SECTION);
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<"visual" | "json">("visual");
  const [jsonText, setJsonText] = useState("");
  const [jsonError, setJsonError] = useState("");
  const [complexErrors, setComplexErrors] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<NoticeState | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const loadConfig = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    try {
      const response = await api.getConfig(signal ? { signal } : undefined);
      if (signal?.aborted) return;
      const config = cloneConfig(response.config);
      setSavedConfig(config);
      setDraftConfig(cloneConfig(config));
      setJsonText(formatConfig(config));
      setJsonError("");
      setComplexErrors({});
      setNotice({ type: "success", text: "已读取 config.json" });
    } catch (error) {
      if (signal?.aborted) return;
      setNotice({ type: "error", text: error instanceof Error ? error.message : "读取配置失败" });
    } finally {
      if (signal?.aborted) return;
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void loadConfig(controller.signal);
    return () => {
      controller.abort();
    };
  }, [loadConfig]);

  const sectionKeys = useMemo(() => sortSectionKeys(Object.keys(draftConfig ?? {})), [draftConfig]);
  const modelAliases = useMemo(() => collectModelAliases(draftConfig), [draftConfig]);
  const allItems = useMemo(() => (draftConfig ? flattenConfig(draftConfig) : []), [draftConfig]);
  const savedText = useMemo(() => (savedConfig ? formatConfig(savedConfig) : ""), [savedConfig]);
  const draftText = useMemo(() => (draftConfig ? formatConfig(draftConfig) : ""), [draftConfig]);
  const hasChanges = Boolean(savedConfig && draftConfig && savedText !== draftText);
  const complexErrorCount = Object.keys(complexErrors).length;
  const visibleItems = useMemo(() => {
    const sectionItems =
      selectedSection === ALL_SECTION ? allItems : allItems.filter((item) => item.rootKey === selectedSection);
    return sectionItems.filter((item) => valueMatchesSearch(item, query));
  }, [allItems, query, selectedSection]);

  const fieldCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    allItems.forEach((item) => {
      counts[item.rootKey] = (counts[item.rootKey] ?? 0) + 1;
    });
    return counts;
  }, [allItems]);

  const activeSectionMeta = selectedSection === ALL_SECTION ? null : getSectionDefinition(selectedSection);
  const activeAccent = activeSectionMeta ? ACCENT_CLASSES[activeSectionMeta.accent] : ACCENT_CLASSES.blue;
  const canSave = Boolean(draftConfig && hasChanges && !saving && !jsonError && complexErrorCount === 0);

  function handleModeChange(nextMode: "visual" | "json") {
    setMode(nextMode);
    if (nextMode === "json" && draftConfig) {
      setJsonText(formatConfig(draftConfig));
      setJsonError("");
    }
  }

  const handleValueChange = useCallback((path: string, value: JsonValue) => {
    setDraftConfig((current) => {
      if (!current) return current;
      return setValueAtPath(current, path, value);
    });
    setNotice(null);
  }, []);

  const handleComplexError = useCallback((path: string, error: string | null) => {
    setComplexErrors((current) => {
      if (error && current[path] === error) {
        return current;
      }
      if (!error && !(path in current)) {
        return current;
      }
      const next = { ...current };
      if (error) {
        next[path] = error;
      } else {
        delete next[path];
      }
      return next;
    });
  }, []);

  function handleRawJsonChange(nextText: string) {
    setJsonText(nextText);
    try {
      const parsed = JSON.parse(nextText);
      if (!isJsonObject(parsed)) {
        throw new Error("config.json 顶层必须是 JSON object");
      }
      setDraftConfig(parsed as AppConfig);
      setJsonError("");
      setComplexErrors({});
      setNotice(null);
    } catch (error) {
      setJsonError(error instanceof Error ? error.message : "JSON 格式无效");
    }
  }

  function handleReset() {
    if (!savedConfig) return;
    const next = cloneConfig(savedConfig);
    setDraftConfig(next);
    setJsonText(formatConfig(next));
    setJsonError("");
    setComplexErrors({});
    setNotice({ type: "info", text: "已恢复为磁盘中的当前配置" });
  }

  async function handleSave() {
    if (!draftConfig || !canSave) return;
    setSaving(true);
    try {
      const response = await api.saveConfig(draftConfig);
      const nextConfig = cloneConfig(response.config);
      setSavedConfig(nextConfig);
      setDraftConfig(cloneConfig(nextConfig));
      setJsonText(formatConfig(nextConfig));
      setJsonError("");
      setComplexErrors({});
      setNotice({
        type: response.restart_required ? "warning" : "success",
        text: response.restart_required
          ? "配置已保存；前端监听地址、端口或启动设置需要重启主程序后完全生效"
          : "配置已保存",
      });
    } catch (error) {
      setNotice({ type: "error", text: error instanceof Error ? error.message : "保存配置失败" });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-slate-50 text-slate-900">
      <header className="z-10 shrink-0 border-b border-slate-200 bg-white px-6 py-4 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-100 text-blue-700">
              <Settings2 className="h-5 w-5" />
            </div>
            <div>
              <h1 className="m-0 text-xl font-bold text-slate-900">配置中心</h1>
              <p className="m-0 text-sm text-slate-500">config.json</p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <div className="flex rounded-lg border border-slate-200 bg-slate-100 p-1">
              <button
                type="button"
                onClick={() => handleModeChange("visual")}
                className={clsx(
                  "inline-flex h-9 items-center gap-2 rounded-md px-3 text-sm font-medium transition",
                  mode === "visual" ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-800"
                )}
              >
                <SlidersHorizontal className="h-4 w-4" />
                <span>表单</span>
              </button>
              <button
                type="button"
                onClick={() => handleModeChange("json")}
                className={clsx(
                  "inline-flex h-9 items-center gap-2 rounded-md px-3 text-sm font-medium transition",
                  mode === "json" ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-800"
                )}
              >
                <FileJson className="h-4 w-4" />
                <span>JSON</span>
              </button>
            </div>

            <button
              type="button"
              onClick={() => void loadConfig()}
              disabled={loading || saving}
              className="inline-flex h-10 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <RefreshCw className={clsx("h-4 w-4", loading && "animate-spin")} />
              <span>刷新</span>
            </button>

            <button
              type="button"
              onClick={handleReset}
              disabled={!hasChanges || saving}
              className="inline-flex h-10 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 transition hover:border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <RotateCcw className="h-4 w-4" />
              <span>重置</span>
            </button>

            <button
              type="button"
              onClick={() => void handleSave()}
              disabled={!canSave}
              className="inline-flex h-10 items-center gap-2 rounded-lg bg-blue-600 px-4 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              <Save className="h-4 w-4" />
              <span>{saving ? "保存中" : "保存"}</span>
            </button>
          </div>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 overflow-hidden">
        <aside className="hidden h-full min-h-0 w-80 shrink-0 flex-col overflow-hidden border-r border-slate-200 bg-white p-4 lg:flex">
          <div className="mb-4 grid shrink-0 grid-cols-2 gap-3">
            <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
              <p className="m-0 text-xs font-medium text-slate-500">分区</p>
              <p className="m-0 text-2xl font-bold text-slate-900">{sectionKeys.length}</p>
            </div>
            <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
              <p className="m-0 text-xs font-medium text-slate-500">字段</p>
              <p className="m-0 text-2xl font-bold text-slate-900">{allItems.length}</p>
            </div>
            <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
              <p className="m-0 text-xs font-medium text-slate-500">模型别名</p>
              <p className="m-0 text-2xl font-bold text-blue-700">{modelAliases.length}</p>
            </div>
            <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
              <p className="m-0 text-xs font-medium text-slate-500">敏感项</p>
              <p className="m-0 text-2xl font-bold text-rose-700">{allItems.filter((item) => item.sensitive).length}</p>
            </div>
          </div>

          <button
            type="button"
            onClick={() => setSelectedSection(ALL_SECTION)}
            className={clsx(
              "mb-2 flex w-full shrink-0 items-center justify-between rounded-lg border px-3 py-3 text-left transition",
              selectedSection === ALL_SECTION
                ? "border-blue-200 bg-blue-50 text-blue-800"
                : "border-transparent text-slate-600 hover:border-slate-200 hover:bg-slate-50"
            )}
          >
            <span className="flex items-center gap-3">
              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-100 text-blue-700">
                <FileJson className="h-4 w-4" />
              </span>
              <span>
                <span className="block text-sm font-semibold">全部配置</span>
                <span className="block text-xs text-slate-500">{allItems.length} 个字段</span>
              </span>
            </span>
          </button>

          <div className="min-h-0 flex-1 space-y-2 overflow-y-scroll pr-1 [scrollbar-gutter:stable]">
            {sectionKeys.map((key) => {
              const section = getSectionDefinition(key);
              const Icon = section.icon;
              const accent = ACCENT_CLASSES[section.accent];
              const active = selectedSection === key;
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => setSelectedSection(key)}
                  className={clsx(
                    "relative flex w-full items-start gap-3 rounded-lg border px-3 py-3 text-left transition",
                    active ? accent.active : "border-transparent text-slate-600 hover:border-slate-200 hover:bg-slate-50"
                  )}
                >
                  <span className={clsx("mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg", accent.icon)}>
                    <Icon className="h-4 w-4" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-semibold">{section.label}</span>
                      <span className="shrink-0 text-xs font-medium text-slate-400">{fieldCounts[key] ?? 0}</span>
                    </span>
                    <span className="mt-1 block text-xs leading-5 text-slate-500">{section.summary}</span>
                  </span>
                  {active ? <span className={clsx("absolute left-0 top-3 h-8 w-1 rounded-r", accent.marker)} /> : null}
                </button>
              );
            })}
          </div>
        </aside>

        <main className="h-full min-h-0 min-w-0 flex-1 overflow-y-scroll p-6 [scrollbar-gutter:stable]">
          <datalist id="config-model-aliases">
            {modelAliases.map((alias) => (
              <option key={alias} value={alias} />
            ))}
          </datalist>

          <div className="mx-auto flex max-w-7xl flex-col gap-4">
            {notice ? <Notice notice={notice} /> : null}

            {jsonError ? (
              <div className="flex items-center gap-2 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm font-medium text-rose-700">
                <AlertCircle className="h-4 w-4 shrink-0" />
                <span>{jsonError}</span>
              </div>
            ) : null}

            {complexErrorCount > 0 ? (
              <div className="flex items-center gap-2 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm font-medium text-rose-700">
                <AlertCircle className="h-4 w-4 shrink-0" />
                <span>{complexErrorCount} 个 JSON 字段格式无效</span>
              </div>
            ) : null}

            {loading && !draftConfig ? (
              <div className="rounded-lg border border-slate-200 bg-white p-8 text-sm text-slate-500">正在读取 config.json...</div>
            ) : mode === "json" ? (
              <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
                <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
                  <div className="flex items-center gap-3">
                    <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-100 text-slate-700">
                      <FileJson className="h-4 w-4" />
                    </span>
                    <div>
                      <h2 className="m-0 text-base font-bold text-slate-900">原始 JSON</h2>
                      <p className="m-0 text-xs text-slate-500">{draftConfig ? Object.keys(draftConfig).length : 0} 个顶层配置区</p>
                    </div>
                  </div>
                  <span className={clsx("text-sm font-semibold", hasChanges ? "text-amber-700" : "text-emerald-700")}>
                    {hasChanges ? "有未保存修改" : "已同步"}
                  </span>
                </div>
                <textarea
                  value={jsonText}
                  onChange={(event) => handleRawJsonChange(event.target.value)}
                  spellCheck={false}
                  className="h-[calc(100vh-260px)] min-h-96 w-full resize-none border-0 bg-slate-950 p-4 font-mono text-sm leading-6 text-slate-100 outline-none"
                />
              </section>
            ) : (
              <>
                <section className="rounded-lg border border-slate-200 bg-white shadow-sm">
                  <div className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-200 px-5 py-4">
                    <div className="flex min-w-0 items-center gap-3">
                      <span className={clsx("flex h-10 w-10 shrink-0 items-center justify-center rounded-lg", activeAccent.icon)}>
                        {activeSectionMeta ? (
                          <activeSectionMeta.icon className="h-5 w-5" />
                        ) : (
                          <FileJson className="h-5 w-5" />
                        )}
                      </span>
                      <div className="min-w-0">
                        <h2 className="m-0 truncate text-lg font-bold text-slate-900">
                          {activeSectionMeta?.label ?? "全部配置"}
                        </h2>
                        <p className="m-0 text-sm text-slate-500">
                          {activeSectionMeta?.summary ?? "config.json 中当前可编辑的全部字段。"}
                        </p>
                      </div>
                    </div>

                    <div className="relative w-full sm:w-80">
                      <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                      <input
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                        placeholder="搜索路径或说明"
                        className="h-10 w-full rounded-lg border border-slate-200 bg-white pl-9 pr-3 text-sm text-slate-800 outline-none transition focus:border-blue-300 focus:ring-2 focus:ring-blue-100"
                      />
                    </div>
                  </div>

                  <div className="divide-y divide-slate-100">
                    {visibleItems.length === 0 ? (
                      <div className="px-5 py-12 text-center text-sm text-slate-500">没有匹配的配置项</div>
                    ) : (
                      visibleItems.map((item) => (
                        <div key={item.path} className="grid gap-4 px-5 py-4 xl:grid-cols-[minmax(260px,0.9fr)_minmax(300px,1.1fr)]">
                          <div className="min-w-0">
                            <div className="mb-2 flex flex-wrap items-center gap-2">
                              <span className="rounded-md bg-slate-100 px-2 py-1 font-mono text-xs font-semibold text-slate-700">
                                {item.path}
                              </span>
                              <span className="rounded-md border border-slate-200 px-2 py-1 text-xs font-medium uppercase tracking-normal text-slate-500">
                                {item.valueType}
                              </span>
                              {item.sensitive ? (
                                <span className="inline-flex items-center gap-1 rounded-md border border-rose-200 bg-rose-50 px-2 py-1 text-xs font-semibold text-rose-700">
                                  <KeyRound className="h-3 w-3" />
                                  密钥
                                </span>
                              ) : null}
                            </div>
                            <p className="m-0 text-sm leading-6 text-slate-600">{item.description}</p>
                          </div>

                          <ConfigValueEditor
                            item={item}
                            onChange={handleValueChange}
                            onComplexError={handleComplexError}
                          />
                        </div>
                      ))
                    )}
                  </div>
                </section>
              </>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
