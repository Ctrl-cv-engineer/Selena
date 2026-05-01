export interface Message {
  id: string;
  type: "user" | "agent" | "roleplay" | "simple" | "assistant";
  content: string;
  timestamp: Date;
}

export interface DebugInfo {
  agentMessages: Message[];
  rolePlayMessages: Message[];
  simpleMessages: Message[];
}

export interface CollectionData {
  name: string;
  records: Record<string, unknown>[];
  total: number;
}

export interface ScheduleEvent {
  id: string;
  title: string;
  time: string;
  data: unknown;
}

export interface ScheduleData {
  date: string;
  events: ScheduleEvent[];
}

export interface RuntimeMessage {
  id: string;
  topic_group: number;
  role: string;
  content: string;
  timestamp: string;
}

export interface RuntimeContextMessage {
  id: string;
  role: string;
  content: string;
}

export interface RuntimeAgentStep {
  id: string;
  step: number;
  phase: string;
  tool_name: string;
  title: string;
  detail: string;
  status: string;
  timestamp: string;
  input_count?: number;
}

export interface RuntimeToolSecurityEvent {
  id: string;
  event_type: string;
  tool_name: string;
  detail: string;
  status: string;
  timestamp: string;
  input_count?: number;
  payload?: Record<string, unknown>;
}

export interface RuntimeToolApproval {
  approval_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  policy: Record<string, unknown>;
  status: string;
  created_at: string;
  updated_at: string;
  resolved_at: string;
  decision: string;
  input_count?: number;
  result?: Record<string, unknown>;
}

export interface RuntimeMemorySection {
  key: string;
  label: string;
  description: string;
  layer: string;
  items: string[];
  item_count: number;
}

export interface RuntimeMemoryLayerBase {
  label: string;
  source_kind: string;
  source_label: string;
  source_path?: string;
  updated_at?: string;
}

export interface RuntimePersistentCoreMemory extends RuntimeMemoryLayerBase {
  sections: RuntimeMemorySection[];
}

export interface RuntimeTopicWorkingMemory extends RuntimeMemoryLayerBase {
  sections: RuntimeMemorySection[];
}

export interface RuntimeTopicArchiveRecord {
  archive_id: number | null;
  source_file: string;
  source_session_prefix: string;
  source_topic_group: number | null;
  topic_message_count: number;
  summary_text: string;
  archived_at: string;
  updated_at: string;
  topic_excerpt: string;
}

export interface RuntimeTopicArchiveMemory extends RuntimeMemoryLayerBase {
  total_archives: number;
  recent_archives: RuntimeTopicArchiveRecord[];
}

export interface RuntimeAtomicMemoryRecord {
  id?: string | number | null;
  text: string;
  personalizedText: string;
  textType: string;
  memory_status: string;
  memory_status_detail: string;
  timestamp: string;
  updated_at: string;
  valid_from: string;
  valid_to: string;
  source: string;
  source_file: string;
  source_topic_group: number | null;
  memory_kind: string;
}

export interface RuntimeAtomicSemanticMemory extends RuntimeMemoryLayerBase {
  source_collection: string;
  runtime_initialized: boolean;
  total_records: number;
  atomic_records: number;
  active_records: number;
  historical_records: number;
  topic_summary_records: number;
  recent_atomic_memories: RuntimeAtomicMemoryRecord[];
  error: string;
}

export interface RuntimeMemoryLayers {
  persistent_core: RuntimePersistentCoreMemory;
  topic_working_memory: RuntimeTopicWorkingMemory;
  topic_archive: RuntimeTopicArchiveMemory;
  atomic_semantic_memory: RuntimeAtomicSemanticMemory;
}

export interface ScheduleTask {
  task_id: number;
  task_date: string;
  reminder_time: string;
  task_content: string;
  reminder_status: string;
  task_status: string;
  created_at: string;
  updated_at: string;
  reminded_at: string | null;
}

