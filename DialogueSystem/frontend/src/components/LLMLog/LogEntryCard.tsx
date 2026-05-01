import { useState } from "react";
import { ChevronDown, ChevronRight, Hash, MessageSquare, Timer } from "lucide-react";
import clsx from "clsx";

import { formatLLMLogMessageContent } from "@/lib/llmLogContent";
import type { LLMCallLog, LLMCallMessage } from "@/types";

const ROLE_STYLES: Record<string, string> = {
  system: "border-purple-200 bg-purple-50 text-purple-800",
  user: "border-blue-200 bg-blue-50 text-blue-800",
  assistant: "border-green-200 bg-green-50 text-green-800",
  tool: "border-amber-200 bg-amber-50 text-amber-800",
};

const ROLE_BADGE: Record<string, string> = {
  system: "bg-purple-100 text-purple-700",
  user: "bg-blue-100 text-blue-700",
  assistant: "bg-green-100 text-green-700",
  tool: "bg-amber-100 text-amber-700",
};

function formatDuration(ms: number | undefined): string {
  if (!ms) return "-";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function MessageCard({ message }: { message: LLMCallMessage }) {
  const [open, setOpen] = useState(false);
  const content = formatLLMLogMessageContent(message.content);
  const preview = content.length > 120 ? content.slice(0, 120) + "…" : content;

  return (
    <div className={clsx("rounded-md border p-3", ROLE_STYLES[message.role] ?? "border-slate-200 bg-slate-50 text-slate-800")}>
      <button
        type="button"
        className="flex w-full items-start gap-2 text-left"
        onClick={() => setOpen((value) => !value)}
      >
        <span className={clsx("shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold uppercase", ROLE_BADGE[message.role] ?? "bg-slate-100 text-slate-600")}>
          {message.role}
        </span>
        <span className="min-w-0 text-xs leading-relaxed whitespace-pre-wrap break-words">
          {open ? content : preview}
        </span>
      </button>
    </div>
  );
}

type LogEntryCardProps = {
  log: LLMCallLog;
  getCallerBadgeClass?: (caller: string, log: LLMCallLog) => string;
  getCallerLabel?: (caller: string, log: LLMCallLog) => string;
};

export function LLMLogEntryCard({
  log,
  getCallerBadgeClass,
  getCallerLabel,
}: LogEntryCardProps) {
  const [open, setOpen] = useState(false);
  const callerClass =
    getCallerBadgeClass?.(log.caller, log) ?? "bg-slate-100 text-slate-700 border-slate-200";
  const callerLabel = getCallerLabel?.(log.caller, log) ?? log.caller;
  const tools = Array.isArray(log.extra?.tools) ? log.extra.tools : [];

  return (
    <div className="rounded-lg border border-slate-200 bg-white shadow-sm">
      <button
        type="button"
        className="flex w-full items-center gap-3 p-4 text-left hover:bg-slate-50"
        onClick={() => setOpen((value) => !value)}
      >
        {open ? <ChevronDown className="h-4 w-4 shrink-0 text-slate-400" /> : <ChevronRight className="h-4 w-4 shrink-0 text-slate-400" />}
        <span className={clsx("shrink-0 rounded-full border px-2.5 py-0.5 text-xs font-semibold", callerClass)}>
          {callerLabel}
        </span>
        <span className="font-mono text-xs text-slate-500">{log.model_name || log.model_key || "unknown"}</span>
        <div className="ml-auto flex items-center gap-3 text-xs text-slate-400">
          {log.thinking && <span className="rounded bg-indigo-50 px-1.5 py-0.5 font-medium text-indigo-600">思考</span>}
          {log.json_mode && <span className="rounded bg-cyan-50 px-1.5 py-0.5 font-medium text-cyan-600">JSON</span>}
          {log.duration_ms != null && (
            <span className="flex items-center gap-1">
              <Timer className="h-3 w-3" />
              {formatDuration(log.duration_ms)}
            </span>
          )}
          <span className="flex items-center gap-1">
            <MessageSquare className="h-3 w-3" />
            {log.messages.length}
          </span>
          <span className="flex items-center gap-1">
            <Hash className="h-3 w-3" />
            {log.id}
          </span>
          {log.status && log.status !== "completed" && (
            <span className="rounded bg-red-50 px-1.5 py-0.5 font-medium text-red-600">{log.status}</span>
          )}
        </div>
      </button>

      {open && (
        <div className="space-y-2 border-t border-slate-100 p-4">
          <div className="mb-3 flex flex-wrap gap-4 text-xs text-slate-400">
            <span>时间: {log.timestamp}</span>
            {log.completed_at && <span>完成: {log.completed_at}</span>}
            <span>Caller: {log.caller}</span>
            {log.reasoning_effort && <span>推理强度: {log.reasoning_effort}</span>}
            {log.stream && <span>流式: 是</span>}
          </div>
          {log.messages.map((message, index) => (
            <MessageCard key={`${log.id}-message-${index}`} message={message} />
          ))}
          {tools.length > 0 && (
            <div className="mt-2">
              <h4 className="mb-1 text-xs font-semibold text-slate-500">工具定义 ({tools.length})</h4>
              <pre className="max-h-40 overflow-auto rounded-md bg-slate-50 p-2 text-[10px] text-slate-600">
                {JSON.stringify(tools, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
