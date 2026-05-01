import { type ElementType, type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Bot,
  Cpu,
  FileText,
  GitBranch,
  Layers3,
  Play,
  Plus,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  Trash2,
  Wrench,
} from "lucide-react";
import clsx from "clsx";

import { formatLLMLogMessageContent } from "@/lib/llmLogContent";
import { api } from "@/services/api";
import type {
  LLMCallLog,
  RuntimeAgentStep,
  RuntimeToolApproval,
  SubAgentTask,
  WorkbenchDiffPreview,
  WorkbenchOverview,
  WorkbenchWorktree,
} from "@/types";

type DiffSelection = {
  path: string;
  staged: boolean;
};

const AGENT_TYPE_OPTIONS = ["general", "explore", "research", "plan", "review", "test"];

function formatTimestamp(value?: string | number | null) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  if (typeof value === "number") {
    const numeric = value > 1e12 ? value : value * 1000;
    return new Date(numeric).toLocaleString();
  }
  return value;
}

function formatDuration(durationMs?: number) {
  if (!durationMs) return "-";
  if (durationMs >= 1000) {
    return `${(durationMs / 1000).toFixed(2)}s`;
  }
  return `${durationMs}ms`;
}

function formatStatusText(status: string) {
  const normalized = String(status || "").toLowerCase();
  if (normalized === "completed") return "已完成";
  if (normalized === "running") return "运行中";
  if (normalized === "queued") return "排队中";
  if (normalized === "waiting_input") return "等待输入";
  if (normalized === "waiting_approval") return "等待审批";
  if (normalized === "cancelling") return "取消中";
  if (normalized === "timed_out") return "已超时";
  if (normalized === "failed") return "失败";
  if (normalized === "cancelled") return "已取消";
  return status || "未知";
}

function getStatusClass(status: string) {
  const normalized = String(status || "").toLowerCase();
  if (normalized === "completed") return "border-emerald-200 bg-emerald-50 text-emerald-700";
  if (normalized === "running") return "border-sky-200 bg-sky-50 text-sky-700";
  if (normalized === "queued") return "border-violet-200 bg-violet-50 text-violet-700";
  if (normalized === "waiting_input" || normalized === "waiting_approval") {
    return "border-amber-200 bg-amber-50 text-amber-700";
  }
  if (normalized === "failed" || normalized === "cancelled" || normalized === "timed_out") {
    return "border-rose-200 bg-rose-50 text-rose-700";
  }
  return "border-slate-200 bg-slate-100 text-slate-600";
}

function workspaceRootFromTask(task: SubAgentTask) {
  const value = task.task_context?.workspace_root;
  return typeof value === "string" ? value : "";
}

function isManagedWorktree(path: string, defaultRoot: string) {
  if (!path || !defaultRoot) return false;
  return path.toLowerCase().startsWith(defaultRoot.toLowerCase());
}

function EmptyPanel({ text }: { text: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-stone-300 bg-stone-50 px-4 py-8 text-center text-sm text-stone-400">
      {text}
    </div>
  );
}

function SectionCard({
  title,
  description,
  icon: Icon,
  accentClass,
  children,
  action,
}: {
  title: string;
  description: string;
  icon: ElementType;
  accentClass: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-[28px] border border-stone-200 bg-white shadow-[0_18px_60px_-32px_rgba(28,25,23,0.35)]">
      <div className="border-b border-stone-200 bg-stone-50/90 px-5 py-4">
        <div className="flex items-start gap-3">
          <div className={clsx("rounded-2xl p-2.5", accentClass)}>
            <Icon className="h-5 w-5" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-semibold text-stone-800">{title}</h2>
            <p className="text-sm text-stone-500">{description}</p>
          </div>
          {action ? <div className="shrink-0">{action}</div> : null}
        </div>
      </div>
      <div className="space-y-4 p-5">{children}</div>
    </section>
  );
}

function MetricCard({
  icon: Icon,
  title,
  value,
  detail,
  tone,
}: {
  icon: ElementType;
  title: string;
  value: string | number;
  detail: string;
  tone: string;
}) {
  return (
    <div className="rounded-[24px] border border-stone-200 bg-white p-5 shadow-[0_16px_50px_-34px_rgba(28,25,23,0.45)]">
      <div className="mb-4 flex items-center gap-3">
        <div className={clsx("rounded-2xl p-2.5", tone)}>
          <Icon className="h-5 w-5" />
        </div>
        <div className="text-sm font-medium uppercase tracking-[0.18em] text-stone-500">{title}</div>
      </div>
      <div className="text-3xl font-semibold text-stone-900">{value}</div>
      <div className="mt-2 text-sm leading-relaxed text-stone-500">{detail}</div>
    </div>
  );
}