export interface RuntimeState {
  conversation: RuntimeMessage[];
  agent_steps: RuntimeAgentStep[];
  pending_tool_approvals: RuntimeToolApproval[];
  recent_tool_security_events: RuntimeToolSecurityEvent[];
  contexts: {
    agent: RuntimeContextMessage[];
    role_play: RuntimeContextMessage[];
    simple: RuntimeContextMessage[];
  };
  memory_layers?: RuntimeMemoryLayers;
  due_tasks: ScheduleTask[];
  meta: {
    topic_group: number;
    input_count: number;
    runtime_initialized: boolean;
    agent_suspended?: boolean;
    agent_suspended_question?: string;
  };
}

export interface CollectionSummary {
  key: string;
  name: string;
  label?: string;
  storage_kind?: "qdrant" | "sqlite";
  description?: string;
  vector_size: number;
  exists: boolean;
}

export type CollectionPointId = string | number;

export interface CollectionRecord {
  id?: CollectionPointId;
  [key: string]: unknown;
}

export interface CollectionFieldSchema {
  key: string;
  sample: unknown;
}

export interface CollectionDetail {
  name: string;
  label?: string;
  storage_kind?: "qdrant" | "sqlite";
  description?: string;
  readonly?: boolean;
  exists: boolean;
  total: number;
  vector_kind: "single" | "named";
  vector_size: number;
  vector_names: string[];
  named_vector_sizes: Record<string, number>;
  records: CollectionRecord[];
  field_schema?: CollectionFieldSchema[];
}

export type JsonPrimitive = string | number | boolean | null;

export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

export type AppConfig = Record<string, JsonValue>;


// ---- LLM Inspector types ----

// ---- ATM (Autonomous Task Mode) types ----

export interface ATMSession {
  id: number;
  session_date: string;
  total_input_tokens: number;
  total_output_tokens: number;
  tasks_planned: number;
  tasks_completed: number;
  tasks_carried_over: number;
  interrupt_count: number;
  plan_generated_at: string | null;
  session_finished_at: string | null;
  finish_reason: string;
  created_at: string;
  updated_at: string;
}

export interface ATMTask {
  id: number;
  task_date: string;
  task_content: string;
  expected_goal: string;
  status: string;
  source: string;
  current_attempt_id: string;
  attempt_count: number;
  token_usage_input: number;
  token_usage_output: number;
  execution_log: string;
  resume_snapshot: string;
  pause_reason: string;
  carry_over_from_date: string | null;
  carry_over_from_id: number | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  pause_requested_at: string | null;
  paused_at: string | null;
  updated_at: string;
}

export interface ATMAttempt {
  attempt_id: string;
  task_id: number;
  task_date: string;
  lease_id: string;
  subagent_task_id: string;
  status: string;
  input_tokens: number;
  output_tokens: number;
  result_summary: string;
  error_message: string;
  started_at: string;
  last_heartbeat_at: string | null;
  finished_at: string | null;
  updated_at: string;
}

export interface ATMSessionData {
  sessions: ATMSession[];
  tasks: ATMTask[];
  attempts: ATMAttempt[];
}

export interface LLMCallMessage {
  role: string;
  content: unknown;
}

export interface LLMCallLog {
  id: number;
  timestamp: string;
  completed_at?: string;
  duration_ms?: number;
  status?: string;
  caller: string;
  model_key: string;
  model_name: string;
  thinking: boolean;
  json_mode: boolean;
  stream: boolean;
  reasoning_effort?: "high" | "max" | string;
  messages: LLMCallMessage[];
  extra?: {
    tools?: unknown[];
    [key: string]: unknown;
  };
}

// ---- Advanced runtime / debug panel types ----

export interface FunctionToolDefinition {
  type: "function";
  function: {
    name: string;
    description: string;
    parameters: {
      type: string;
      properties: Record<string, unknown>;
      required?: string[];
      additionalProperties?: boolean;
    };
  };
}

export interface SubAgentAwaiting {
  type?: string;
  question?: string;
  options?: string[];
  context?: string;
  approval_id?: string;
  tool_name?: string;
  tool_arguments?: Record<string, unknown>;
  policy?: Record<string, unknown>;
  clarification_required?: boolean;
}

export interface SubAgentTaskStats {
  tool_calls: number;
  duration_seconds: number;
}

