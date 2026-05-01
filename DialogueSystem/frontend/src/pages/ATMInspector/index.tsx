import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  RefreshCw,
  Zap,
  Clock,
  CheckCircle2,
  XCircle,
  PauseCircle,
  ArrowRightLeft,
  FileText,
  Calendar,
  Activity,
  Timer,
} from "lucide-react";
import clsx from "clsx";

import { LLMLogEntryCard } from "@/components/LLMLog/LogEntryCard";
import { api } from "@/services/api";
import type {
  LLMCallLog,
  ATMSession,
  ATMTask,
  ATMAttempt,
} from "@/types";

// ---- Style maps ----

const CALLER_COLORS: Record<string, string> = {
  plan: "bg-violet-100 text-violet-700 border-violet-200",
  exec: "bg-sky-100 text-sky-700 border-sky-200",
  summary: "bg-emerald-100 text-emerald-700 border-emerald-200",
  share_score: "bg-rose-100 text-rose-700 border-rose-200",
  memory_conflict: "bg-fuchsia-100 text-fuchsia-700 border-fuchsia-200",
};
const EXEC_CALLER_COLORS = [
  "bg-sky-100 text-sky-700 border-sky-200",
  "bg-cyan-100 text-cyan-700 border-cyan-200",
  "bg-emerald-100 text-emerald-700 border-emerald-200",
  "bg-lime-100 text-lime-700 border-lime-200",
  "bg-amber-100 text-amber-700 border-amber-200",
  "bg-orange-100 text-orange-700 border-orange-200",
  "bg-rose-100 text-rose-700 border-rose-200",
  "bg-pink-100 text-pink-700 border-pink-200",
  "bg-fuchsia-100 text-fuchsia-700 border-fuchsia-200",
  "bg-violet-100 text-violet-700 border-violet-200",
];

const STATUS_CFG: Record<string, { icon: typeof CheckCircle2; color: string }> = {
  completed: { icon: CheckCircle2, color: "text-emerald-500" },
  failed: { icon: XCircle, color: "text-red-500" },
  paused: { icon: PauseCircle, color: "text-amber-500" },
  timed_out: { icon: Timer, color: "text-amber-600" },
  cancelled: { icon: PauseCircle, color: "text-slate-500" },
  stale: { icon: Clock, color: "text-orange-500" },
  interrupt_requested: { icon: PauseCircle, color: "text-rose-500" },
  pending: { icon: Clock, color: "text-slate-400" },
  carried_over: { icon: ArrowRightLeft, color: "text-violet-500" },
  running: { icon: Activity, color: "text-sky-500" },
};

const FINISH_STYLES: Record<string, string> = {
  all_completed: "bg-emerald-100 text-emerald-700",
  partial: "bg-amber-100 text-amber-700",
  token_limit: "bg-red-100 text-red-700",
  interrupted: "bg-rose-100 text-rose-700",
};

function hashText(value: string): number {
  let hash = 0;
  for (let i = 0; i < value.length; i += 1) {
    hash = (hash * 33 + value.charCodeAt(i)) >>> 0;
  }
  return hash;
}

function callerColor(c: string) {
  const parts = c.replace("autonomous_task.", "").split(".");
  const phase = parts[0] ?? "";
  if (phase === "exec") {
    const taskKey = parts[1] ?? parts.slice(1).join(".") ?? "";
    if (taskKey) {
      return EXEC_CALLER_COLORS[hashText(taskKey) % EXEC_CALLER_COLORS.length];
    }
  }
  return CALLER_COLORS[phase] ?? "bg-amber-100 text-amber-700 border-amber-200";
}

function callerLabel(c: string): string {
  const parts = c.replace("autonomous_task.", "").split(".");
  const phase = parts[0] ?? "";
  const m: Record<string, string> = { plan: "计划生成", exec: "任务执行", summary: "会话总结", share_score: "分享评分", memory_conflict: "记忆冲突" };
  const rest = parts.slice(1).join(".");
  return rest ? `${m[phase] ?? phase} [${rest}]` : (m[phase] ?? phase);
}

function tokenStr(input: number, output: number): string {
  return `${input} / ${output} (${input + output})`;
}

// ---- Session Card ----

