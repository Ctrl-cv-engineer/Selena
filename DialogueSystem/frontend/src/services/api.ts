import type {
  AppConfig,
  BrowserExtractPageResult,
  BrowserOpenTabResult,
  BrowserReadLinkedPageResult,
  BrowserStatusResult,
  CollectionDetail,
  CollectionPointId,
  CollectionRecord,
  CollectionSummary,
  LLMCallLog,
  LongTermMemorySearchResult,
  LongTermMemoryStoreResult,
  ManagedSkillListResult,
  MCPToolListResult,
  RuntimeState,
  ScheduleTask,
  SkillMutationResult,
  SubAgentBatchResult,
  SubAgentListResult,
  SubAgentStatusResult,
  ToolApprovalListResult,
  ToolApprovalResolveResult,
  WorkbenchDiffPreview,
  WorkbenchOverview,
  WorkbenchWorktreeMutationResult,
} from "@/types";

interface ApiEnvelope {
  ok: boolean;
  error?: string;
}

interface RuntimeResponse extends ApiEnvelope, RuntimeState {}

interface ConfigResponse extends ApiEnvelope {
  config: AppConfig;
  restart_required?: boolean;
}

interface ScheduleResponse extends ApiEnvelope {
  date?: string | null;
  tasks: ScheduleTask[];
}

interface CollectionListResponse extends ApiEnvelope {
  collections: CollectionSummary[];
}

interface CollectionResponse extends ApiEnvelope, CollectionDetail {}

interface CollectionMutationResponse extends ApiEnvelope {
  collection: string;
  id?: CollectionPointId;
  deleted_ids?: CollectionPointId[];
  deleted_count?: number;
  vector_generated?: boolean;
  record?: CollectionRecord;
}

interface LLMLogsResponse extends ApiEnvelope {
  logs: LLMCallLog[];
}

interface SubAgentListResponse extends ApiEnvelope, SubAgentListResult {}

interface SubAgentStatusResponse extends ApiEnvelope, SubAgentStatusResult {}

interface SubAgentBatchResponse extends ApiEnvelope, SubAgentBatchResult {}

interface LongTermMemorySearchResponse extends ApiEnvelope, LongTermMemorySearchResult {}

interface LongTermMemoryStoreResponse extends ApiEnvelope, LongTermMemoryStoreResult {}

interface MCPToolListResponse extends ApiEnvelope, MCPToolListResult {}

interface ManagedSkillListResponse extends ApiEnvelope, ManagedSkillListResult {}

interface SkillMutationResponse extends ApiEnvelope, SkillMutationResult {}

interface ToolApprovalListResponse extends ApiEnvelope, ToolApprovalListResult {}

interface ToolApprovalResolveResponse extends ApiEnvelope, ToolApprovalResolveResult {}

interface BrowserStatusResponse extends ApiEnvelope, BrowserStatusResult {}

interface WorkbenchResponse extends ApiEnvelope, WorkbenchOverview {}

interface WorkbenchDiffResponse extends ApiEnvelope, WorkbenchDiffPreview {}

interface WorkbenchWorktreeMutationResponse extends ApiEnvelope, WorkbenchWorktreeMutationResult {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method || "GET").toUpperCase();
  const requestPath =
    method === "GET"
      ? `${path}${path.includes("?") ? "&" : "?"}_ts=${Date.now()}`
      : path;
  const response = await fetch(requestPath, {
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-cache",
      Pragma: "no-cache",
      ...(init?.headers ?? {}),
    },
    ...init,
    method,
  });

  const data = (await response.json()) as ApiEnvelope & T;
  if (!response.ok || !data.ok) {
    throw new Error(data.error || "Request failed.");
  }
  return data as T;
}