export interface SubAgentTask {
  task_id: string;
  task: string;
  agent_type?: string;
  task_context?: Record<string, unknown>;
  model: string;
  status: string;
  priority?: number;
  priority_label?: string;
  created_at: number;
  updated_at: number;
  finished_at: number | null;
  result: string;
  error: string;
  tool_trace: unknown[];
  structured_output?: Record<string, unknown>;
  max_tool_calls: number;
  timeout_seconds: number;
  run_in_background?: boolean;
  deadline_at?: number | null;
  awaiting?: SubAgentAwaiting;
  resume_count?: number;
  cancel_requested?: boolean;
  cancel_reason?: string;
  status_message?: string;
  queue_position?: number | null;
  queue_reason?: string;
  queued_at?: number | null;
  stats?: SubAgentTaskStats;
  wait_completed?: boolean;
  waiting_for_external_input?: boolean;
  cache_hit?: boolean;
  cache_source_task_id?: string;
  cache_created_at?: number | null;
  cache_expires_at?: number | null;
  cache_age_seconds?: number | null;
  group_id?: string;
  group_index?: number | null;
  group_size?: number | null;
  group_label?: string;
}

export interface SubAgentListResult {
  count: number;
  tasks: SubAgentTask[];
}

export interface SubAgentStatusResult {
  task: SubAgentTask;
}

export interface SubAgentBatchError {
  index?: number;
  task?: string;
  task_id?: string;
  code?: string;
  error: string;
}

export interface SubAgentBatchGroup {
  group_id: string;
  group_label?: string;
  requested_count?: number;
  created_count?: number;
  error_count?: number;
  created_at?: number;
}

export interface SubAgentBatchSummary {
  requested_count: number;
  resolved_count: number;
  error_count: number;
  unknown_task_ids?: string[];
  status_counts?: Record<string, number>;
  terminal_count?: number;
  completed_count?: number;
  failed_count?: number;
  cancelled_count?: number;
  timed_out_count?: number;
  running_count?: number;
  queued_count?: number;
  waiting_count?: number;
  cache_hit_count?: number;
  wait_completed?: boolean;
  waiting_for_external_input?: boolean;
  deadline_reached?: boolean;
  group_id?: string;
  group_label?: string;
  group_ids?: string[];
  group_labels?: string[];
  terminal_task_ids?: string[];
  waiting_task_ids?: string[];
  active_task_ids?: string[];
}

export interface SubAgentBatchResult {
  count: number;
  tasks: SubAgentTask[];
  group?: SubAgentBatchGroup;
  summary?: SubAgentBatchSummary;
  errors?: SubAgentBatchError[];
  partial_failure?: boolean;
}

export interface LongTermMemoryResult {
  id?: string | number | null;
  score: number;
  text: string;
  textType: string;
  memory_kind: string;
  memory_status: string;
  timestamp: string;
  source: string;
  source_file: string;
  source_topic_group: number | null;
  topic_archive?: RuntimeTopicArchiveRecord | null;
}

export interface LongTermMemorySearchResult {
  count: number;
  results: LongTermMemoryResult[];
}

export interface LongTermMemoryStoreResult {
  text: string;
  text_type: string;
  importance: number;
  ttl_days: number;
}

export interface MCPToolSpec {
  server_name: string;
  server_url: string;
  tool_name: string;
  tool_definition: FunctionToolDefinition;
}

export interface MCPToolListResult {
  count: number;
  tools: MCPToolSpec[];
}

export interface ManagedSkill {
  folder_name: string;
  skill_name: string;
  enabled: boolean;
  description: string;
  tool_names: string[];
  manifest_path: string;
  skill_path: string;
  has_skill_md: boolean;
  runtime_path: string;
  runtime_mode: string;
  trusted_runtime: boolean;
}

export interface SkillDiagnostic {
  folder_name: string;
  severity: string;
  message: string;
  path?: string;
}

export interface ManagedSkillListResult {
  count: number;
  skills: ManagedSkill[];
  diagnostics?: SkillDiagnostic[];
}

export interface SkillMutationResult {
  skill_dir?: string;
  manifest_path?: string;
  skill_path?: string;
  runtime_path?: string;
  tool_paths?: string[];
  eval_path?: string;
}

export interface ToolApprovalListResult {
  count: number;
  approvals: RuntimeToolApproval[];
}

export interface ToolApprovalResolveResult {
  approval: RuntimeToolApproval;
}

export interface BrowserStatusTool {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
}