function SessionCard({ session }: { session: ATMSession }) {
  const fs = FINISH_STYLES[session.finish_reason] ?? "bg-slate-100 text-slate-600";
  const total = (session.total_input_tokens ?? 0) + (session.total_output_tokens ?? 0);
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <Calendar className="h-5 w-5 text-amber-500" />
        <h2 className="text-lg font-bold text-slate-800">{session.session_date}</h2>
        {session.finish_reason && (
          <span className={clsx("rounded-full px-2.5 py-0.5 text-xs font-semibold", fs)}>
            {session.finish_reason.replace(/_/g, " ")}
          </span>
        )}
        {session.interrupt_count > 0 && (
          <span className="rounded-full bg-rose-100 px-2 py-0.5 text-xs font-medium text-rose-600">
            {session.interrupt_count} 次中断
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-x-8 gap-y-2 text-sm sm:grid-cols-4">
        <StatCell label="计划任务" value={session.tasks_planned} />
        <StatCell label="已完成" value={session.tasks_completed} color="text-emerald-600" />
        <StatCell label="已结转" value={session.tasks_carried_over} color="text-violet-600" />
        <div>
          <span className="text-slate-400">Token 用量</span>
          <p className="font-semibold text-slate-700">
            <span className="text-sky-600">{session.total_input_tokens ?? 0}</span>
            <span className="text-slate-300"> / </span>
            <span className="text-emerald-600">{session.total_output_tokens ?? 0}</span>
            <span className="ml-1 text-xs text-slate-400">({total})</span>
          </p>
        </div>
      </div>
      <div className="mt-3 flex gap-6 text-xs text-slate-400">
        {session.plan_generated_at && <span>计划生成: {session.plan_generated_at}</span>}
        {session.session_finished_at && <span>完成: {session.session_finished_at}</span>}
      </div>
    </div>
  );
}

function StatCell({ label, value, color }: { label: string; value: number; color?: string }) {
  return (
    <div>
      <span className="text-slate-400">{label}</span>
      <p className={clsx("font-semibold", color ?? "text-slate-700")}>{value}</p>
    </div>
  );
}

// ---- Task Card ----

function TaskCard({ task, attempts }: { task: ATMTask; attempts: ATMAttempt[] }) {
  const [open, setOpen] = useState(false);
  const cfg = STATUS_CFG[task.status] ?? STATUS_CFG.pending;
  const Icon = cfg.icon;
  const taskAttempts = attempts.filter((a) => a.task_id === task.id);

  return (
    <div className="rounded-lg border border-slate-200 bg-white shadow-sm">
      <button
        type="button"
        className="flex w-full items-start gap-3 p-4 text-left hover:bg-slate-50"
        onClick={() => setOpen((v) => !v)}
      >
        {open ? <ChevronDown className="mt-0.5 h-4 w-4 text-slate-400 shrink-0" /> : <ChevronRight className="mt-0.5 h-4 w-4 text-slate-400 shrink-0" />}
        <Icon className={clsx("mt-0.5 h-4 w-4 shrink-0", cfg.color)} />
        <div className="min-w-0 flex-1">
          <p className="font-medium text-slate-800 line-clamp-2">{task.task_content}</p>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-400">
            <span className={clsx("rounded-full px-2 py-0.5 font-medium", cfg.color, "bg-opacity-10")}>{task.status}</span>
            <span>来源: {task.source}</span>
            <span>尝试: {task.attempt_count}</span>
            {task.token_usage_input + task.token_usage_output > 0 && (
              <span>Token: {tokenStr(task.token_usage_input, task.token_usage_output)}</span>
            )}
            {task.carry_over_from_date && (
              <span className="text-violet-500">结转自 {task.carry_over_from_date}</span>
            )}
          </div>
        </div>
      </button>

      {open && (
        <div className="border-t border-slate-100 px-4 pb-4 pt-3 space-y-3">
          {task.expected_goal && (
            <div>
              <h4 className="text-xs font-semibold text-slate-500 mb-1">预期目标</h4>
              <p className="text-sm text-slate-700 whitespace-pre-wrap">{task.expected_goal}</p>
            </div>
          )}

          {task.execution_log && (
            <div>
              <h4 className="text-xs font-semibold text-slate-500 mb-1">执行日志</h4>
              <pre className="max-h-60 overflow-auto rounded-md bg-slate-50 p-3 text-xs text-slate-700 whitespace-pre-wrap">{task.execution_log}</pre>
            </div>
          )}

          {task.pause_reason && (
            <div>
              <h4 className="text-xs font-semibold text-amber-600 mb-1">暂停原因</h4>
              <p className="text-sm text-amber-700">{task.pause_reason}</p>
            </div>
          )}

          {taskAttempts.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold text-slate-500 mb-2">执行尝试 ({taskAttempts.length})</h4>
              <div className="space-y-2">
                {taskAttempts.map((a) => (
                  <AttemptRow key={a.attempt_id} attempt={a} />
                ))}
              </div>
            </div>
          )}

          <div className="flex flex-wrap gap-4 text-xs text-slate-400 pt-1">
            {task.started_at && <span>开始: {task.started_at}</span>}
            {task.completed_at && <span>完成: {task.completed_at}</span>}
            {task.paused_at && <span>暂停: {task.paused_at}</span>}
          </div>
        </div>
      )}
    </div>
  );
}