export const api = {
  getRuntime() {
    return request<RuntimeResponse>("/api/runtime");
  },

  sendMessage(message: string) {
    return request<RuntimeResponse>("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message }),
    });
  },

  clearDialogue() {
    return request<RuntimeResponse>("/api/chat/clear", {
      method: "POST",
    });
  },

  continueDialogue() {
    return request<RuntimeResponse>("/api/chat/continue", {
      method: "POST",
    });
  },

  getConfig(init?: RequestInit) {
    return request<ConfigResponse>("/api/config", init);
  },

  saveConfig(config: AppConfig) {
    return request<ConfigResponse>("/api/config", {
      method: "PUT",
      body: JSON.stringify({ config }),
    });
  },

  getSchedules(date: string) {
    const query = new URLSearchParams({ date });
    return request<ScheduleResponse>(`/api/schedules?${query.toString()}`);
  },

  getCollections() {
    return request<CollectionListResponse>("/api/collections");
  },

  getCollection(name: string, limit = 200) {
    const query = new URLSearchParams({ limit: String(limit) });
    return request<CollectionResponse>(
      `/api/collections/${encodeURIComponent(name)}?${query.toString()}`
    );
  },

  createCollectionRecord(
    name: string,
    payload: {
      id?: CollectionPointId;
      payload: Record<string, unknown>;
      vector?: unknown;
      auto_vectorize?: boolean;
      vector_text?: string;
    }
  ) {
    return request<CollectionMutationResponse>(`/api/collections/${encodeURIComponent(name)}/records`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  updateCollectionRecord(
    name: string,
    id: CollectionPointId,
    payload: {
      payload: Record<string, unknown>;
      vector?: unknown;
      auto_vectorize?: boolean;
      vector_text?: string;
    }
  ) {
    return request<CollectionMutationResponse>(
      `/api/collections/${encodeURIComponent(name)}/records/${encodeURIComponent(String(id))}`,
      {
        method: "PUT",
        body: JSON.stringify(payload),
      }
    );
  },

  deleteCollectionRecord(name: string, id: CollectionPointId) {
    return request<CollectionMutationResponse>(
      `/api/collections/${encodeURIComponent(name)}/records/${encodeURIComponent(String(id))}`,
      {
        method: "DELETE",
      }
    );
  },

  deleteCollectionRecords(name: string, ids: CollectionPointId[]) {
    return request<CollectionMutationResponse>(
      `/api/collections/${encodeURIComponent(name)}/records/batch-delete`,
      {
        method: "POST",
        body: JSON.stringify({ ids }),
      }
    );
  },

  getLLMLogs(sinceId = 0) {
    const query = sinceId > 0 ? `?since_id=${sinceId}` : "";
    return request<LLMLogsResponse>(`/api/llm-logs${query}`);
  },

  getATMLLMLogs(sinceId = 0) {
    const query = sinceId > 0 ? `?since_id=${sinceId}` : "";
    return request<LLMLogsResponse>(`/api/atm-llm-logs${query}`);
  },

  getATMSessions(date?: string) {
    const query = date ? `?date=${encodeURIComponent(date)}` : "";
    return request<{ ok: boolean; sessions: import("@/types").ATMSession[]; tasks: import("@/types").ATMTask[]; attempts: import("@/types").ATMAttempt[] }>(`/api/atm-sessions${query}`);
  },

  getWorkbench() {
    return request<WorkbenchResponse>("/api/workbench");
  },

  getWorkbenchDiff(path: string, staged = false) {
    const query = new URLSearchParams({ path, staged: String(staged) });
    return request<WorkbenchDiffResponse>(`/api/workbench/diff?${query.toString()}`);
  },

  createWorkbenchWorktree(payload: { branch: string; base_ref?: string; path?: string }) {
    return request<WorkbenchWorktreeMutationResponse>("/api/workbench/worktrees", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  removeWorkbenchWorktree(payload: { path: string }) {
    return request<WorkbenchWorktreeMutationResponse>("/api/workbench/worktrees/remove", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  getSubAgents(includeCompleted = true) {
    const query = new URLSearchParams({ include_completed: String(includeCompleted) });
    return request<SubAgentListResponse>(`/api/subagents?${query.toString()}`);
  },

  getSubAgentStatus(taskId: string) {
    return request<SubAgentStatusResponse>(`/api/subagents/${encodeURIComponent(taskId)}`);
  },

  createSubAgent(payload: {
    task: string;
    agent_type?: string;
    model?: string;
    max_tool_calls?: number;
    timeout_seconds?: number;
    priority?: number;
    use_cache?: boolean;
    task_context?: Record<string, unknown>;
  }) {
    return request<SubAgentStatusResponse>("/api/subagents", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  createSubAgentBatch(payload: {
    tasks: Array<{
      task: string;
      agent_type?: string;
      model?: string;
      max_tool_calls?: number;
      timeout_seconds?: number;
      priority?: number;
      use_cache?: boolean;
      task_context?: Record<string, unknown>;
    }>;
    group_label?: string;
    wait_for_completion?: boolean;
    timeout_seconds?: number;
    poll_interval_seconds?: number;
  }) {
    return request<SubAgentBatchResponse>("/api/subagents/batch", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  continueSubAgent(
    taskId: string,
    payload: {
      user_reply?: string;
      approval_decision?: "approved" | "rejected";
    }
  ) {
    return request<SubAgentStatusResponse>(`/api/subagents/${encodeURIComponent(taskId)}/continue`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  cancelSubAgent(taskId: string, payload?: { reason?: string }) {
    return request<SubAgentStatusResponse>(`/api/subagents/${encodeURIComponent(taskId)}/cancel`, {
      method: "POST",
      body: JSON.stringify(payload ?? {}),
    });
  },

  waitForSubAgents(payload: {
    task_ids: string[];
    timeout_seconds?: number;
    poll_interval_seconds?: number;
  }) {
    return request<SubAgentBatchResponse>("/api/subagents/wait", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  searchLongTermMemory(payload: {
    query: string;
    include_historical?: boolean;
    limit?: number;
  }) {
    return request<LongTermMemorySearchResponse>("/api/memory/search", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  storeLongTermMemory(payload: {
    text: string;
    personalized_text?: string;
    text_type?: string;
    importance?: number;
    ttl_days?: number;
  }) {
    return request<LongTermMemoryStoreResponse>("/api/memory/store", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  getMcpTools() {
    return request<MCPToolListResponse>("/api/mcp-tools");
  },

  refreshMcpTools() {
    return request<MCPToolListResponse>("/api/mcp-tools/refresh", {
      method: "POST",
    });
  },

  getSkills() {
    return request<ManagedSkillListResponse>("/api/skills");
  },

  getToolApprovals() {
    return request<ToolApprovalListResponse>("/api/tool-approvals");
  },

  resolveToolApproval(payload: { approval_id: string; decision: "approved" | "rejected" }) {
    return request<ToolApprovalResolveResponse>("/api/tool-approvals/resolve", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  saveSkill(payload: {
    skill_name: string;
    description: string;
    when_to_use?: string[];
    intent_examples?: string[];
    tool_definitions?: unknown[];
    skill_instructions?: string;
    runtime_code?: string;
    enabled?: boolean;
  }) {
    return request<SkillMutationResponse>("/api/skills", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  deleteSkill(skillName: string) {
    return request<SkillMutationResponse>(`/api/skills/${encodeURIComponent(skillName)}`, {
      method: "DELETE",
    });
  },

  getBrowserStatus() {
    return request<BrowserStatusResponse>("/api/browser/status");
  },

  browserOpenTab(payload: { url: string }) {
    return request<BrowserOpenTabResult>("/api/browser/open-tab", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  browserExtractPage(payload?: { max_text_length?: number }) {
    return request<BrowserExtractPageResult>("/api/browser/extract-page", {
      method: "POST",
      body: JSON.stringify(payload ?? {}),
    });
  },

  browserReadLinkedPage(payload: {
    query?: string;
    ref?: string;
    auto_open_first?: boolean;
    max_candidates?: number;
    max_text_length?: number;
  }) {
    return request<BrowserReadLinkedPageResult>("/api/browser/read-linked-page", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
};
