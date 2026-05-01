import { type ElementType, type ReactNode, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Archive,
  Bot,
  BookOpen,
  Brain,
  Database,
  ExternalLink,
  Globe,
  Heart,
  Layers3,
  Network,
  Plus,
  RefreshCw,
  Search,
  ShieldAlert,
  Sparkles,
  Wrench,
  Zap,
} from "lucide-react";
import clsx from "clsx";

import { api } from "@/services/api";
import type {
  BrowserExtractPageResult,
  BrowserReadLinkedPageResult,
  BrowserStatusResult,
  LongTermMemoryResult,
  ManagedSkill,
  MCPToolSpec,
  RuntimeAtomicMemoryRecord,
  RuntimeContextMessage,
  RuntimeMemoryLayerBase,
  RuntimeMemorySection,
  RuntimeState,
  RuntimeToolApproval,
  RuntimeToolSecurityEvent,
  RuntimeTopicArchiveRecord,
  SkillDiagnostic,
  SubAgentBatchResult,
  SubAgentTask,
} from "@/types";

function formatTimestamp(value?: string | number | null) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  if (typeof value === "number") {
    return new Date(value * 1000).toLocaleString();
  }
  return value;
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
  return status;
}

function isTerminalSubAgentStatus(status: string) {
  const normalized = String(status || "").toLowerCase();
  return normalized === "completed" || normalized === "failed" || normalized === "cancelled" || normalized === "timed_out";
}

function EmptyBlock({ text }: { text: string }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-center text-sm text-slate-400">
      {text}
    </div>
  );
}

function InfoPill({ label, value }: { label: string; value: string | number | boolean | null | undefined }) {
  return (
    <div className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs text-slate-600">
      <span className="font-medium text-slate-500">{label}:</span> {String(value ?? "-")}
    </div>
  );
}

function SectionTitle({ icon: Icon, title, description }: { icon: ElementType; title: string; description?: string }) {
  return (
    <div className="mb-3 flex items-start gap-3">
      <div className="mt-0.5 rounded-xl bg-slate-100 p-2 text-slate-600">
        <Icon className="h-5 w-5" />
      </div>
      <div>
        <h2 className="text-lg font-semibold text-slate-800">{title}</h2>
        {description ? <p className="text-sm text-slate-500">{description}</p> : null}
      </div>
    </div>
  );
}

function PanelCard({
  title,
  icon: Icon,
  colorClass,
  children,
  action,
}: {
  title: string;
  icon: ElementType;
  colorClass: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className={clsx("flex items-center gap-2 border-b border-slate-200 px-4 py-3", colorClass)}>
        <Icon className="h-5 w-5" />
        <h3 className="font-semibold">{title}</h3>
        {action ? <div className="ml-auto">{action}</div> : null}
      </div>
      <div className="space-y-4 p-4">{children}</div>
    </section>
  );
}

function TextInput({
  value,
  onChange,
  placeholder,
  rows = 3,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  rows?: number;
}) {
  return (
    <textarea
      value={value}
      onChange={(event) => onChange(event.target.value)}
      placeholder={placeholder}
      rows={rows}
      className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700 outline-none transition focus:border-slate-300 focus:bg-white focus:ring-2 focus:ring-slate-200"
    />
  );
}

function InlineInput({
  value,
  onChange,
  placeholder,
  type = "text",
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  type?: string;
}) {
  return (
    <input
      value={value}
      onChange={(event) => onChange(event.target.value)}
      placeholder={placeholder}
      type={type}
      className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700 outline-none transition focus:border-slate-300 focus:bg-white focus:ring-2 focus:ring-slate-200"
    />
  );
}

function SmallButton({
  children,
  onClick,
  disabled,
  busy,
  variant = "default",
}: {
  children: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  busy?: boolean;
  variant?: "default" | "primary" | "danger";
}) {
  const className =
    variant === "primary"
      ? "bg-slate-900 text-white hover:bg-slate-800"
      : variant === "danger"
        ? "bg-red-600 text-white hover:bg-red-500"
        : "bg-slate-100 text-slate-700 hover:bg-slate-200";

  return (
    <button
      onClick={onClick}
      disabled={disabled || busy}
      className={clsx(
        "inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition",
        className,
        (disabled || busy) && "cursor-not-allowed opacity-60"
      )}
    >
      {busy ? <RefreshCw className="h-4 w-4 animate-spin" /> : null}
      {children}
    </button>
  );
}

function StatusBadge({ status }: { status: string }) {
  const normalized = String(status || "").toLowerCase();
  const className =
    normalized === "completed"
      ? "border-emerald-200 bg-emerald-50 text-emerald-700"
      : normalized === "running"
        ? "border-blue-200 bg-blue-50 text-blue-700"
        : normalized === "queued"
          ? "border-violet-200 bg-violet-50 text-violet-700"
        : normalized === "waiting_input" || normalized === "waiting_approval"
          ? "border-amber-200 bg-amber-50 text-amber-700"
          : normalized === "cancelling"
            ? "border-orange-200 bg-orange-50 text-orange-700"
            : normalized === "timed_out"
              ? "border-orange-200 bg-orange-50 text-orange-700"
              : normalized === "cancelled"
                ? "border-slate-300 bg-slate-100 text-slate-700"
        : normalized === "failed"
          ? "border-red-200 bg-red-50 text-red-700"
          : "border-slate-200 bg-slate-50 text-slate-600";

  return (
    <span className={clsx("rounded-full border px-2 py-0.5 text-xs font-medium", className)}>
      {formatStatusText(status)}
    </span>
  );
}

function ResultBox({ title, content }: { title: string; content: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
      <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">{title}</div>
      <div className="whitespace-pre-wrap break-words text-sm leading-relaxed text-slate-700">{content || "-"}</div>
    </div>
  );
}

function ToolApprovalCard({
  approval,
  busy,
  onResolve,
}: {
  approval: RuntimeToolApproval;
  busy: boolean;
  onResolve: (approvalId: string, decision: "approved" | "rejected") => void;
}) {
  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 shadow-sm">
      <div className="flex flex-wrap gap-2">
        <InfoPill label="工具" value={approval.tool_name} />
        <InfoPill label="状态" value={approval.status} />
        <InfoPill label="创建时间" value={approval.created_at} />
      </div>
      <div className="mt-3 whitespace-pre-wrap rounded-lg bg-white/80 p-3 text-xs text-slate-700">
        {JSON.stringify(approval.arguments ?? {}, null, 2)}
      </div>
      <div className="mt-2 text-sm text-slate-700">
        {String((approval.policy as Record<string, unknown>)?.reason || "此工具调用需要审批。")}
      </div>
      <div className="mt-3 flex gap-2">
        <SmallButton disabled={busy} busy={busy} variant="primary" onClick={() => onResolve(approval.approval_id, "approved")}>
          批准
        </SmallButton>
        <SmallButton disabled={busy} variant="danger" onClick={() => onResolve(approval.approval_id, "rejected")}>
          拒绝
        </SmallButton>
      </div>
    </div>
  );
}

function ToolSecurityEventCard({ event }: { event: RuntimeToolSecurityEvent }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap gap-2">
        <InfoPill label="事件" value={event.event_type} />
        <InfoPill label="工具" value={event.tool_name || "-"} />
        <InfoPill label="状态" value={event.status || "-"} />
        <InfoPill label="时间" value={event.timestamp || "-"} />
      </div>
      <div className="mt-3 text-sm leading-relaxed text-slate-700">{event.detail || "-"}</div>
      {event.payload ? (
        <div className="mt-3 whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
          {JSON.stringify(event.payload, null, 2)}
        </div>
      ) : null}
    </div>
  );
}