function AttemptRow({ attempt }: { attempt: ATMAttempt }) {
  const [open, setOpen] = useState(false);
  const cfg = STATUS_CFG[attempt.status] ?? STATUS_CFG.pending;
  const Icon = cfg.icon;
  const normalizedResultSummary = String(attempt.result_summary ?? "").trim();
  const normalizedErrorMessage = String(attempt.error_message ?? "").trim();
  const showResultSummary = normalizedResultSummary && normalizedResultSummary !== normalizedErrorMessage;

  return (
    <div className="rounded-md border border-slate-100 bg-slate-50/50">
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs hover:bg-slate-100"
        onClick={() => setOpen((v) => !v)}
      >
        {open ? <ChevronDown className="h-3 w-3 text-slate-400" /> : <ChevronRight className="h-3 w-3 text-slate-400" />}
        <Icon className={clsx("h-3.5 w-3.5", cfg.color)} />
        <span className="font-mono text-slate-500">{attempt.attempt_id.slice(0, 8)}</span>
        <span className={clsx("font-medium", cfg.color)}>{attempt.status}</span>
        {attempt.input_tokens + attempt.output_tokens > 0 && (
          <span className="text-slate-400 ml-auto">Token: {tokenStr(attempt.input_tokens, attempt.output_tokens)}</span>
        )}
      </button>
      {open && (
        <div className="border-t border-slate-100 px-3 py-2 space-y-1 text-xs">
          {showResultSummary && (
            <div>
              <span className="font-semibold text-slate-500">结果: </span>
              <span className="text-slate-700">{normalizedResultSummary}</span>
            </div>
          )}
          {normalizedErrorMessage && (
            <div>
              <span className="font-semibold text-red-500">错误: </span>
              <span className="text-red-700">{normalizedErrorMessage}</span>
            </div>
          )}
          <div className="flex flex-wrap gap-4 text-slate-400">
            <span>开始: {attempt.started_at}</span>
            {attempt.finished_at && <span>完成: {attempt.finished_at}</span>}
            {attempt.subagent_task_id && <span>SubAgent: {attempt.subagent_task_id.slice(0, 8)}</span>}
          </div>
        </div>
      )}
    </div>
  );
}

// ---- Phase filter chips ----

const PHASE_OPTIONS = [
  { key: "", label: "全部" },
  { key: "plan", label: "计划生成" },
  { key: "exec", label: "任务执行" },
  { key: "summary", label: "会话总结" },
  { key: "share_score", label: "分享评分" },
  { key: "memory_conflict", label: "记忆冲突" },
];

// ---- Main Page ----

type TabKey = "overview" | "llm-logs";