function TinyButton({
  children,
  onClick,
  busy,
  disabled,
  variant = "default",
}: {
  children: ReactNode;
  onClick: () => void;
  busy?: boolean;
  disabled?: boolean;
  variant?: "default" | "primary" | "danger";
}) {
  const className =
    variant === "primary"
      ? "bg-stone-900 text-white hover:bg-stone-800"
      : variant === "danger"
        ? "bg-rose-600 text-white hover:bg-rose-500"
        : "bg-stone-100 text-stone-700 hover:bg-stone-200";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy || disabled}
      className={clsx(
        "inline-flex items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium transition",
        className,
        (busy || disabled) && "cursor-not-allowed opacity-60"
      )}
    >
      {busy ? <RefreshCw className="h-4 w-4 animate-spin" /> : null}
      {children}
    </button>
  );
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={clsx("rounded-full border px-2.5 py-1 text-xs font-medium", getStatusClass(status))}>
      {formatStatusText(status)}
    </span>
  );
}

function StepCard({ step }: { step: RuntimeAgentStep }) {
  return (
    <div className="rounded-2xl border border-stone-200 bg-stone-50 px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={step.status} />
        <span className="rounded-full bg-white px-2.5 py-1 text-xs font-medium text-stone-500">
          {step.phase || "agent"}
        </span>
        <span className="ml-auto text-xs text-stone-400">{formatTimestamp(step.timestamp)}</span>
      </div>
      <div className="mt-2 text-sm font-semibold text-stone-800">{step.title || step.tool_name || "执行步骤"}</div>
      <div className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-stone-600">{step.detail || "-"}</div>
    </div>
  );
}

function ApprovalCard({
  approval,
  busy,
  onResolve,
}: {
  approval: RuntimeToolApproval;
  busy: boolean;
  onResolve: (approvalId: string, decision: "approved" | "rejected") => void;
}) {
  return (
    <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={approval.status} />
        <span className="rounded-full bg-white px-2.5 py-1 text-xs text-stone-500">{approval.tool_name}</span>
        <span className="ml-auto text-xs text-stone-400">{approval.created_at}</span>
      </div>
      <pre className="mt-3 overflow-auto rounded-xl bg-white/80 p-3 text-xs leading-relaxed text-stone-600">
        {JSON.stringify(approval.arguments || {}, null, 2)}
      </pre>
      <div className="mt-3 flex flex-wrap gap-2">
        <TinyButton busy={busy} onClick={() => onResolve(approval.approval_id, "approved")} variant="primary">
          批准
        </TinyButton>
        <TinyButton busy={busy} onClick={() => onResolve(approval.approval_id, "rejected")} variant="danger">
          拒绝
        </TinyButton>
      </div>
    </div>
  );
}

function TaskCard({
  task,
  busy,
  replyValue,
  onReplyChange,
  onContinue,
  onApprove,
  onReject,
  onCancel,
}: {
  task: SubAgentTask;
  busy: boolean;
  replyValue: string;
  onReplyChange: (value: string) => void;
  onContinue: () => void;
  onApprove: () => void;
  onReject: () => void;
  onCancel: () => void;
}) {
  const workspaceRoot = workspaceRootFromTask(task);
  return (
    <div className="rounded-2xl border border-stone-200 bg-stone-50 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={task.status} />
        <span className="rounded-full bg-white px-2.5 py-1 text-xs text-stone-500">{task.agent_type || "general"}</span>
        {task.priority_label ? (
          <span className="rounded-full bg-white px-2.5 py-1 text-xs text-stone-500">{task.priority_label}</span>
        ) : null}
        {task.cache_hit ? (
          <span className="rounded-full bg-emerald-100 px-2.5 py-1 text-xs text-emerald-700">cache hit</span>
        ) : null}
        <span className="ml-auto text-xs text-stone-400">{formatTimestamp(task.updated_at)}</span>
      </div>
      <div className="mt-3 text-sm font-semibold text-stone-800">{task.task}</div>
      <div className="mt-1 text-sm leading-relaxed text-stone-600">{task.status_message || task.result || task.error || "-"}</div>
      <div className="mt-3 flex flex-wrap gap-2 text-xs text-stone-500">
        <span className="rounded-full bg-white px-2.5 py-1">ID {task.task_id}</span>
        <span className="rounded-full bg-white px-2.5 py-1">tools {task.max_tool_calls}</span>
        <span className="rounded-full bg-white px-2.5 py-1">timeout {task.timeout_seconds}s</span>
        {workspaceRoot ? <span className="rounded-full bg-white px-2.5 py-1">workspace {workspaceRoot}</span> : null}
      </div>
      {task.awaiting?.question ? (
        <div className="mt-3 rounded-2xl border border-amber-200 bg-white px-3 py-3 text-sm text-stone-700">
          {task.awaiting.question}
        </div>
      ) : null}
      {task.status === "waiting_input" ? (
        <div className="mt-3 space-y-2">
          <textarea
            value={replyValue}
            onChange={(event) => onReplyChange(event.target.value)}
            rows={3}
            placeholder="输入继续该任务所需的回复..."
            className="w-full rounded-2xl border border-stone-200 bg-white px-3 py-2 text-sm text-stone-700 outline-none transition focus:border-stone-300 focus:ring-2 focus:ring-stone-200"
          />
          <TinyButton busy={busy} disabled={!replyValue.trim()} onClick={onContinue} variant="primary">
            继续任务
          </TinyButton>
        </div>
      ) : null}
      {task.status === "waiting_approval" ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <TinyButton busy={busy} onClick={onApprove} variant="primary">
            批准并继续
          </TinyButton>
          <TinyButton busy={busy} onClick={onReject} variant="danger">
            拒绝并继续
          </TinyButton>
        </div>
      ) : null}
      {task.status === "running" || task.status === "queued" ? (
        <div className="mt-3">
          <TinyButton busy={busy} onClick={onCancel}>
            取消任务
          </TinyButton>
        </div>
      ) : null}
    </div>
  );
}

