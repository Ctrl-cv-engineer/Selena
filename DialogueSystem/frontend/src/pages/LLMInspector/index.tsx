import { useCallback, useEffect, useState } from "react";
import {
  Filter,
  RefreshCw,
  Cpu,
} from "lucide-react";
import clsx from "clsx";

import { LLMLogEntryCard } from "@/components/LLMLog/LogEntryCard";
import { api } from "@/services/api";
import type { LLMCallLog } from "@/types";

// ---- Caller badge colors ----
const CALLER_COLORS: Record<string, string> = {
  Agent: "bg-indigo-100 text-indigo-700 border-indigo-200",
  RolePlay: "bg-pink-100 text-pink-700 border-pink-200",
  Simple: "bg-amber-100 text-amber-700 border-amber-200",
  RAG_Reply: "bg-emerald-100 text-emerald-700 border-emerald-200",
  topic_same: "bg-cyan-100 text-cyan-700 border-cyan-200",
  topic_archive_summary: "bg-teal-100 text-teal-700 border-teal-200",
  context_summary: "bg-violet-100 text-violet-700 border-violet-200",
  core_memory_update: "bg-sky-100 text-sky-700 border-sky-200",
  silence_follow_up: "bg-rose-100 text-rose-700 border-rose-200",
  intention_example_gen: "bg-orange-100 text-orange-700 border-orange-200",
  history_summary: "bg-lime-100 text-lime-700 border-lime-200",
  memory_conflict: "bg-fuchsia-100 text-fuchsia-700 border-fuchsia-200",
};

function getCallerColor(caller: string) {
  return CALLER_COLORS[caller] ?? "bg-slate-100 text-slate-700 border-slate-200";
}

// ---- Main page ----
export default function LLMInspector() {
  const [logs, setLogs] = useState<LLMCallLog[]>([]);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [filter, setFilter] = useState<string>("");

  const loadLogs = useCallback(async () => {
    setRefreshing(true);
    try {
      const response = await api.getLLMLogs();
      setLogs(response.logs ?? []);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load LLM logs");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void loadLogs();
    const timer = window.setInterval(() => {
      void loadLogs();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [loadLogs]);

  // Derive unique callers for filter
  const callers = Array.from(new Set(logs.map((l) => l.caller))).sort();

  const filteredLogs = filter
    ? logs.filter((l) => l.caller === filter)
    : logs;

  // Show newest first
  const sortedLogs = [...filteredLogs].reverse();

  return (
    <div className="flex h-full flex-col bg-slate-50">
      {/* Header */}
      <header className="z-10 flex items-center justify-between border-b border-slate-200 bg-white px-6 py-4 shadow-sm">
        <div className="flex items-center gap-3">
          <Cpu className="h-6 w-6 text-indigo-600" />
          <div>
            <h1 className="text-xl font-bold text-slate-800">LLM API Messages Inspector</h1>
            <p className="text-sm text-slate-500">
              Real-time view of all LLM API calls with full message payloads
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* Filter dropdown */}
          <div className="flex items-center gap-2">
            <Filter className="h-4 w-4 text-slate-400" />
            <select
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700 focus:border-indigo-300 focus:outline-none focus:ring-1 focus:ring-indigo-200"
            >
              <option value="">All callers ({logs.length})</option>
              {callers.map((caller) => (
                <option key={caller} value={caller}>
                  {caller} ({logs.filter((l) => l.caller === caller).length})
                </option>
              ))}
            </select>
          </div>

          <button
            onClick={() => void loadLogs()}
            className="flex items-center gap-2 rounded-lg bg-slate-100 px-3 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-200"
          >
            <RefreshCw className={clsx("h-4 w-4", refreshing && "animate-spin")} />
            <span>Refresh</span>
          </button>
        </div>
      </header>

      {/* Error banner */}
      {error && (
        <div className="mx-6 mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Log list */}
      <div className="flex-1 overflow-y-auto p-6">
        {sortedLogs.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-slate-400">
            <Cpu className="mb-4 h-12 w-12 opacity-30" />
            <p className="text-lg font-medium">No LLM calls recorded yet</p>
            <p className="mt-1 text-sm">Send a message in Chat to trigger LLM calls</p>
          </div>
        ) : (
          <div className="space-y-3">
            {sortedLogs.map((log) => (
              <LLMLogEntryCard
                key={log.id}
                log={log}
                getCallerBadgeClass={(caller) => getCallerColor(caller)}
                getCallerLabel={(caller) => caller}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