export default function ATMInspector() {
  const [tab, setTab] = useState<TabKey>("overview");
  const [dateFilter, setDateFilter] = useState(() => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  });

  // Overview data
  const [sessions, setSessions] = useState<ATMSession[]>([]);
  const [tasks, setTasks] = useState<ATMTask[]>([]);
  const [attempts, setAttempts] = useState<ATMAttempt[]>([]);
  const [overviewLoading, setOverviewLoading] = useState(false);

  // LLM log data
  const [logs, setLogs] = useState<LLMCallLog[]>([]);
  const [logLoading, setLogLoading] = useState(false);
  const [phaseFilter, setPhaseFilter] = useState("");

  const fetchOverview = useCallback(async () => {
    setOverviewLoading(true);
    try {
      const data = await api.getATMSessions(dateFilter || undefined);
      setSessions(data.sessions ?? []);
      setTasks(data.tasks ?? []);
      setAttempts(data.attempts ?? []);
    } catch {
      /* ignore */
    } finally {
      setOverviewLoading(false);
    }
  }, [dateFilter]);

  const fetchLogs = useCallback(async () => {
    setLogLoading(true);
    try {
      const data = await api.getATMLLMLogs();
      setLogs(data.logs ?? []);
    } catch {
      /* ignore */
    } finally {
      setLogLoading(false);
    }
  }, []);

  useEffect(() => {
    if (tab === "overview") fetchOverview();
    else fetchLogs();
  }, [tab, fetchOverview, fetchLogs]);

  const filteredLogs = useMemo(() => {
    if (!phaseFilter) return logs;
    return logs.filter((l) => l.caller.replace("autonomous_task.", "").startsWith(phaseFilter));
  }, [logs, phaseFilter]);

  const isLoading = tab === "overview" ? overviewLoading : logLoading;
  const refresh = tab === "overview" ? fetchOverview : fetchLogs;

  return (
    <div className="h-full overflow-y-auto"><div className="mx-auto max-w-5xl space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Zap className="h-6 w-6 text-amber-500" />
          <h1 className="text-2xl font-bold text-slate-800">ATM Inspector</h1>
        </div>
        <button
          type="button"
          onClick={refresh}
          disabled={isLoading}
          className="flex items-center gap-2 rounded-lg bg-slate-100 px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-200 disabled:opacity-50"
        >
          <RefreshCw className={clsx("h-4 w-4", isLoading && "animate-spin")} />
          刷新
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 rounded-lg bg-slate-100 p-1">
        {([["overview", "概览"], ["llm-logs", "LLM 日志"]] as const).map(([key, label]) => (
          <button
            key={key}
            type="button"
            className={clsx(
              "flex-1 rounded-md px-4 py-2 text-sm font-medium transition-colors",
              tab === key ? "bg-white text-slate-800 shadow-sm" : "text-slate-500 hover:text-slate-700"
            )}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {tab === "overview" && (
        <div className="space-y-6">
          {/* Date picker */}
          <div className="flex items-center gap-3">
            <label className="text-sm font-medium text-slate-600">日期</label>
            <input
              type="date"
              value={dateFilter}
              onChange={(e) => setDateFilter(e.target.value)}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700 shadow-sm focus:border-amber-400 focus:outline-none focus:ring-1 focus:ring-amber-400"
            />
            <button
              type="button"
              onClick={() => setDateFilter("")}
              className="text-xs text-slate-400 hover:text-slate-600 underline"
            >
              全部日期
            </button>
          </div>

          {/* Sessions */}
          {sessions.length > 0 && (
            <section>
              <h2 className="mb-3 text-sm font-semibold text-slate-500 uppercase tracking-wider">会话</h2>
              <div className="space-y-4">
                {sessions.map((s) => (
                  <SessionCard key={s.id} session={s} />
                ))}
              </div>
            </section>
          )}

          {/* Tasks */}
          {tasks.length > 0 && (
            <section>
              <h2 className="mb-3 text-sm font-semibold text-slate-500 uppercase tracking-wider">任务 ({tasks.length})</h2>
              <div className="space-y-3">
                {tasks.map((t) => (
                  <TaskCard key={t.id} task={t} attempts={attempts} />
                ))}
              </div>
            </section>
          )}

          {!overviewLoading && sessions.length === 0 && tasks.length === 0 && (
            <div className="py-16 text-center text-slate-400">
              <FileText className="mx-auto mb-3 h-10 w-10" />
              <p>暂无 ATM 会话数据</p>
              {dateFilter && <p className="mt-1 text-xs">尝试选择其他日期或查看全部日期</p>}
            </div>
          )}
        </div>
      )}

      {tab === "llm-logs" && (
        <div className="space-y-4">
          {/* Phase filter */}
          <div className="flex flex-wrap gap-2">
            {PHASE_OPTIONS.map((opt) => (
              <button
                key={opt.key}
                type="button"
                className={clsx(
                  "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                  phaseFilter === opt.key
                    ? "border-amber-300 bg-amber-50 text-amber-700"
                    : "border-slate-200 bg-white text-slate-500 hover:border-slate-300"
                )}
                onClick={() => setPhaseFilter(opt.key)}
              >
                {opt.label}
              </button>
            ))}
            <span className="ml-auto self-center text-xs text-slate-400">
              {filteredLogs.length} / {logs.length} 条记录
            </span>
          </div>

          {/* Log entries */}
          <div className="space-y-3">
            {filteredLogs.map((log) => (
              <LLMLogEntryCard
                key={log.id}
                log={log}
                getCallerBadgeClass={(caller) => callerColor(caller)}
                getCallerLabel={(caller) => callerLabel(caller)}
              />
            ))}
          </div>

          {!logLoading && logs.length === 0 && (
            <div className="py-16 text-center text-slate-400">
              <FileText className="mx-auto mb-3 h-10 w-10" />
              <p>暂无 ATM LLM 调用记录</p>
              <p className="mt-1 text-xs">ATM 运行后，LLM 调用记录将显示在这里</p>
            </div>
          )}
        </div>
      )}
    </div></div>
  );
}