function DiffButton({
  active,
  label,
  onClick,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        "rounded-full px-2.5 py-1 text-xs font-medium transition",
        active ? "bg-stone-900 text-white" : "bg-white text-stone-500 hover:bg-stone-100"
      )}
    >
      {label}
    </button>
  );
}

function RecentCallCard({ log }: { log: LLMCallLog }) {
  const lastMessage = log.messages[log.messages.length - 1];
  const preview = formatLLMLogMessageContent(lastMessage?.content).slice(0, 140) || "No message preview";

  return (
    <div className="rounded-2xl border border-stone-200 bg-stone-50 p-3">
      <div className="flex items-center gap-2">
        <span className="rounded-full bg-white px-2 py-1 text-xs text-stone-500">{log.caller}</span>
        <span className="rounded-full bg-white px-2 py-1 text-xs text-stone-500">{log.model_name || log.model_key}</span>
        <span className="ml-auto text-xs text-stone-400">{log.timestamp}</span>
      </div>
      <div className="mt-2 text-sm text-stone-700">
        {preview}
      </div>
      <div className="mt-2 text-xs text-stone-400">{formatDuration(log.duration_ms)}</div>
    </div>
  );
}

export default function Workbench() {
  const [data, setData] = useState<WorkbenchOverview | null>(null);
  const [error, setError] = useState("");
  const [banner, setBanner] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const [taskText, setTaskText] = useState("");
  const [taskAgentType, setTaskAgentType] = useState("general");
  const [taskModel, setTaskModel] = useState("");
  const [taskPriority, setTaskPriority] = useState("0");
  const [taskTimeout, setTaskTimeout] = useState("90");
  const [taskMaxTools, setTaskMaxTools] = useState("");
  const [taskUseCache, setTaskUseCache] = useState(true);
  const [selectedTaskWorkspace, setSelectedTaskWorkspace] = useState("");
  const [creatingTask, setCreatingTask] = useState(false);

  const [worktreeBranch, setWorktreeBranch] = useState("");
  const [worktreeBaseRef, setWorktreeBaseRef] = useState("HEAD");
  const [creatingWorktree, setCreatingWorktree] = useState(false);
  const [removingWorktreePath, setRemovingWorktreePath] = useState("");

  const [selectedDiff, setSelectedDiff] = useState<DiffSelection | null>(null);
  const [diffPreview, setDiffPreview] = useState<WorkbenchDiffPreview | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);

  const [replyDrafts, setReplyDrafts] = useState<Record<string, string>>({});
  const [taskActionId, setTaskActionId] = useState("");
  const [approvalActionId, setApprovalActionId] = useState("");

  const loadWorkbench = useCallback(async () => {
    setRefreshing(true);
    try {
      const response = await api.getWorkbench();
      setData(response);
      setError("");
      setSelectedTaskWorkspace((current) => {
        const worktrees = response.git.worktrees || [];
        if (current && worktrees.some((item) => item.path === current)) {
          return current;
        }
        return worktrees.find((item) => item.is_current)?.path || response.git.root || "";
      });
      setSelectedDiff((current) => {
        const changedFiles = response.git.changed_files || [];
        if (!changedFiles.length) {
          return null;
        }
        if (current && changedFiles.some((item) => item.path === current.path)) {
          return current;
        }
        const firstFile = changedFiles[0];
        return {
          path: firstFile.path,
          staged: firstFile.unstaged ? false : true,
        };
      });
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "加载工程工作台失败");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void loadWorkbench();
    const timer = window.setInterval(() => {
      void loadWorkbench();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [loadWorkbench]);

  useEffect(() => {
    if (!selectedDiff) {
      setDiffPreview(null);
      return;
    }
    let active = true;
    setDiffLoading(true);
    void api
      .getWorkbenchDiff(selectedDiff.path, selectedDiff.staged)
      .then((response) => {
        if (!active) return;
        setDiffPreview(response);
      })
      .catch((requestError) => {
        if (!active) return;
        setDiffPreview({
          path: selectedDiff.path,
          staged: selectedDiff.staged,
          patch: "",
          truncated: false,
          generated_at: "",
          error: requestError instanceof Error ? requestError.message : "Diff 读取失败",
        });
      })
      .finally(() => {
        if (active) {
          setDiffLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [selectedDiff]);

  const agentSteps = useMemo(
    () => [...(data?.runtime.agent_steps || [])].slice(-10).reverse(),
    [data?.runtime.agent_steps]
  );
  const pendingApprovals = useMemo(
    () => (data?.runtime.pending_tool_approvals || []).filter((item) => item.status === "pending"),
    [data?.runtime.pending_tool_approvals]
  );
  const taskList = useMemo(
    () =>
      [...(data?.subagents.tasks || [])].sort(
        (left, right) => Number(right.updated_at || 0) - Number(left.updated_at || 0)
      ),
    [data?.subagents.tasks]
  );
  const worktrees = data?.git.worktrees || [];
  const changedFiles = data?.git.changed_files || [];

  async function handleCreateTask() {
    const normalizedTask = taskText.trim();
    if (!normalizedTask) {
      setBanner("请输入要派发给子 Agent 的任务。");
      return;
    }

    setCreatingTask(true);
    try {
      const taskContext = selectedTaskWorkspace ? { workspace_root: selectedTaskWorkspace } : undefined;
      const parsedTaskMaxTools = taskMaxTools.trim() ? Number(taskMaxTools) || undefined : undefined;
      const response = await api.createSubAgent({
        task: normalizedTask,
        agent_type: taskAgentType,
        model: taskModel.trim() || undefined,
        max_tool_calls: parsedTaskMaxTools,
        timeout_seconds: Number(taskTimeout) || 90,
        priority: Number(taskPriority) || 0,
        use_cache: taskUseCache,
        task_context: taskContext,
      });
      setBanner(`已创建任务 ${response.task.task_id}。`);
      setTaskText("");
      await loadWorkbench();
    } catch (requestError) {
      setBanner(requestError instanceof Error ? requestError.message : "创建子 Agent 任务失败");
    } finally {
      setCreatingTask(false);
    }
  }

  async function handleResolveApproval(approvalId: string, decision: "approved" | "rejected") {
    setApprovalActionId(approvalId);
    try {
      await api.resolveToolApproval({ approval_id: approvalId, decision });
      setBanner(`审批 ${approvalId} 已${decision === "approved" ? "批准" : "拒绝"}。`);
      await loadWorkbench();
    } catch (requestError) {
      setBanner(requestError instanceof Error ? requestError.message : "审批处理失败");
    } finally {
      setApprovalActionId("");
    }
  }

  async function handleContinueTask(taskId: string, payload: { userReply?: string; approvalDecision?: "approved" | "rejected" }) {
    setTaskActionId(taskId);
    try {
      await api.continueSubAgent(taskId, {
        user_reply: payload.userReply || undefined,
        approval_decision: payload.approvalDecision,
      });
      setReplyDrafts((current) => {
        const next = { ...current };
        delete next[taskId];
        return next;
      });
      setBanner(`任务 ${taskId} 已继续。`);
      await loadWorkbench();
    } catch (requestError) {
      setBanner(requestError instanceof Error ? requestError.message : "继续子 Agent 失败");
    } finally {
      setTaskActionId("");
    }
  }

  async function handleCancelTask(taskId: string) {
    setTaskActionId(taskId);
    try {
      await api.cancelSubAgent(taskId, { reason: "Cancelled from Workbench" });
      setBanner(`已提交取消请求 ${taskId}。`);
      await loadWorkbench();
    } catch (requestError) {
      setBanner(requestError instanceof Error ? requestError.message : "取消任务失败");
    } finally {
      setTaskActionId("");
    }
  }

  async function handleCreateWorktree() {
    const normalizedBranch = worktreeBranch.trim();
    if (!normalizedBranch) {
      setBanner("请输入 worktree 分支名。");
      return;
    }
    setCreatingWorktree(true);
    try {
      const response = await api.createWorkbenchWorktree({
        branch: normalizedBranch,
        base_ref: worktreeBaseRef.trim() || undefined,
      });
      setBanner(response.message || `已创建 worktree ${normalizedBranch}。`);
      setWorktreeBranch("");
      if (response.worktree?.path) {
        setSelectedTaskWorkspace(response.worktree.path);
      }
      await loadWorkbench();
    } catch (requestError) {
      setBanner(requestError instanceof Error ? requestError.message : "创建 worktree 失败");
    } finally {
      setCreatingWorktree(false);
    }
  }

  async function handleRemoveWorktree(path: string) {
    setRemovingWorktreePath(path);
    try {
      const response = await api.removeWorkbenchWorktree({ path });
      setBanner(response.message || "已移除 worktree。");
      if (selectedTaskWorkspace === path) {
        setSelectedTaskWorkspace("");
      }
      await loadWorkbench();
    } catch (requestError) {
      setBanner(requestError instanceof Error ? requestError.message : "移除 worktree 失败");
    } finally {
      setRemovingWorktreePath("");
    }
  }

  return (
    <div className="flex h-full flex-col overflow-hidden bg-[radial-gradient(circle_at_top_left,_rgba(245,158,11,0.16),_transparent_34%),radial-gradient(circle_at_top_right,_rgba(14,165,233,0.12),_transparent_30%),linear-gradient(180deg,_#f7f4ef_0%,_#f3efe8_100%)]">
      <header className="border-b border-stone-200/80 bg-white/80 px-6 py-5 backdrop-blur">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-3xl">
            <div className="mb-2 inline-flex items-center gap-2 rounded-full bg-stone-900 px-3 py-1 text-xs font-medium uppercase tracking-[0.22em] text-stone-100">
              <Sparkles className="h-3.5 w-3.5" />
              Engineering Shell
            </div>
            <h1 className="text-2xl font-semibold text-stone-900">Workbench</h1>
            <p className="mt-1 text-sm leading-relaxed text-stone-600">
              把会话计划、子 Agent 编排、git 变更、worktree 隔离和 LLM 用量放进同一个工程驾驶舱。
            </p>
          </div>
          <TinyButton busy={refreshing} onClick={() => void loadWorkbench()}>
            <RefreshCw className="h-4 w-4" />
            刷新
          </TinyButton>
        </div>
      </header>

      {error ? (
        <div className="mx-6 mt-5 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {error}
        </div>
      ) : null}

      {banner ? (
        <div className="mx-6 mt-5 rounded-2xl border border-stone-200 bg-white px-4 py-3 text-sm text-stone-700 shadow-sm">
          {banner}
        </div>
      ) : null}

      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="space-y-6">
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-4">
            <MetricCard
              icon={GitBranch}
              title="Repository"
              value={data?.git.branch || (data?.git.detached ? "detached" : "-")}
              detail={
                data?.git.available
                  ? `${data?.git.changed_files.length || 0} files changed · ahead ${data?.git.ahead || 0} · behind ${data?.git.behind || 0}`
                  : data?.git.error || "Git unavailable"
              }
              tone="bg-amber-100 text-amber-700"
            />
            <MetricCard
              icon={Bot}
              title="Subagents"
              value={data?.subagents.count || 0}
              detail={`运行中 ${data?.subagents.active_count || 0} · 排队 ${data?.subagents.queued_count || 0} · 等待 ${data?.subagents.waiting_count || 0}`}
              tone="bg-sky-100 text-sky-700"
            />
            <MetricCard
              icon={ShieldAlert}
              title="Approvals"
              value={pendingApprovals.length}
              detail={
                data?.runtime.meta.agent_suspended
                  ? data.runtime.meta.agent_suspended_question || "主 Agent 当前已暂停，等待外部反馈。"
                  : "当前主 Agent 未暂停。"
              }
              tone="bg-rose-100 text-rose-700"
            />
            <MetricCard
              icon={Cpu}
              title="LLM Usage"
              value={data?.llm.usage.total_tokens || 0}
              detail={`calls ${data?.llm.total_calls || 0} · tracked ${data?.llm.usage.tracked_calls || 0} · avg ${formatDuration(data?.llm.average_duration_ms)}`}
              tone="bg-emerald-100 text-emerald-700"
            />
          </div>

          <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1.1fr,0.9fr]">
            <SectionCard
              title="Plan View"
              description="跟踪当前会话的可见计划、主 Agent 暂停点与最近执行轨迹。"
              icon={Layers3}
              accentClass="bg-amber-100 text-amber-700"
            >
              {data?.runtime.meta.agent_suspended ? (
                <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                  {data.runtime.meta.agent_suspended_question || "主 Agent 已暂停，等待回复。"}
                </div>
              ) : null}
              {agentSteps.length ? agentSteps.map((step) => <StepCard key={step.id} step={step} />) : <EmptyPanel text="当前没有可见的计划步骤。" />}
            </SectionCard>

            <SectionCard
              title="Task Deck"
              description="在选定 worktree 中派发子 Agent 任务，并直接处理等待输入或等待审批的任务。"
              icon={Wrench}
              accentClass="bg-sky-100 text-sky-700"
            >
              <div className="rounded-[24px] border border-stone-200 bg-stone-50 p-4">
                <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
                  <textarea
                    value={taskText}
                    onChange={(event) => setTaskText(event.target.value)}
                    rows={4}
                    placeholder="例如：审查当前改动是否会引入路径越界问题，并给出修复建议"
                    className="w-full rounded-2xl border border-stone-200 bg-white px-3 py-2 text-sm text-stone-700 outline-none transition focus:border-stone-300 focus:ring-2 focus:ring-stone-200 xl:col-span-2"
                  />
                  <select
                    value={taskAgentType}
                    onChange={(event) => setTaskAgentType(event.target.value)}
                    className="rounded-2xl border border-stone-200 bg-white px-3 py-2 text-sm text-stone-700 outline-none focus:border-stone-300"
                  >
                    {AGENT_TYPE_OPTIONS.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                  <select
                    value={selectedTaskWorkspace}
                    onChange={(event) => setSelectedTaskWorkspace(event.target.value)}
                    className="rounded-2xl border border-stone-200 bg-white px-3 py-2 text-sm text-stone-700 outline-none focus:border-stone-300"
                  >
                    {(worktrees.length ? worktrees : [{ path: data?.git.root || "", branch: "primary" } as WorkbenchWorktree]).map((worktree) => (
                      <option key={worktree.path} value={worktree.path}>
                        {worktree.branch || "detached"} · {worktree.path}
                      </option>
                    ))}
                  </select>
                  <input
                    value={taskModel}
                    onChange={(event) => setTaskModel(event.target.value)}
                    placeholder="模型名（可选）"
                    className="rounded-2xl border border-stone-200 bg-white px-3 py-2 text-sm text-stone-700 outline-none focus:border-stone-300"
                  />
                  <input
                    value={taskPriority}
                    onChange={(event) => setTaskPriority(event.target.value)}
                    placeholder="优先级"
                    className="rounded-2xl border border-stone-200 bg-white px-3 py-2 text-sm text-stone-700 outline-none focus:border-stone-300"
                  />
                  <input
                    value={taskTimeout}
                    onChange={(event) => setTaskTimeout(event.target.value)}
                    placeholder="超时秒数"
                    className="rounded-2xl border border-stone-200 bg-white px-3 py-2 text-sm text-stone-700 outline-none focus:border-stone-300"
                  />
                  <input
                    value={taskMaxTools}
                    onChange={(event) => setTaskMaxTools(event.target.value)}
                    placeholder="最大工具调用"
                    className="rounded-2xl border border-stone-200 bg-white px-3 py-2 text-sm text-stone-700 outline-none focus:border-stone-300"
                  />
                </div>
                <label className="mt-3 flex items-center gap-2 text-sm text-stone-600">
                  <input
                    type="checkbox"
                    checked={taskUseCache}
                    onChange={(event) => setTaskUseCache(event.target.checked)}
                    className="h-4 w-4 rounded border-stone-300 text-stone-900 focus:ring-stone-300"
                  />
                  启用结果缓存
                </label>
                <div className="mt-4">
                  <TinyButton busy={creatingTask} onClick={handleCreateTask} variant="primary">
                    <Play className="h-4 w-4" />
                    派发任务
                  </TinyButton>
                </div>
              </div>

              {taskList.length ? (
                taskList.slice(0, 8).map((task) => (
                  <TaskCard
                    key={task.task_id}
                    task={task}
                    busy={taskActionId === task.task_id}
                    replyValue={replyDrafts[task.task_id] || ""}
                    onReplyChange={(value) =>
                      setReplyDrafts((current) => ({
                        ...current,
                        [task.task_id]: value,
                      }))
                    }
                    onContinue={() =>
                      void handleContinueTask(task.task_id, {
                        userReply: replyDrafts[task.task_id] || "",
                      })
                    }
                    onApprove={() => void handleContinueTask(task.task_id, { approvalDecision: "approved" })}
                    onReject={() => void handleContinueTask(task.task_id, { approvalDecision: "rejected" })}
                    onCancel={() => void handleCancelTask(task.task_id)}
                  />
                ))
              ) : (
                <EmptyPanel text="当前还没有子 Agent 任务。" />
              )}
            </SectionCard>
          </div>

          <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1.05fr,0.95fr]">
            <SectionCard
              title="Git Change Lens"
              description="查看工作区变更、选择 staged 或 unstaged patch，并快速判断改动范围。"
              icon={FileText}
              accentClass="bg-emerald-100 text-emerald-700"
            >
              {data?.git.available ? (
                <>
                  <div className="flex flex-wrap gap-2 text-xs text-stone-500">
                    <span className="rounded-full bg-stone-100 px-2.5 py-1">staged {data.git.staged_count}</span>
                    <span className="rounded-full bg-stone-100 px-2.5 py-1">unstaged {data.git.unstaged_count}</span>
                    <span className="rounded-full bg-stone-100 px-2.5 py-1">untracked {data.git.untracked_count}</span>
                    <span className="rounded-full bg-stone-100 px-2.5 py-1">
                      +{data.git.unstaged_diff.additions + data.git.staged_diff.additions} / -
                      {data.git.unstaged_diff.deletions + data.git.staged_diff.deletions}
                    </span>
                  </div>

                  <div className="grid grid-cols-1 gap-4 xl:grid-cols-[0.95fr,1.05fr]">
                    <div className="space-y-3">
                      {changedFiles.length ? (
                        changedFiles.map((file) => (
                          <div key={file.display_path} className="rounded-2xl border border-stone-200 bg-stone-50 p-3">
                            <div className="font-mono text-xs text-stone-700">{file.display_path}</div>
                            <div className="mt-2 flex flex-wrap gap-2">
                              {file.unstaged ? (
                                <DiffButton
                                  label="worktree"
                                  active={selectedDiff?.path === file.path && !selectedDiff?.staged}
                                  onClick={() => setSelectedDiff({ path: file.path, staged: false })}
                                />
                              ) : null}
                              {file.staged ? (
                                <DiffButton
                                  label="staged"
                                  active={selectedDiff?.path === file.path && !!selectedDiff?.staged}
                                  onClick={() => setSelectedDiff({ path: file.path, staged: true })}
                                />
                              ) : null}
                              {file.untracked ? <span className="rounded-full bg-white px-2.5 py-1 text-xs text-stone-500">untracked</span> : null}
                            </div>
                          </div>
                        ))
                      ) : (
                        <EmptyPanel text="当前工作区没有 git 变更。" />
                      )}
                    </div>

                    <div className="rounded-[24px] border border-stone-200 bg-stone-950 p-4 text-stone-100">
                      <div className="mb-3 flex items-center gap-2 text-sm text-stone-300">
                        <FileText className="h-4 w-4" />
                        <span>{selectedDiff ? `${selectedDiff.path} · ${selectedDiff.staged ? "staged" : "worktree"}` : "选择一个文件查看 diff"}</span>
                      </div>
                      {diffLoading ? (
                        <div className="flex items-center gap-2 text-sm text-stone-300">
                          <RefreshCw className="h-4 w-4 animate-spin" />
                          正在读取 diff...
                        </div>
                      ) : diffPreview?.error ? (
                        <div className="text-sm text-rose-300">{diffPreview.error}</div>
                      ) : diffPreview?.patch ? (
                        <pre className="max-h-[420px] overflow-auto whitespace-pre-wrap break-words text-xs leading-relaxed">
                          {diffPreview.patch}
                        </pre>
                      ) : (
                        <div className="text-sm text-stone-400">当前选择没有可显示的 patch。</div>
                      )}
                    </div>
                  </div>
                </>
              ) : (
                <EmptyPanel text={data?.git.error || "当前工作区不可用。"} />
              )}
            </SectionCard>

            <SectionCard
              title="Worktree Bay"
              description="管理隔离工作目录，并把新任务直接派发到对应 worktree。"
              icon={GitBranch}
              accentClass="bg-indigo-100 text-indigo-700"
            >
              <div className="rounded-[24px] border border-stone-200 bg-stone-50 p-4">
                <div className="grid grid-cols-1 gap-3 xl:grid-cols-[1fr,0.7fr]">
                  <input
                    value={worktreeBranch}
                    onChange={(event) => setWorktreeBranch(event.target.value)}
                    placeholder="新分支名，例如 codex/agent-shell"
                    className="rounded-2xl border border-stone-200 bg-white px-3 py-2 text-sm text-stone-700 outline-none focus:border-stone-300"
                  />
                  <input
                    value={worktreeBaseRef}
                    onChange={(event) => setWorktreeBaseRef(event.target.value)}
                    placeholder="base ref"
                    className="rounded-2xl border border-stone-200 bg-white px-3 py-2 text-sm text-stone-700 outline-none focus:border-stone-300"
                  />
                </div>
                <div className="mt-4">
                  <TinyButton busy={creatingWorktree} onClick={handleCreateWorktree} variant="primary">
                    <Plus className="h-4 w-4" />
                    创建 worktree
                  </TinyButton>
                </div>
              </div>

              {worktrees.length ? (
                worktrees.map((worktree) => (
                  <div key={worktree.path} className="rounded-2xl border border-stone-200 bg-stone-50 p-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rounded-full bg-white px-2.5 py-1 text-xs font-medium text-stone-600">
                        {worktree.branch || "detached"}
                      </span>
                      {worktree.is_current ? (
                        <span className="rounded-full bg-emerald-100 px-2.5 py-1 text-xs text-emerald-700">current</span>
                      ) : null}
                      {worktree.locked ? (
                        <span className="rounded-full bg-amber-100 px-2.5 py-1 text-xs text-amber-700">locked</span>
                      ) : null}
                      <span className="ml-auto text-xs text-stone-400">{worktree.head.slice(0, 10)}</span>
                    </div>
                    <div className="mt-2 font-mono text-xs text-stone-600">{worktree.path}</div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <TinyButton onClick={() => setSelectedTaskWorkspace(worktree.path)}>设为任务工作区</TinyButton>
                      {isManagedWorktree(worktree.path, data?.git.default_worktree_root || "") && !worktree.is_current ? (
                        <TinyButton
                          busy={removingWorktreePath === worktree.path}
                          onClick={() => void handleRemoveWorktree(worktree.path)}
                          variant="danger"
                        >
                          <Trash2 className="h-4 w-4" />
                          移除
                        </TinyButton>
                      ) : null}
                    </div>
                  </div>
                ))
              ) : (
                <EmptyPanel text="当前仓库还没有额外 worktree。" />
              )}
            </SectionCard>
          </div>

          <div className="grid grid-cols-1 gap-6 xl:grid-cols-[0.95fr,1.05fr]">
            <SectionCard
              title="Approval Queue"
              description="集中处理工具审批，避免主 Agent 或子 Agent 在中途卡住。"
              icon={AlertTriangle}
              accentClass="bg-amber-100 text-amber-700"
            >
              {pendingApprovals.length ? (
                pendingApprovals.map((approval) => (
                  <ApprovalCard
                    key={approval.approval_id}
                    approval={approval}
                    busy={approvalActionId === approval.approval_id}
                    onResolve={(approvalId, decision) => void handleResolveApproval(approvalId, decision)}
                  />
                ))
              ) : (
                <EmptyPanel text="当前没有待处理的工具审批。" />
              )}
            </SectionCard>

            <SectionCard
              title="LLM Pulse"
              description="汇总调用量、Token 用量和最近的模型调用轨迹。"
              icon={Cpu}
              accentClass="bg-emerald-100 text-emerald-700"
            >
              <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                <div className="rounded-2xl border border-stone-200 bg-stone-50 p-4">
                  <div className="text-sm font-medium text-stone-500">Token 使用</div>
                  <div className="mt-3 text-2xl font-semibold text-stone-900">{data?.llm.usage.total_tokens || 0}</div>
                  <div className="mt-2 space-y-1 text-sm text-stone-600">
                    <div>prompt {data?.llm.usage.prompt_tokens || 0}</div>
                    <div>completion {data?.llm.usage.completion_tokens || 0}</div>
                    <div>tracked calls {data?.llm.usage.tracked_calls || 0}</div>
                  </div>
                </div>
                <div className="rounded-2xl border border-stone-200 bg-stone-50 p-4">
                  <div className="text-sm font-medium text-stone-500">Cost View</div>
                  <div className="mt-3 text-2xl font-semibold text-stone-900">
                    {data?.llm.cost_estimate.available ? `$${data.llm.cost_estimate.amount_usd}` : "Not tracked"}
                  </div>
                  <div className="mt-2 text-sm text-stone-600">
                    {data?.llm.cost_estimate.available
                      ? "来自每次调用返回的成本元数据。"
                      : data?.llm.cost_estimate.reason || "当前还没有价格表或成本采集。"}
                  </div>
                </div>
              </div>

              <div className="flex flex-wrap gap-2 text-xs text-stone-500">
                {(data?.llm.callers || []).slice(0, 6).map((caller) => (
                  <span key={caller.name} className="rounded-full bg-stone-100 px-2.5 py-1">
                    {caller.name} {caller.count}
                  </span>
                ))}
              </div>

              {data?.llm.recent_calls.length ? (
                data.llm.recent_calls.map((log) => <RecentCallCard key={log.id} log={log} />)
              ) : (
                <EmptyPanel text="当前还没有记录到 LLM 调用。" />
              )}
            </SectionCard>
          </div>
        </div>
      </div>
    </div>
  );
}