export interface BrowserStatusPage {
  url?: string;
  title?: string;
  element_count?: number;
  page_text?: string;
  error?: string;
}

export interface BrowserStatusResult {
  tool_count: number;
  tools: BrowserStatusTool[];
  controller_initialized: boolean;
  current_page: BrowserStatusPage;
}

export interface BrowserOpenTabResult {
  ok: boolean;
  action?: string;
  url?: string;
  title?: string;
  error?: string;
  [key: string]: unknown;
}

export interface BrowserExtractPageResult {
  ok: boolean;
  action?: string;
  url?: string;
  title?: string;
  page_text?: string;
  element_count?: number;
  snapshot?: string;
  error?: string;
  [key: string]: unknown;
}

export interface BrowserReadLinkedPageResult {
  ok: boolean;
  action?: string;
  query?: string;
  opened?: boolean;
  requires_selection?: boolean;
  candidate_count?: number;
  candidates?: Array<{
    ref?: string;
    label?: string;
    href?: string;
    text?: string;
  }>;
  clicked_ref?: string;
  click_result?: Record<string, unknown>;
  search_result?: Record<string, unknown>;
  page?: {
    url?: string;
    title?: string;
    page_text?: string;
    snapshot?: string;
    tab_id?: string;
  };
  error?: string;
  snapshot?: string;
  [key: string]: unknown;
}

export interface WorkbenchGitFileChange {
  path: string;
  display_path: string;
  index_status: string;
  worktree_status: string;
  staged: boolean;
  unstaged: boolean;
  untracked: boolean;
  renamed: boolean;
}

export interface WorkbenchGitDiffFile {
  path: string;
  additions: number;
  deletions: number;
  changes: number;
}

export interface WorkbenchGitDiffStat {
  file_count: number;
  additions: number;
  deletions: number;
  files: WorkbenchGitDiffFile[];
}

export interface WorkbenchWorktree {
  path: string;
  branch: string;
  branch_ref: string;
  head: string;
  is_current: boolean;
  is_detached: boolean;
  is_prunable: boolean;
  locked: boolean;
  bare: boolean;
}

export interface WorkbenchGitStatus {
  available: boolean;
  root: string;
  current_path: string;
  branch: string;
  head: string;
  detached: boolean;
  upstream: string;
  ahead: number;
  behind: number;
  dirty: boolean;
  staged_count: number;
  unstaged_count: number;
  untracked_count: number;
  changed_files: WorkbenchGitFileChange[];
  staged_diff: WorkbenchGitDiffStat;
  unstaged_diff: WorkbenchGitDiffStat;
  worktrees: WorkbenchWorktree[];
  default_worktree_root: string;
  error?: string;
}

export interface WorkbenchCallerStat {
  name: string;
  count: number;
}

export interface WorkbenchUsageSummary {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  tracked_calls: number;
  missing_usage_calls: number;
}

export interface WorkbenchCostEstimate {
  available: boolean;
  amount_usd: number;
  reason?: string;
}

export interface WorkbenchLLMSummary {
  total_calls: number;
  completed_calls: number;
  failed_calls: number;
  running_calls: number;
  total_duration_ms: number;
  average_duration_ms: number;
  unique_models: string[];
  callers: WorkbenchCallerStat[];
  usage: WorkbenchUsageSummary;
  cost_estimate: WorkbenchCostEstimate;
  recent_calls: LLMCallLog[];
}

export interface WorkbenchSubAgentSummary {
  count: number;
  active_count: number;
  queued_count: number;
  waiting_count: number;
  completed_count: number;
  failed_count: number;
  cancelled_count: number;
  cache_hit_count: number;
  tasks: SubAgentTask[];
}

export interface WorkbenchOverview {
  generated_at: string;
  runtime: RuntimeState;
  subagents: WorkbenchSubAgentSummary;
  git: WorkbenchGitStatus;
  llm: WorkbenchLLMSummary;
}

export interface WorkbenchDiffPreview {
  path: string;
  staged: boolean;
  patch: string;
  truncated: boolean;
  generated_at: string;
  error?: string;
}

export interface WorkbenchWorktreeMutationResult {
  message: string;
  worktree?: WorkbenchWorktree | null;
  git: WorkbenchGitStatus;
}