function ContextColumn({
  title,
  icon: Icon,
  colorClass,
  messages,
}: {
  title: string;
  icon: ElementType;
  colorClass: string;
  messages: RuntimeContextMessage[];
}) {
  return (
    <div className="flex flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className={clsx("flex items-center gap-2 border-b border-slate-200 px-4 py-3", colorClass)}>
        <Icon className="h-5 w-5" />
        <h3 className="font-semibold">{title}</h3>
        <span className="ml-auto rounded-full bg-white/80 px-2 py-0.5 text-xs font-medium">{messages.length}</span>
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto bg-slate-50 p-4">
        {messages.length === 0 ? (
          <EmptyBlock text="当前暂无上下文消息。" />
        ) : (
          messages.map((message) => (
            <div key={message.id} className="rounded-xl border border-slate-200 bg-white p-3 text-sm shadow-sm">
              <div className="mb-2 flex items-center gap-2">
                <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
                  {message.role}
                </span>
              </div>
              <div className="whitespace-pre-wrap leading-relaxed text-slate-700">{message.content}</div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function LayerSource({ layer }: { layer: RuntimeMemoryLayerBase }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
      <div>
        <span className="font-medium text-slate-700">来源:</span> {layer.source_label}
      </div>
      <div className="mt-1">
        <span className="font-medium text-slate-700">类型:</span> {layer.source_kind}
      </div>
      {layer.source_path ? (
        <div className="mt-1 break-all">
          <span className="font-medium text-slate-700">路径:</span> {layer.source_path}
        </div>
      ) : null}
      {layer.updated_at ? (
        <div className="mt-1">
          <span className="font-medium text-slate-700">更新时间:</span> {layer.updated_at}
        </div>
      ) : null}
    </div>
  );
}

function MemorySections({ sections }: { sections: RuntimeMemorySection[] }) {
  if (!sections.length) {
    return <EmptyBlock text="当前没有可展示的记忆条目。" />;
  }

  return (
    <div className="space-y-3">
      {sections.map((section) => (
        <div key={section.key} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="font-semibold text-slate-800">{section.label}</h4>
            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
              {section.item_count} 项
            </span>
          </div>
          <p className="mt-2 text-sm leading-relaxed text-slate-500">{section.description}</p>
          {section.items.length ? (
            <div className="mt-3 flex flex-wrap gap-2">
              {section.items.map((item, index) => (
                <span
                  key={`${section.key}-${index}`}
                  className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-sm text-slate-700"
                >
                  {item}
                </span>
              ))}
            </div>
          ) : (
            <div className="mt-3 text-sm text-slate-400">暂无条目</div>
          )}
        </div>
      ))}
    </div>
  );
}

function ArchiveRecordCard({ archive }: { archive: RuntimeTopicArchiveRecord }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap gap-2">
        <InfoPill label="topic_group" value={archive.source_topic_group ?? "-"} />
        <InfoPill label="消息数" value={archive.topic_message_count} />
        <InfoPill label="归档时间" value={archive.archived_at || "-"} />
      </div>
      <div className="mt-3 break-all text-sm text-slate-500">
        <span className="font-medium text-slate-700">来源文件:</span> {archive.source_file || "-"}
      </div>
      <div className="mt-3 text-sm leading-relaxed text-slate-700">
        <span className="font-medium text-slate-800">摘要:</span> {archive.summary_text || "暂无摘要"}
      </div>
      {archive.topic_excerpt ? (
        <div className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-sm leading-relaxed text-slate-600">
          {archive.topic_excerpt}
        </div>
      ) : null}
    </div>
  );
}

function AtomicRecordCard({ record }: { record: RuntimeAtomicMemoryRecord }) {
  const statusClass =
    record.memory_status === "historical"
      ? "border-amber-200 bg-amber-50 text-amber-700"
      : "border-emerald-200 bg-emerald-50 text-emerald-700";

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className={clsx("rounded-full border px-2 py-0.5 text-xs font-medium", statusClass)}>
          {record.memory_status}
        </span>
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">{record.memory_kind}</span>
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">{record.textType}</span>
      </div>
      <div className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-slate-800">
        {record.personalizedText || record.text}
      </div>
      {record.personalizedText && record.personalizedText !== record.text ? (
        <div className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-slate-500">{record.text}</div>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-2">
        <InfoPill label="时间" value={record.timestamp || "-"} />
        <InfoPill label="更新时间" value={record.updated_at || "-"} />
        <InfoPill label="topic_group" value={record.source_topic_group ?? "-"} />
      </div>
      <div className="mt-3 text-sm text-slate-500">
        <div>
          <span className="font-medium text-slate-700">来源:</span> {record.source || "-"}
        </div>
        {record.source_file ? (
          <div className="mt-1 break-all">
            <span className="font-medium text-slate-700">来源文件:</span> {record.source_file}
          </div>
        ) : null}
        {record.memory_status_detail ? (
          <div className="mt-1">
            <span className="font-medium text-slate-700">详情:</span> {record.memory_status_detail}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function MemorySearchResultCard({ item }: { item: LongTermMemoryResult }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <InfoPill label="score" value={item.score.toFixed(4)} />
        <InfoPill label="status" value={item.memory_status} />
        <InfoPill label="kind" value={item.memory_kind} />
        <InfoPill label="type" value={item.textType} />
      </div>
      <div className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-slate-800">{item.text}</div>
      <div className="mt-3 flex flex-wrap gap-2">
        <InfoPill label="时间" value={item.timestamp || "-"} />
        <InfoPill label="topic_group" value={item.source_topic_group ?? "-"} />
      </div>
      <div className="mt-3 text-sm text-slate-500">
        <div>
          <span className="font-medium text-slate-700">来源:</span> {item.source || "-"}
        </div>
        {item.source_file ? (
          <div className="mt-1 break-all">
            <span className="font-medium text-slate-700">来源文件:</span> {item.source_file}
          </div>
        ) : null}
      </div>
      {item.topic_archive ? (
        <div className="mt-3 rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm text-slate-600">
          <div className="font-medium text-slate-700">关联话题归档</div>
          <div className="mt-1">{item.topic_archive.summary_text || "暂无摘要"}</div>
        </div>
      ) : null}
    </div>
  );
}

function SubAgentTaskCard({
  task,
  busy,
  onContinue,
  onCancel,
}: {
  task: SubAgentTask;
  busy: boolean;
  onContinue: (taskId: string, payload: { userReply?: string; approvalDecision?: "approved" | "rejected" }) => Promise<void>;
  onCancel: (taskId: string, reason: string) => Promise<void>;
}) {
  const [userReply, setUserReply] = useState("");
  const [cancelReason, setCancelReason] = useState("");
  const normalizedStatus = String(task.status || "").toLowerCase();
  const awaiting = task.awaiting ?? {};
  const isQueued = normalizedStatus === "queued";
  const isWaitingInput = normalizedStatus === "waiting_input";
  const isWaitingApproval = normalizedStatus === "waiting_approval";
  const canCancel = !isTerminalSubAgentStatus(normalizedStatus) && normalizedStatus !== "cancelling";
  const hasStructuredOutput = Boolean(task.structured_output && Object.keys(task.structured_output).length);
  const cacheAgeLabel =
    typeof task.cache_age_seconds === "number" ? `${task.cache_age_seconds.toFixed(task.cache_age_seconds >= 10 ? 0 : 1)}s` : "";
  const groupPositionLabel =
    typeof task.group_index === "number" && typeof task.group_size === "number" && task.group_size > 0
      ? `${task.group_index}/${task.group_size}`
      : "";
  const shouldShowStatusMessage = Boolean(
    task.status_message &&
      task.status_message !== task.result &&
      task.status_message !== task.error &&
      task.status_message !== task.queue_reason
  );

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={task.status} />
        <InfoPill label="任务 ID" value={task.task_id} />
        {task.agent_type ? <InfoPill label="类型" value={task.agent_type} /> : null}
        {task.group_id ? (
          <InfoPill label="批次" value={task.group_label ? `${task.group_label} · ${task.group_id}` : task.group_id} />
        ) : null}
        {groupPositionLabel ? <InfoPill label="批次序号" value={groupPositionLabel} /> : null}
        <InfoPill label="模型" value={task.model} />
        {typeof task.priority === "number" ? <InfoPill label="优先级" value={task.priority} /> : null}
        {task.cache_hit ? <InfoPill label="缓存" value="命中" /> : null}
        <InfoPill label="工具上限" value={task.max_tool_calls} />
        <InfoPill label="超时" value={`${task.timeout_seconds}s`} />
        {typeof task.resume_count === "number" && task.resume_count > 0 ? (
          <InfoPill label="恢复次数" value={task.resume_count} />
        ) : null}
      </div>
      <div className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-slate-800">{task.task}</div>
      <div className="mt-3 flex flex-wrap gap-2">
        <InfoPill label="创建时间" value={formatTimestamp(task.created_at)} />
        <InfoPill label="更新时间" value={formatTimestamp(task.updated_at)} />
        <InfoPill label="完成时间" value={formatTimestamp(task.finished_at)} />
        {task.deadline_at ? <InfoPill label="截止时间" value={formatTimestamp(task.deadline_at)} /> : null}
        {isQueued && task.queue_position ? <InfoPill label="队列位置" value={task.queue_position} /> : null}
        {isQueued && task.queued_at ? <InfoPill label="排队时间" value={formatTimestamp(task.queued_at)} /> : null}
        {task.stats ? <InfoPill label="耗时" value={`${task.stats.duration_seconds.toFixed(2)}s`} /> : null}
        {task.cache_hit && task.cache_source_task_id ? <InfoPill label="缓存来源" value={task.cache_source_task_id} /> : null}
        {task.cache_hit && cacheAgeLabel ? <InfoPill label="缓存年龄" value={cacheAgeLabel} /> : null}
        {task.cache_hit && task.cache_created_at ? <InfoPill label="缓存时间" value={formatTimestamp(task.cache_created_at)} /> : null}
        {task.cache_hit && task.cache_expires_at ? <InfoPill label="过期时间" value={formatTimestamp(task.cache_expires_at)} /> : null}
      </div>
      {isQueued && task.queue_reason ? <ResultBox title="排队说明" content={task.queue_reason} /> : null}
      {shouldShowStatusMessage ? <ResultBox title="状态说明" content={task.status_message || ""} /> : null}
      {isWaitingInput || isWaitingApproval ? (
        <div className="space-y-3 rounded-xl border border-amber-200 bg-amber-50 p-4">
          <div className="flex flex-wrap gap-2">
            <InfoPill label="等待类型" value={awaiting.type || normalizedStatus} />
            {awaiting.tool_name ? <InfoPill label="工具" value={awaiting.tool_name} /> : null}
          </div>
          <div className="whitespace-pre-wrap text-sm leading-relaxed text-slate-800">
            {awaiting.question || "任务正在等待外部输入。"}
          </div>
          {awaiting.context ? (
            <div className="rounded-lg bg-white/80 px-3 py-2 text-sm text-slate-600">{awaiting.context}</div>
          ) : null}
          {(awaiting.options || []).length ? (
            <div className="flex flex-wrap gap-2">
              {(awaiting.options || []).map((option) => (
                <span
                  key={`${task.task_id}-${option}`}
                  className="rounded-full border border-amber-200 bg-white px-3 py-1 text-xs text-amber-700"
                >
                  {option}
                </span>
              ))}
            </div>
          ) : null}
          {isWaitingApproval && awaiting.tool_arguments ? (
            <details className="rounded-lg border border-amber-200 bg-white/80 px-3 py-2">
              <summary className="cursor-pointer text-sm font-medium text-slate-700">审批参数</summary>
              <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-xs leading-relaxed text-slate-600">
                {JSON.stringify(awaiting.tool_arguments, null, 2)}
              </pre>
            </details>
          ) : null}
          {isWaitingApproval && awaiting.policy ? (
            <details className="rounded-lg border border-amber-200 bg-white/80 px-3 py-2">
              <summary className="cursor-pointer text-sm font-medium text-slate-700">策略说明</summary>
              <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-xs leading-relaxed text-slate-600">
                {JSON.stringify(awaiting.policy, null, 2)}
              </pre>
            </details>
          ) : null}
          <TextInput
            value={userReply}
            onChange={setUserReply}
            placeholder={isWaitingApproval ? "可选：补充说明，或直接批准 / 拒绝。" : "输入继续任务所需的补充内容。"}
            rows={3}
          />
          <div className="flex flex-wrap gap-2">
            {isWaitingApproval ? (
              <>
                <SmallButton
                  variant="primary"
                  busy={busy}
                  onClick={() => void onContinue(task.task_id, { approvalDecision: "approved" })}
                >
                  批准并继续
                </SmallButton>
                <SmallButton
                  variant="danger"
                  busy={busy}
                  onClick={() => void onContinue(task.task_id, { approvalDecision: "rejected" })}
                >
                  拒绝并继续
                </SmallButton>
              </>
            ) : null}
            <SmallButton
              busy={busy}
              disabled={!userReply.trim()}
              onClick={() => void onContinue(task.task_id, { userReply: userReply.trim() })}
            >
              发送回复
            </SmallButton>
          </div>
        </div>
      ) : null}
      {task.result ? <ResultBox title="结果" content={task.result} /> : null}
      {task.error ? (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{task.error}</div>
      ) : null}
      {hasStructuredOutput ? (
        <details className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
          <summary className="cursor-pointer text-sm font-medium text-slate-700">结构化输出</summary>
          <pre className="mt-3 overflow-x-auto whitespace-pre-wrap text-xs leading-relaxed text-slate-600">
            {JSON.stringify(task.structured_output, null, 2)}
          </pre>
        </details>
      ) : null}
      {canCancel ? (
        <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
          <div className="mb-2 text-sm font-medium text-slate-700">取消任务</div>
          <div className="flex flex-col gap-3 md:flex-row">
            <div className="flex-1">
              <InlineInput value={cancelReason} onChange={setCancelReason} placeholder="可选取消原因" />
            </div>
            <SmallButton
              variant="danger"
              busy={busy}
              onClick={() => void onCancel(task.task_id, cancelReason.trim())}
            >
              取消任务
            </SmallButton>
          </div>
        </div>
      ) : null}
      <details className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
        <summary className="cursor-pointer text-sm font-medium text-slate-700">
          工具轨迹 ({task.tool_trace.length})
        </summary>
        <pre className="mt-3 overflow-x-auto whitespace-pre-wrap text-xs leading-relaxed text-slate-600">
          {JSON.stringify(task.tool_trace, null, 2)}
        </pre>
      </details>
    </div>
  );
}

function MCPToolCard({ tool }: { tool: MCPToolSpec }) {
  const functionMeta = tool.tool_definition.function;
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <InfoPill label="服务端" value={tool.server_name} />
        <InfoPill label="工具" value={tool.tool_name} />
        <InfoPill label="函数" value={functionMeta.name} />
      </div>
      <div className="mt-3 text-sm leading-relaxed text-slate-700">{functionMeta.description || "暂无描述"}</div>
      <div className="mt-3 break-all text-xs text-slate-500">{tool.server_url}</div>
      <details className="mt-3 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
        <summary className="cursor-pointer text-sm font-medium text-slate-700">输入 Schema</summary>
        <pre className="mt-3 overflow-x-auto whitespace-pre-wrap text-xs leading-relaxed text-slate-600">
          {JSON.stringify(functionMeta.parameters, null, 2)}
        </pre>
      </details>
    </div>
  );
}

function SkillCard({ skill, onDelete, deleting }: { skill: ManagedSkill; onDelete: (name: string) => void; deleting: boolean }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <InfoPill label="Skill" value={skill.skill_name} />
        <InfoPill label="目录" value={skill.folder_name} />
        <InfoPill label="启用" value={skill.enabled} />
        <InfoPill label="SKILL.md" value={skill.has_skill_md ? "yes" : "no"} />
        {skill.runtime_mode ? <InfoPill label="Runtime" value={skill.runtime_mode} /> : null}
      </div>
      <div className="mt-3 text-sm leading-relaxed text-slate-700">{skill.description || "暂无描述"}</div>
      <div className="mt-3 flex flex-wrap gap-2">
        {(skill.tool_names || []).length ? (
          skill.tool_names.map((toolName) => (
            <span
              key={`${skill.skill_name}-${toolName}`}
              className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs text-slate-700"
            >
              {toolName}
            </span>
          ))
        ) : (
          <span className="text-sm text-slate-400">暂无工具定义</span>
        )}
      </div>
      <div className="mt-3 text-xs text-slate-500">
        <div className="break-all">manifest: {skill.manifest_path || "-"}</div>
        <div className="mt-1 break-all">skill: {skill.skill_path || "-"}</div>
        <div className="mt-1 break-all">runtime: {skill.runtime_path || "-"}</div>
      </div>
      <div className="mt-3">
        <SmallButton variant="danger" busy={deleting} onClick={() => onDelete(skill.skill_name)}>
          删除 Skill
        </SmallButton>
      </div>
    </div>
  );
}

export default function Debug() {
  const [runtime, setRuntime] = useState<RuntimeState | null>(null);
  const [subAgents, setSubAgents] = useState<SubAgentTask[]>([]);
  const [mcpTools, setMcpTools] = useState<MCPToolSpec[]>([]);
  const [skills, setSkills] = useState<ManagedSkill[]>([]);
  const [skillDiagnostics, setSkillDiagnostics] = useState<SkillDiagnostic[]>([]);
  const [browserStatus, setBrowserStatus] = useState<BrowserStatusResult | null>(null);

  const [error, setError] = useState("");
  const [banner, setBanner] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [mcpRefreshing, setMcpRefreshing] = useState(false);
  const [savingSkill, setSavingSkill] = useState(false);
  const [deletingSkillName, setDeletingSkillName] = useState("");
  const [runningBrowserAction, setRunningBrowserAction] = useState("");
  const [creatingSubAgent, setCreatingSubAgent] = useState(false);
  const [creatingSubAgentBatch, setCreatingSubAgentBatch] = useState(false);
  const [searchingMemory, setSearchingMemory] = useState(false);
  const [storingMemory, setStoringMemory] = useState(false);
  const [resolvingApprovalId, setResolvingApprovalId] = useState("");
  const [subAgentActionTaskId, setSubAgentActionTaskId] = useState("");

  const [subAgentTask, setSubAgentTask] = useState("");
  const [subAgentBatchTasks, setSubAgentBatchTasks] = useState("");
  const [subAgentBatchLabel, setSubAgentBatchLabel] = useState("");
  const [subAgentBatchWait, setSubAgentBatchWait] = useState(false);
  const [subAgentModel, setSubAgentModel] = useState("");
  const [subAgentMaxToolCalls, setSubAgentMaxToolCalls] = useState("");
  const [subAgentTimeout, setSubAgentTimeout] = useState("60");
  const [subAgentPriority, setSubAgentPriority] = useState("0");
  const [subAgentUseCache, setSubAgentUseCache] = useState(true);
  const [lastSubAgentBatch, setLastSubAgentBatch] = useState<SubAgentBatchResult | null>(null);

  const [memoryQuery, setMemoryQuery] = useState("");
  const [memoryLimit, setMemoryLimit] = useState("8");
  const [includeHistorical, setIncludeHistorical] = useState(false);
  const [memoryResults, setMemoryResults] = useState<LongTermMemoryResult[]>([]);

  const [memoryText, setMemoryText] = useState("");
  const [memoryPersonalizedText, setMemoryPersonalizedText] = useState("");
  const [memoryTextType, setMemoryTextType] = useState("Fact");
  const [memoryImportance, setMemoryImportance] = useState("0.7");
  const [memoryTtlDays, setMemoryTtlDays] = useState("30");

  const [skillName, setSkillName] = useState("");
  const [skillDescription, setSkillDescription] = useState("");
  const [skillWhenToUse, setSkillWhenToUse] = useState("");
  const [skillIntentExamples, setSkillIntentExamples] = useState("");
  const [skillEnabled, setSkillEnabled] = useState(true);
  const [skillInstructions, setSkillInstructions] = useState("");
  const [skillToolDefinitions, setSkillToolDefinitions] = useState("[]");

  const [browserUrl, setBrowserUrl] = useState("");
  const [browserQuery, setBrowserQuery] = useState("");
  const [browserResult, setBrowserResult] = useState<
    BrowserExtractPageResult | BrowserReadLinkedPageResult | Record<string, unknown> | null
  >(null);

  const memoryLayers = runtime?.memory_layers;

  const activeDynamicToolCount = useMemo(() => {
    return (browserStatus?.tool_count || 0) + mcpTools.length;
  }, [browserStatus?.tool_count, mcpTools.length]);

  const subAgentCacheHitCount = useMemo(() => {
    return subAgents.filter((task) => task.cache_hit).length;
  }, [subAgents]);

  const lastSubAgentBatchStatusText = useMemo(() => {
    const statusCounts = lastSubAgentBatch?.summary?.status_counts;
    if (!statusCounts) {
      return "";
    }
    return Object.entries(statusCounts)
      .filter(([, count]) => typeof count === "number" && count > 0)
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([status, count]) => `${formatStatusText(status)} ${count}`)
      .join(" · ");
  }, [lastSubAgentBatch]);

  function upsertSubAgentTask(task: SubAgentTask) {
    setSubAgents((current) => {
      const next = current.filter((item) => item.task_id !== task.task_id);
      next.push(task);
      return next;
    });
  }

  async function loadDebugData() {
    setRefreshing(true);
    try {
      const [runtimeState, subAgentResponse, mcpResponse, skillsResponse, browserResponse] = await Promise.all([
        api.getRuntime(),
        api.getSubAgents(true),
        api.getMcpTools(),
        api.getSkills(),
        api.getBrowserStatus(),
      ]);
      setRuntime(runtimeState);
      setSubAgents(subAgentResponse.tasks);
      setMcpTools(mcpResponse.tools);
      setSkills(skillsResponse.skills);
      setSkillDiagnostics(skillsResponse.diagnostics || []);
      setBrowserStatus(browserResponse);
      setError("");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "加载调试数据失败");
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => {
    void loadDebugData();
    const timer = window.setInterval(() => {
      void loadDebugData();
    }, 5000);
    return () => {
      window.clearInterval(timer);
    };
  }, []);

  async function handleCreateSubAgent() {
    const task = subAgentTask.trim();
    if (!task) {
      setBanner("请输入子代理任务。");
      return;
    }

    setCreatingSubAgent(true);
    try {
      const parsedSubAgentMaxToolCalls = subAgentMaxToolCalls.trim()
        ? Number(subAgentMaxToolCalls) || undefined
        : undefined;
      const response = await api.createSubAgent({
        task,
        model: subAgentModel.trim() || undefined,
        max_tool_calls: parsedSubAgentMaxToolCalls,
        timeout_seconds: Number(subAgentTimeout) || 60,
        priority: Number(subAgentPriority) || 0,
        use_cache: subAgentUseCache,
      });
      setBanner(`已创建子代理任务 ${response.task.task_id}`);
      setSubAgentTask("");
      upsertSubAgentTask(response.task);
    } catch (requestError) {
      setBanner(requestError instanceof Error ? requestError.message : "创建子代理失败");
    } finally {
      setCreatingSubAgent(false);
    }
  }

  async function handleCreateSubAgentBatch() {
    const taskItems = subAgentBatchTasks
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean);
    if (!taskItems.length) {
      setBanner("请输入至少一个并行子代理任务。");
      return;
    }

    setCreatingSubAgentBatch(true);
    try {
      const parsedSubAgentMaxToolCalls = subAgentMaxToolCalls.trim()
        ? Number(subAgentMaxToolCalls) || undefined
        : undefined;
      const response = await api.createSubAgentBatch({
        tasks: taskItems.map((task) => ({
          task,
          model: subAgentModel.trim() || undefined,
          max_tool_calls: parsedSubAgentMaxToolCalls,
          timeout_seconds: Number(subAgentTimeout) || 60,
          priority: Number(subAgentPriority) || 0,
          use_cache: subAgentUseCache,
        })),
        group_label: subAgentBatchLabel.trim() || undefined,
        wait_for_completion: subAgentBatchWait,
        timeout_seconds: subAgentBatchWait ? Math.max(Number(subAgentTimeout) || 60, 20) : undefined,
      });
      setLastSubAgentBatch(response);
      (response.tasks || []).forEach((task) => upsertSubAgentTask(task));
      setSubAgentBatchTasks("");
      const groupId = response.group?.group_id || "-";
      const errorCount = response.summary?.error_count || response.errors?.length || 0;
      if (errorCount > 0) {
        setBanner(`已创建批次 ${groupId}，成功 ${response.count} 个，失败 ${errorCount} 个。`);
      } else {
        setBanner(`已创建批次 ${groupId}，共 ${response.count} 个任务。`);
      }
    } catch (requestError) {
      setBanner(requestError instanceof Error ? requestError.message : "创建并行子代理任务失败");
    } finally {
      setCreatingSubAgentBatch(false);
    }
  }

  async function handleContinueSubAgent(
    taskId: string,
    payload: { userReply?: string; approvalDecision?: "approved" | "rejected" }
  ) {
    setSubAgentActionTaskId(taskId);
    try {
      const response = await api.continueSubAgent(taskId, {
        user_reply: payload.userReply || undefined,
        approval_decision: payload.approvalDecision,
      });
      upsertSubAgentTask(response.task);
      setBanner(`已继续任务 ${taskId}，当前状态：${formatStatusText(response.task.status)}。`);
    } catch (requestError) {
      setBanner(requestError instanceof Error ? requestError.message : "继续子代理任务失败");
    } finally {
      setSubAgentActionTaskId("");
    }
  }

  async function handleCancelSubAgent(taskId: string, reason: string) {
    setSubAgentActionTaskId(taskId);
    try {
      const response = await api.cancelSubAgent(taskId, {
        reason: reason || undefined,
      });
      upsertSubAgentTask(response.task);
      setBanner(`已提交取消请求，任务 ${taskId} 当前状态：${formatStatusText(response.task.status)}。`);
    } catch (requestError) {
      setBanner(requestError instanceof Error ? requestError.message : "取消子代理任务失败");
    } finally {
      setSubAgentActionTaskId("");
    }
  }

  async function handleSearchMemory() {
    const query = memoryQuery.trim();
    if (!query) {
      setBanner("请输入长期记忆检索内容。");
      return;
    }

    setSearchingMemory(true);
    try {
      const response = await api.searchLongTermMemory({
        query,
        include_historical: includeHistorical,
        limit: Number(memoryLimit) || 8,
      });
      setMemoryResults(response.results);
      setBanner(`长期记忆检索返回 ${response.count} 条结果。`);
    } catch (requestError) {
      setBanner(requestError instanceof Error ? requestError.message : "长期记忆检索失败");
    } finally {
      setSearchingMemory(false);
    }
  }

  async function handleStoreMemory() {
    const text = memoryText.trim();
    if (!text) {
      setBanner("请输入要写入记忆的内容。");
      return;
    }

    setStoringMemory(true);
    try {
      const response = await api.storeLongTermMemory({
        text,
        personalized_text: memoryPersonalizedText.trim() || undefined,
        text_type: memoryTextType.trim() || "Fact",
        importance: Number(memoryImportance) || 0.7,
        ttl_days: Number(memoryTtlDays) || 30,
      });
      setBanner(`已写入长期记忆，TTL 为 ${response.ttl_days} 天。`);
      setMemoryText("");
      setMemoryPersonalizedText("");
      await loadDebugData();
    } catch (requestError) {
      setBanner(requestError instanceof Error ? requestError.message : "长期记忆写入失败");
    } finally {
      setStoringMemory(false);
    }
  }

  async function handleRefreshMcp() {
    setMcpRefreshing(true);
    try {
      const response = await api.refreshMcpTools();
      setMcpTools(response.tools);
      setBanner(`已刷新 MCP 工具，共 ${response.count} 个。`);
    } catch (requestError) {
      setBanner(requestError instanceof Error ? requestError.message : "刷新 MCP 工具失败");
    } finally {
      setMcpRefreshing(false);
    }
  }

  async function handleSaveSkill() {
    const normalizedSkillName = skillName.trim();
    const normalizedDescription = skillDescription.trim();
    if (!normalizedSkillName || !normalizedDescription) {
      setBanner("SkillName 和 Description 为必填项。");
      return;
    }

    let toolDefinitions: unknown[] | undefined;
    const toolDefinitionsText = skillToolDefinitions.trim();
    if (toolDefinitionsText) {
      try {
        const parsed = JSON.parse(toolDefinitionsText);
        if (!Array.isArray(parsed)) {
          throw new Error("ToolDefinitions 必须是 JSON 数组。");
        }
        toolDefinitions = parsed;
      } catch (parseError) {
        setBanner(parseError instanceof Error ? parseError.message : "ToolDefinitions 解析失败");
        return;
      }
    }

    setSavingSkill(true);
    try {
      await api.saveSkill({
        skill_name: normalizedSkillName,
        description: normalizedDescription,
        when_to_use: skillWhenToUse
          .split("\n")
          .map((item) => item.trim())
          .filter(Boolean),
        intent_examples: skillIntentExamples
          .split("\n")
          .map((item) => item.trim())
          .filter(Boolean),
        skill_instructions: skillInstructions.trim() || undefined,
        tool_definitions: toolDefinitions,
        enabled: skillEnabled,
      });
      setBanner(`已保存 Skill ${normalizedSkillName}。`);
      setSkillName("");
      setSkillDescription("");
      setSkillWhenToUse("");
      setSkillIntentExamples("");
      setSkillInstructions("");
      setSkillToolDefinitions("[]");
      const skillsResponse = await api.getSkills();
      setSkills(skillsResponse.skills);
      setSkillDiagnostics(skillsResponse.diagnostics || []);
    } catch (requestError) {
      setBanner(requestError instanceof Error ? requestError.message : "保存 Skill 失败");
    } finally {
      setSavingSkill(false);
    }
  }

  async function handleDeleteSkill(name: string) {
    setDeletingSkillName(name);
    try {
      await api.deleteSkill(name);
      setBanner(`已删除 Skill ${name}。`);
      const skillsResponse = await api.getSkills();
      setSkills(skillsResponse.skills);
      setSkillDiagnostics(skillsResponse.diagnostics || []);
    } catch (requestError) {
      setBanner(requestError instanceof Error ? requestError.message : "删除 Skill 失败");
    } finally {
      setDeletingSkillName("");
    }
  }

  async function handleBrowserOpenTab() {
    const url = browserUrl.trim();
    if (!url) {
      setBanner("请输入 URL。");
      return;
    }

    setRunningBrowserAction("open");
    try {
      const response = await api.browserOpenTab({ url });
      setBrowserResult(response);
      setBanner(response.ok ? "已打开目标页面。" : response.error || "打开页面失败");
      const browserResponse = await api.getBrowserStatus();
      setBrowserStatus(browserResponse);
    } catch (requestError) {
      setBanner(requestError instanceof Error ? requestError.message : "打开页面失败");
    } finally {
      setRunningBrowserAction("");
    }
  }

  async function handleBrowserExtractPage() {
    setRunningBrowserAction("extract");
    try {
      const response = await api.browserExtractPage();
      setBrowserResult(response);
      setBanner(response.ok ? "已抽取当前页面内容。" : response.error || "页面抽取失败");
      const browserResponse = await api.getBrowserStatus();
      setBrowserStatus(browserResponse);
    } catch (requestError) {
      setBanner(requestError instanceof Error ? requestError.message : "页面抽取失败");
    } finally {
      setRunningBrowserAction("");
    }
  }

  async function handleBrowserReadLinkedPage() {
    const query = browserQuery.trim();
    if (!query) {
      setBanner("请输入浏览器搜索内容。");
      return;
    }

    setRunningBrowserAction("read-linked");
    try {
      const response = await api.browserReadLinkedPage({ query });
      setBrowserResult(response);
      setBanner(
        response.ok
          ? response.page
            ? "已读取链接页面。"
            : "已返回候选链接，请根据 ref 继续操作。"
          : response.error || "读取链接页面失败",
      );
      const browserResponse = await api.getBrowserStatus();
      setBrowserStatus(browserResponse);
    } catch (requestError) {
      setBanner(requestError instanceof Error ? requestError.message : "读取链接页面失败");
    } finally {
      setRunningBrowserAction("");
    }
  }

  async function handleResolveApproval(approvalId: string, decision: "approved" | "rejected") {
    setResolvingApprovalId(approvalId);
    try {
      await api.resolveToolApproval({ approval_id: approvalId, decision });
      setBanner(`审批 ${approvalId} 已${decision === "approved" ? "批准" : "拒绝"}。`);
      await loadDebugData();
    } catch (requestError) {
      setBanner(requestError instanceof Error ? requestError.message : "审批处理失败");
    } finally {
      setResolvingApprovalId("");
    }
  }

  return (
    <div className="flex h-full flex-col bg-slate-50">
      <header className="z-10 flex items-center justify-between border-b border-slate-200 bg-white px-6 py-4 shadow-sm">
        <div>
          <h1 className="text-xl font-bold text-slate-800">高级运行时调试面板</h1>
          <p className="text-sm text-slate-500">
            可视化运行时状态，并直接调试子代理、长期记忆、MCP 工具、浏览器工具和 Skill 自管理能力。
          </p>
        </div>
        <SmallButton busy={refreshing} onClick={() => void loadDebugData()}>
          <RefreshCw className="h-4 w-4" />
          刷新
        </SmallButton>
      </header>

      {error ? (
        <div className="mx-6 mt-6 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      ) : null}

      {banner ? (
        <div className="mx-6 mt-6 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 shadow-sm">
          {banner}
        </div>
      ) : null}

      <div className="flex-1 overflow-y-auto p-6">
        <div className="space-y-8">
          <section>
            <SectionTitle
              icon={Sparkles}
              title="运行时总览"
              description="展示当前会话状态，以及新增运行时能力的实时状态。"
            />
            <div className="grid grid-cols-1 gap-6 xl:grid-cols-4">
              <PanelCard title="会话运行时" icon={Bot} colorClass="bg-slate-100 text-slate-700">
                <div className="flex flex-wrap gap-2">
                  <InfoPill label="话题组" value={runtime?.meta.topic_group ?? "-"} />
                  <InfoPill label="输入次数" value={runtime?.meta.input_count ?? "-"} />
                  <InfoPill label="已初始化" value={runtime?.meta.runtime_initialized ?? false} />
                </div>
                <ResultBox title="对话条数" content={String(runtime?.conversation.length ?? 0)} />
              </PanelCard>

              <PanelCard title="子代理运行时" icon={Network} colorClass="bg-blue-50 text-blue-700">
                <div className="flex flex-wrap gap-2">
                  <InfoPill label="总任务数" value={subAgents.length} />
                  <InfoPill label="运行中" value={subAgents.filter((task) => task.status === "running").length} />
                  <InfoPill label="排队中" value={subAgents.filter((task) => task.status === "queued").length} />
                  <InfoPill
                    label="等待中"
                    value={subAgents.filter((task) => task.status === "waiting_input" || task.status === "waiting_approval").length}
                  />
                  <InfoPill
                    label="已完成"
                    value={subAgents.filter((task) => task.status === "completed").length}
                  />
                </div>
              </PanelCard>

              <PanelCard title="动态工具" icon={Wrench} colorClass="bg-emerald-50 text-emerald-700">
                <div className="flex flex-wrap gap-2">
                  <InfoPill label="MCP 工具数" value={mcpTools.length} />
                  <InfoPill label="浏览器工具数" value={browserStatus?.tool_count ?? 0} />
                  <InfoPill label="动态工具总数" value={activeDynamicToolCount} />
                </div>
              </PanelCard>

              <PanelCard title="Skill 运行时" icon={BookOpen} colorClass="bg-amber-50 text-amber-700">
                <div className="flex flex-wrap gap-2">
                  <InfoPill label="已安装 Skills" value={skills.length} />
                  <InfoPill label="已启用 Skills" value={skills.filter((skill) => skill.enabled).length} />
                </div>
              </PanelCard>
            </div>
          </section>

          <section>
            <SectionTitle
              icon={Layers3}
              title="上下文窗口"
              description="展示当前实际注入到不同对话模式中的运行时上下文。"
            />
            <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
              <ContextColumn
                title="Agent 上下文"
                icon={Bot}
                messages={runtime?.contexts.agent ?? []}
                colorClass="bg-indigo-50 text-indigo-700"
              />
              <ContextColumn
                title="RolePlay 上下文"
                icon={Heart}
                messages={runtime?.contexts.role_play ?? []}
                colorClass="bg-pink-50 text-pink-700"
              />
              <ContextColumn
                title="Simple 上下文"
                icon={Zap}
                messages={runtime?.contexts.simple ?? []}
                colorClass="bg-amber-50 text-amber-700"
              />
            </div>
          </section>

          <section>
            <SectionTitle
              icon={ShieldAlert}
              title="工具安全"
              description="查看 blocked / requires approval / backend / checkpoint 等安全状态，并处理审批。"
            />
            <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
              <PanelCard title="待处理审批" icon={AlertTriangle} colorClass="bg-amber-50 text-amber-700">
                {(runtime?.pending_tool_approvals || []).filter((item) => item.status === "pending").length ? (
                  (runtime?.pending_tool_approvals || [])
                    .filter((item) => item.status === "pending")
                    .map((approval) => (
                      <ToolApprovalCard
                        key={approval.approval_id}
                        approval={approval}
                        busy={resolvingApprovalId === approval.approval_id}
                        onResolve={handleResolveApproval}
                      />
                    ))
                ) : (
                  <EmptyBlock text="当前没有待处理的工具审批。" />
                )}
              </PanelCard>

              <PanelCard title="最近安全事件" icon={Wrench} colorClass="bg-rose-50 text-rose-700">
                {(runtime?.recent_tool_security_events || []).length ? (
                  (runtime?.recent_tool_security_events || []).map((event) => (
                    <ToolSecurityEventCard key={event.id} event={event} />
                  ))
                ) : (
                  <EmptyBlock text="当前没有工具安全事件。" />
                )}
              </PanelCard>
            </div>
          </section>

          <section>
            <SectionTitle
              icon={Database}
              title="记忆层状态"
              description="保留现有分层记忆可视化，并补充长期记忆相关调试能力。"
            />

            {!memoryLayers ? (
              <EmptyBlock text="后端未返回 memory_layers 数据。" />
            ) : (
              <div className="grid grid-cols-1 gap-6 2xl:grid-cols-2">
                <PanelCard title="持久核心记忆" icon={Database} colorClass="bg-sky-50 text-sky-700">
                  <LayerSource layer={memoryLayers.persistent_core} />
                  <MemorySections sections={memoryLayers.persistent_core.sections} />
                </PanelCard>

                <PanelCard
                  title="会话 / 话题工作记忆"
                  icon={Layers3}
                  colorClass="bg-violet-50 text-violet-700"
                >
                  <LayerSource layer={memoryLayers.topic_working_memory} />
                  <MemorySections sections={memoryLayers.topic_working_memory.sections} />
                </PanelCard>

                <PanelCard
                  title="话题归档 / 情节记忆"
                  icon={Archive}
                  colorClass="bg-emerald-50 text-emerald-700"
                >
                  <LayerSource layer={memoryLayers.topic_archive} />
                  <div className="flex flex-wrap gap-2">
                    <InfoPill label="总归档数" value={memoryLayers.topic_archive.total_archives} />
                    <InfoPill label="近期归档" value={memoryLayers.topic_archive.recent_archives.length} />
                  </div>
                  {memoryLayers.topic_archive.recent_archives.length ? (
                    <div className="space-y-3">
                      {memoryLayers.topic_archive.recent_archives.map((archive) => (
                        <ArchiveRecordCard key={`${archive.archive_id}-${archive.source_file}`} archive={archive} />
                      ))}
                    </div>
                  ) : (
                    <EmptyBlock text="当前还没有归档话题。" />
                  )}
                </PanelCard>

                <PanelCard
                  title="原子语义记忆"
                  icon={Search}
                  colorClass="bg-amber-50 text-amber-700"
                >
                  <LayerSource layer={memoryLayers.atomic_semantic_memory} />
                  <div className="flex flex-wrap gap-2">
                    <InfoPill label="集合" value={memoryLayers.atomic_semantic_memory.source_collection} />
                    <InfoPill
                      label="已初始化"
                      value={memoryLayers.atomic_semantic_memory.runtime_initialized}
                    />
                    <InfoPill label="总记录数" value={memoryLayers.atomic_semantic_memory.total_records} />
                    <InfoPill label="原子记忆数" value={memoryLayers.atomic_semantic_memory.atomic_records} />
                    <InfoPill label="生效中" value={memoryLayers.atomic_semantic_memory.active_records} />
                    <InfoPill label="历史记忆" value={memoryLayers.atomic_semantic_memory.historical_records} />
                    <InfoPill
                      label="话题摘要"
                      value={memoryLayers.atomic_semantic_memory.topic_summary_records}
                    />
                  </div>

                  {memoryLayers.atomic_semantic_memory.error ? (
                    <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                      {memoryLayers.atomic_semantic_memory.error}
                    </div>
                  ) : null}

                  {memoryLayers.atomic_semantic_memory.recent_atomic_memories.length ? (
                    <div className="space-y-3">
                      {memoryLayers.atomic_semantic_memory.recent_atomic_memories.map((record, index) => (
                        <AtomicRecordCard
                          key={`${record.id ?? "memory"}-${record.timestamp}-${index}`}
                          record={record}
                        />
                      ))}
                    </div>
                  ) : (
                    <EmptyBlock text="当前没有可展示的近期原子记忆。" />
                  )}
                </PanelCard>
              </div>
            )}
          </section>

          <section>
            <SectionTitle
              icon={Network}
              title="子代理委派"
              description="发起受限后台子代理任务，并查看实时状态、结果与工具轨迹。"
            />
            <div className="grid grid-cols-1 gap-6 2xl:grid-cols-[1.1fr_1.9fr]">
              <div className="space-y-6">
                <PanelCard title="创建委派任务" icon={Plus} colorClass="bg-blue-50 text-blue-700">
                  <TextInput
                    value={subAgentTask}
                    onChange={setSubAgentTask}
                    placeholder="输入一个明确的委派子任务。"
                    rows={4}
                  />
                  <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
                    <InlineInput value={subAgentModel} onChange={setSubAgentModel} placeholder="可选模型" />
                    <InlineInput
                      value={subAgentMaxToolCalls}
                      onChange={setSubAgentMaxToolCalls}
                      placeholder="最大工具调用数"
                      type="number"
                    />
                    <InlineInput
                      value={subAgentTimeout}
                      onChange={setSubAgentTimeout}
                      placeholder="超时秒数"
                      type="number"
                    />
                    <InlineInput
                      value={subAgentPriority}
                      onChange={setSubAgentPriority}
                      placeholder="优先级"
                      type="number"
                    />
                  </div>
                  <label className="flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700">
                    <input
                      type="checkbox"
                      checked={subAgentUseCache}
                      onChange={(event) => setSubAgentUseCache(event.target.checked)}
                    />
                    允许结果缓存
                  </label>
                  <div>
                    <SmallButton variant="primary" busy={creatingSubAgent} onClick={() => void handleCreateSubAgent()}>
                      创建子代理任务
                    </SmallButton>
                  </div>
                </PanelCard>

                <PanelCard title="批量并行委派" icon={Layers3} colorClass="bg-indigo-50 text-indigo-700">
                  <InlineInput value={subAgentBatchLabel} onChange={setSubAgentBatchLabel} placeholder="可选批次标签" />
                  <TextInput
                    value={subAgentBatchTasks}
                    onChange={setSubAgentBatchTasks}
                    placeholder="每行一个并行子代理任务。"
                    rows={6}
                  />
                  <label className="flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700">
                    <input
                      type="checkbox"
                      checked={subAgentBatchWait}
                      onChange={(event) => setSubAgentBatchWait(event.target.checked)}
                    />
                    创建后等待整批任务完成
                  </label>
                  <div>
                    <SmallButton variant="primary" busy={creatingSubAgentBatch} onClick={() => void handleCreateSubAgentBatch()}>
                      并行创建任务批次
                    </SmallButton>
                  </div>
                  {lastSubAgentBatch ? (
                    <div className="space-y-3 rounded-xl border border-slate-200 bg-slate-50 p-3">
                      <div className="flex flex-wrap gap-2">
                        {lastSubAgentBatch.group?.group_id ? <InfoPill label="批次 ID" value={lastSubAgentBatch.group.group_id} /> : null}
                        {lastSubAgentBatch.group?.group_label ? <InfoPill label="标签" value={lastSubAgentBatch.group.group_label} /> : null}
                        <InfoPill label="已创建" value={lastSubAgentBatch.count} />
                        {typeof lastSubAgentBatch.summary?.error_count === "number" ? (
                          <InfoPill label="错误数" value={lastSubAgentBatch.summary.error_count} />
                        ) : null}
                        {lastSubAgentBatch.summary?.wait_completed ? <InfoPill label="等待结果" value="completed" /> : null}
                        {lastSubAgentBatch.summary?.waiting_for_external_input ? <InfoPill label="等待结果" value="waiting" /> : null}
                      </div>
                      {lastSubAgentBatchStatusText ? (
                        <div className="text-sm text-slate-600">{lastSubAgentBatchStatusText}</div>
                      ) : null}
                      {(lastSubAgentBatch.errors || []).length ? (
                        <details className="rounded-lg border border-slate-200 bg-white px-3 py-2">
                          <summary className="cursor-pointer text-sm font-medium text-slate-700">批次错误</summary>
                          <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-xs leading-relaxed text-slate-600">
                            {JSON.stringify(lastSubAgentBatch.errors, null, 2)}
                          </pre>
                        </details>
                      ) : null}
                    </div>
                  ) : null}
                </PanelCard>
              </div>

              <PanelCard
                title="委派任务列表"
                icon={Bot}
                colorClass="bg-slate-100 text-slate-700"
                action={<div className="rounded-full bg-white/80 px-3 py-1 text-xs text-slate-600">缓存命中 {subAgentCacheHitCount}</div>}
              >
                {subAgents.length ? (
                  <div className="space-y-3">
                    {subAgents
                      .slice()
                      .sort((a, b) => b.created_at - a.created_at)
                      .map((task) => (
                        <SubAgentTaskCard
                          key={task.task_id}
                          task={task}
                          busy={subAgentActionTaskId === task.task_id}
                          onContinue={handleContinueSubAgent}
                          onCancel={handleCancelSubAgent}
                        />
                      ))}
                  </div>
                ) : (
                  <EmptyBlock text="当前还没有子代理任务。" />
                )}
              </PanelCard>
            </div>
          </section>

          <section>
            <SectionTitle
              icon={Brain}
              title="长期记忆"
              description="调试可调用长期记忆工具的手动写入与跨会话检索。"
            />
            <div className="grid grid-cols-1 gap-6 2xl:grid-cols-2">
              <PanelCard title="检索长期记忆" icon={Search} colorClass="bg-amber-50 text-amber-700">
                <TextInput
                  value={memoryQuery}
                  onChange={setMemoryQuery}
                  placeholder="输入语义检索内容。"
                  rows={3}
                />
                <div className="grid grid-cols-1 gap-3 md:grid-cols-[160px_1fr]">
                  <InlineInput value={memoryLimit} onChange={setMemoryLimit} placeholder="结果条数" type="number" />
                  <label className="flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700">
                    <input
                      type="checkbox"
                      checked={includeHistorical}
                      onChange={(event) => setIncludeHistorical(event.target.checked)}
                    />
                    包含历史记忆
                  </label>
                </div>
                <div>
                  <SmallButton variant="primary" busy={searchingMemory} onClick={() => void handleSearchMemory()}>
                    开始检索
                  </SmallButton>
                </div>

                {memoryResults.length ? (
                  <div className="space-y-3">
                    {memoryResults.map((item, index) => (
                      <MemorySearchResultCard key={`${item.id ?? "memory-result"}-${index}`} item={item} />
                    ))}
                  </div>
                ) : (
                  <EmptyBlock text="当前还没有执行长期记忆检索。" />
                )}
              </PanelCard>

              <PanelCard title="写入长期记忆" icon={Brain} colorClass="bg-violet-50 text-violet-700">
                <TextInput
                  value={memoryText}
                  onChange={setMemoryText}
                  placeholder="输入要写入的原始文本。"
                  rows={3}
                />
                <TextInput
                  value={memoryPersonalizedText}
                  onChange={setMemoryPersonalizedText}
                  placeholder="可选 personalizedText，用于更自然的召回表达。"
                  rows={2}
                />
                <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                  <InlineInput value={memoryTextType} onChange={setMemoryTextType} placeholder="记忆类型" />
                  <InlineInput value={memoryImportance} onChange={setMemoryImportance} placeholder="重要度" type="number" />
                  <InlineInput value={memoryTtlDays} onChange={setMemoryTtlDays} placeholder="TTL 天数" type="number" />
                </div>
                <div>
                  <SmallButton variant="primary" busy={storingMemory} onClick={() => void handleStoreMemory()}>
                    写入记忆
                  </SmallButton>
                </div>
              </PanelCard>
            </div>
          </section>

          <section>
            <SectionTitle
              icon={Wrench}
              title="MCP 动态工具"
              description="查看当前已发现的 MCP 工具，并从界面刷新运行时动态注册表。"
            />
            <PanelCard
              title="MCP 动态工具注册表"
              icon={Wrench}
              colorClass="bg-emerald-50 text-emerald-700"
              action={
                <SmallButton busy={mcpRefreshing} onClick={() => void handleRefreshMcp()}>
                  刷新 MCP
                </SmallButton>
              }
            >
              <div className="flex flex-wrap gap-2">
                <InfoPill label="数量" value={mcpTools.length} />
              </div>
              {mcpTools.length ? (
                <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                  {mcpTools.map((tool) => (
                    <MCPToolCard key={`${tool.server_name}-${tool.tool_name}`} tool={tool} />
                  ))}
                </div>
              ) : (
                <EmptyBlock text="当前还没有发现 MCP 工具。" />
              )}
            </PanelCard>
          </section>

          <section>
            <SectionTitle
              icon={BookOpen}
              title="Skill 自管理"
              description="创建、更新和删除受管 Skill；受管 Skill 会生成 manifest.json 与 SKILL.md，运行时代码仅支持内置信任技能。"
            />
            <div className="grid grid-cols-1 gap-6 2xl:grid-cols-[1.1fr_1.9fr]">
              <PanelCard title="创建 / 更新 Skill" icon={BookOpen} colorClass="bg-amber-50 text-amber-700">
                <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                  <InlineInput value={skillName} onChange={setSkillName} placeholder="Skill 名称" />
                  <label className="flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700">
                    <input
                      type="checkbox"
                      checked={skillEnabled}
                      onChange={(event) => setSkillEnabled(event.target.checked)}
                    />
                    已启用
                  </label>
                </div>
                <TextInput value={skillDescription} onChange={setSkillDescription} placeholder="描述" rows={2} />
                <TextInput
                  value={skillWhenToUse}
                  onChange={setSkillWhenToUse}
                  placeholder="适用场景，每行一条"
                  rows={3}
                />
                <TextInput
                  value={skillIntentExamples}
                  onChange={setSkillIntentExamples}
                  placeholder="意图示例，每行一条"
                  rows={3}
                />
                <TextInput
                  value={skillToolDefinitions}
                  onChange={setSkillToolDefinitions}
                  placeholder='工具定义 JSON 数组，例如 []'
                  rows={6}
                />
                <TextInput
                  value={skillInstructions}
                  onChange={setSkillInstructions}
                  placeholder="可选 SKILL.md 正文说明；留空时按描述、适用场景和工具自动生成"
                  rows={8}
                />
                <div>
                  <SmallButton variant="primary" busy={savingSkill} onClick={() => void handleSaveSkill()}>
                    保存 Skill
                  </SmallButton>
                </div>
              </PanelCard>

              <PanelCard title="已安装 Skills" icon={Sparkles} colorClass="bg-slate-100 text-slate-700">
                {skillDiagnostics.length ? (
                  <div className="mb-4 space-y-2">
                    {skillDiagnostics.map((item, index) => (
                      <div
                        key={`${item.folder_name}-${index}`}
                        className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800"
                      >
                        <div className="font-medium">
                          {item.severity || "warning"} · {item.folder_name || "skills"}
                        </div>
                        <div className="mt-1 break-all">{item.message}</div>
                      </div>
                    ))}
                  </div>
                ) : null}
                {skills.length ? (
                  <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                    {skills.map((skill) => (
                      <SkillCard
                        key={skill.folder_name}
                        skill={skill}
                        deleting={deletingSkillName === skill.skill_name}
                        onDelete={(name) => void handleDeleteSkill(name)}
                      />
                    ))}
                  </div>
                ) : (
                  <EmptyBlock text="当前没有可管理的 Skill。" />
                )}
              </PanelCard>
            </div>
          </section>

          <section>
            <SectionTitle
              icon={Globe}
              title="浏览器增强"
              description="展示更完整的浏览器能力状态，并支持直接打开、抽取与读取链接页。"
            />
            <div className="grid grid-cols-1 gap-6 2xl:grid-cols-[1.1fr_1.9fr]">
              <PanelCard title="浏览器控制" icon={Globe} colorClass="bg-cyan-50 text-cyan-700">
                <div className="flex flex-wrap gap-2">
                <InfoPill label="工具数量" value={browserStatus?.tool_count ?? 0} />
                <InfoPill label="控制器已初始化" value={browserStatus?.controller_initialized ?? false} />
                </div>

                <InlineInput value={browserUrl} onChange={setBrowserUrl} placeholder="https://example.com" />
                <div>
                  <SmallButton
                    variant="primary"
                    busy={runningBrowserAction === "open"}
                    onClick={() => void handleBrowserOpenTab()}
                  >
                    <ExternalLink className="h-4 w-4" />
                    打开页面
                  </SmallButton>
                </div>

                <div className="grid grid-cols-1 gap-3 md:grid-cols-[1fr_auto]">
                  <InlineInput value={browserQuery} onChange={setBrowserQuery} placeholder="搜索内容" />
                  <SmallButton
                    variant="primary"
                    busy={runningBrowserAction === "read-linked"}
                    onClick={() => void handleBrowserReadLinkedPage()}
                  >
                    搜索并读取链接页
                  </SmallButton>
                </div>

                <div>
                  <SmallButton
                    busy={runningBrowserAction === "extract"}
                    onClick={() => void handleBrowserExtractPage()}
                  >
                    抽取当前页面
                  </SmallButton>
                </div>

                <details className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
                  <summary className="cursor-pointer text-sm font-medium text-slate-700">
                    已注册浏览器工具 ({browserStatus?.tools.length ?? 0})
                  </summary>
                  <div className="mt-3 space-y-3">
                    {(browserStatus?.tools || []).map((tool) => (
                      <div key={tool.name} className="rounded-xl border border-slate-200 bg-white p-3 text-sm">
                        <div className="font-medium text-slate-800">{tool.name}</div>
                        <div className="mt-1 text-slate-600">{tool.description || "暂无描述"}</div>
                      </div>
                    ))}
                  </div>
                </details>
              </PanelCard>

              <PanelCard title="浏览器快照 / 结果" icon={Globe} colorClass="bg-slate-100 text-slate-700">
                {browserStatus?.current_page ? (
                  <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
                    <div className="flex flex-wrap gap-2">
                      <InfoPill label="标题" value={browserStatus.current_page.title || "-"} />
                      <InfoPill label="元素数" value={browserStatus.current_page.element_count ?? "-"} />
                    </div>
                    <div className="mt-3 break-all text-sm text-slate-600">{browserStatus.current_page.url || "-"}</div>
                    {browserStatus.current_page.page_text ? (
                      <div className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-slate-700">
                        {browserStatus.current_page.page_text}
                      </div>
                    ) : null}
                    {browserStatus.current_page.error ? (
                      <div className="mt-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                        {browserStatus.current_page.error}
                      </div>
                    ) : null}
                  </div>
                ) : (
                  <EmptyBlock text="浏览器控制器尚未初始化。" />
                )}

                {browserResult ? (
                  <details className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3" open>
                    <summary className="cursor-pointer text-sm font-medium text-slate-700">最近一次浏览器操作结果</summary>
                    <pre className="mt-3 overflow-x-auto whitespace-pre-wrap text-xs leading-relaxed text-slate-600">
                      {JSON.stringify(browserResult, null, 2)}
                    </pre>
                  </details>
                ) : null}
              </PanelCard>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
