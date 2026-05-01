import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Command,
  Eraser,
  HelpCircle,
  LoaderCircle,
  MessageSquare,
  RefreshCw,
  RotateCcw,
  Send,
  ShieldAlert,
  User,
  Wrench,
  XCircle,
} from "lucide-react";
import clsx from "clsx";

import { api } from "@/services/api";
import type {
  RuntimeAgentStep,
  RuntimeMessage,
  RuntimeState,
  RuntimeToolApproval,
  RuntimeToolSecurityEvent,
} from "@/types";

type TurnArtifacts = {
  steps: RuntimeAgentStep[];
  events: RuntimeToolSecurityEvent[];
  approvals: RuntimeToolApproval[];
};

type ConversationBlock = {
  key: string;
  userMessage: RuntimeMessage | null;
  replies: RuntimeMessage[];
  inputCount: number | null;
};

type ChatCommandName = "clear" | "continue" | "refresh" | "help";

const CHAT_COMMANDS: Array<{
  name: ChatCommandName;
  usage: string;
  title: string;
  description: string;
}> = [
  {
    name: "clear",
    usage: "/clear",
    title: "清空当前对话",
    description: "清空前端可见对话，并重置 Agent 与 RolePlay 上下文。",
  },
  {
    name: "continue",
    usage: "/continue",
    title: "继续上次对话",
    description: "恢复最近一次清空前保存的对话，程序重启后也可使用。",
  },
  {
    name: "refresh",
    usage: "/refresh",
    title: "刷新运行状态",
    description: "重新拉取后端 runtime，不发送新消息。",
  },
  {
    name: "help",
    usage: "/help",
    title: "显示指令说明",
    description: "展开这份指令列表。",
  },
];

function formatClock(timestamp: string) {
  if (!timestamp) return "--:--";
  return timestamp.length >= 16 ? timestamp.slice(11, 16) : timestamp;
}

function parseTimestamp(timestamp: string) {
  if (!timestamp) return 0;
  const normalized = timestamp.includes("T") ? timestamp : timestamp.replace(" ", "T");
  const parsed = Date.parse(normalized);
  return Number.isNaN(parsed) ? 0 : parsed;
}

function getBubbleStyle(role: string) {
  if (role === "user") {
    return {
      container: "ml-auto justify-end",
      bubble: "bg-blue-600 text-white rounded-br-none",
      icon: <User className="h-5 w-5 text-white" />,
      iconClass: "bg-blue-600 ml-3",
    };
  }
  return {
    container: "mr-auto justify-start",
    bubble: "bg-white text-slate-800 border border-slate-200 rounded-bl-none",
    icon: <Bot className="h-5 w-5 text-white" />,
    iconClass: "bg-indigo-600 mr-3",
  };
}

function getCommandIcon(name: ChatCommandName) {
  if (name === "clear") return <Eraser className="h-4 w-4" />;
  if (name === "continue") return <RotateCcw className="h-4 w-4" />;
  if (name === "refresh") return <RefreshCw className="h-4 w-4" />;
  return <HelpCircle className="h-4 w-4" />;
}

function mergeRuntimeState(previous: RuntimeState | null, next: RuntimeState): RuntimeState {
  if (!previous) {
    return next;
  }

  const previousConversation = previous.conversation ?? [];
  const nextConversation = next.conversation ?? [];
  const optimisticMessage = previousConversation[previousConversation.length - 1];

  if (!optimisticMessage?.id?.startsWith("optimistic-user-")) {
    return next;
  }

  const alreadyPersisted = nextConversation.some(
    (message) =>
      message.role === "user" &&
      message.content === optimisticMessage.content &&
      message.topic_group === optimisticMessage.topic_group
  );

  if (alreadyPersisted) {
    return next;
  }

  return {
    ...next,
    conversation: [...nextConversation, optimisticMessage],
  };
}

function buildConversationBlocks(conversation: RuntimeMessage[]) {
  const blocks: ConversationBlock[] = [];
  let currentBlock: ConversationBlock | null = null;
  let inputCount = 0;

  conversation.forEach((message) => {
    if (message.role === "user") {
      if (currentBlock) {
        blocks.push(currentBlock);
      }
      inputCount += 1;
      currentBlock = {
        key: `turn-${inputCount}`,
        userMessage: message,
        replies: [],
        inputCount,
      };
      return;
    }

    if (currentBlock) {
      currentBlock.replies.push(message);
      return;
    }

    blocks.push({
      key: message.id,
      userMessage: null,
      replies: [message],
      inputCount: null,
    });
  });

  if (currentBlock) {
    blocks.push(currentBlock);
  }

  return blocks;
}

function sortSteps(steps: RuntimeAgentStep[]) {
  return [...steps].sort((left, right) => {
    const byTime = parseTimestamp(left.timestamp) - parseTimestamp(right.timestamp);
    if (byTime !== 0) return byTime;
    return (left.step || 0) - (right.step || 0);
  });
}

function sortEvents(events: RuntimeToolSecurityEvent[]) {
  return [...events].sort((left, right) => parseTimestamp(left.timestamp) - parseTimestamp(right.timestamp));
}

function sortApprovals(approvals: RuntimeToolApproval[]) {
  return [...approvals].sort((left, right) => parseTimestamp(left.created_at) - parseTimestamp(right.created_at));
}

function mergeSteps(existing: RuntimeAgentStep[], incoming: RuntimeAgentStep[]) {
  const merged = new Map<string, RuntimeAgentStep>();

  existing.forEach((step) => {
    merged.set(step.id || `${step.step}-${step.phase}-${step.tool_name}-${step.title}`, step);
  });

  incoming.forEach((step) => {
    merged.set(step.id || `${step.step}-${step.phase}-${step.tool_name}-${step.title}`, step);
  });

  return sortSteps(Array.from(merged.values()));
}

function mergeEvents(existing: RuntimeToolSecurityEvent[], incoming: RuntimeToolSecurityEvent[]) {
  const merged = new Map<string, RuntimeToolSecurityEvent>();

  existing.forEach((event) => {
    merged.set(event.id || `${event.event_type}-${event.tool_name}-${event.timestamp}`, event);
  });

  incoming.forEach((event) => {
    merged.set(event.id || `${event.event_type}-${event.tool_name}-${event.timestamp}`, event);
  });

  return sortEvents(Array.from(merged.values()));
}

function mergeApprovals(existing: RuntimeToolApproval[], incoming: RuntimeToolApproval[]) {
  const merged = new Map<string, RuntimeToolApproval>();

  existing.forEach((approval) => {
    merged.set(approval.approval_id, approval);
  });

  incoming.forEach((approval) => {
    merged.set(approval.approval_id, { ...merged.get(approval.approval_id), ...approval });
  });

  return sortApprovals(Array.from(merged.values()));
}

function resolveTurnKey(
  item: {
    input_count?: number;
    timestamp?: string;
    created_at?: string;
  },
  blocks: ConversationBlock[],
  latestTurnKey: string
) {
  const inputCount = Number(item.input_count || 0);
  if (inputCount > 0) {
    const matchedBlock = blocks.find((block) => block.inputCount === inputCount);
    if (matchedBlock) {
      return matchedBlock.key;
    }
  }

  const eventTimestamp = parseTimestamp(item.timestamp || item.created_at || "");
  if (eventTimestamp > 0) {
    const userBlocks = blocks.filter((block) => block.userMessage);
    for (let index = 0; index < userBlocks.length; index += 1) {
      const currentBlock = userBlocks[index];
      const nextBlock = userBlocks[index + 1];
      const currentTimestamp = parseTimestamp(currentBlock.userMessage?.timestamp || "");
      const nextTimestamp = nextBlock ? parseTimestamp(nextBlock.userMessage?.timestamp || "") : Number.POSITIVE_INFINITY;

      if (currentTimestamp <= eventTimestamp && eventTimestamp < nextTimestamp) {
        return currentBlock.key;
      }
    }
  }

  return latestTurnKey;
}

function syncTurnArtifacts(
  previous: Record<string, TurnArtifacts>,
  runtime: RuntimeState | null,
  blocks: ConversationBlock[]
) {
  if (!runtime) {
    return previous;
  }

  const next: Record<string, TurnArtifacts> = { ...previous };
  const latestTurnKey = [...blocks].reverse().find((block) => block.inputCount !== null)?.key ?? "";

  blocks.forEach((block) => {
    if (block.inputCount === null) {
      return;
    }
    next[block.key] = next[block.key] ?? { steps: [], events: [], approvals: [] };
  });

  (runtime.agent_steps ?? []).forEach((step) => {
    const turnKey = resolveTurnKey(step, blocks, latestTurnKey);
    if (!turnKey) {
      return;
    }
    const currentTurnArtifacts = next[turnKey] ?? { steps: [], events: [], approvals: [] };
    next[turnKey] = {
      ...currentTurnArtifacts,
      steps: mergeSteps(currentTurnArtifacts.steps, [step]),
    };
  });

  (runtime.recent_tool_security_events ?? []).forEach((event) => {
    const turnKey = resolveTurnKey(event, blocks, latestTurnKey);
    if (!turnKey) {
      return;
    }
    const currentTurnArtifacts = next[turnKey] ?? { steps: [], events: [], approvals: [] };
    next[turnKey] = {
      ...currentTurnArtifacts,
      events: mergeEvents(currentTurnArtifacts.events, [event]),
    };
  });

  (runtime.pending_tool_approvals ?? []).forEach((approval) => {
    const turnKey = resolveTurnKey(approval, blocks, latestTurnKey);
    if (!turnKey) {
      return;
    }
    const currentTurnArtifacts = next[turnKey] ?? { steps: [], events: [], approvals: [] };
    next[turnKey] = {
      ...currentTurnArtifacts,
      approvals: mergeApprovals(currentTurnArtifacts.approvals, [approval]),
    };
  });

  return next;
}

function AgentStepCard({ step }: { step: RuntimeAgentStep }) {
  const isRunning = step.status === "running";
  const isFailed = step.status === "failed";

  return (
    <div
      className={clsx(
        "rounded-xl border px-3 py-3",
        isFailed
          ? "border-red-200 bg-red-50"
          : isRunning
            ? "border-amber-200 bg-amber-50"
            : "border-slate-200 bg-slate-50"
      )}
    >
      <div className="flex items-start gap-3">
        <div
          className={clsx(
            "mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full",
            isFailed
              ? "bg-red-100 text-red-600"
              : isRunning
                ? "bg-amber-100 text-amber-700"
                : "bg-emerald-100 text-emerald-700"
          )}
        >
          {isRunning ? (
            <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <span className="text-[11px] font-semibold">{step.step}</span>
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm font-medium text-slate-800">{step.title}</p>
            <span className="text-[11px] text-slate-400">{formatClock(step.timestamp)}</span>
          </div>
          {step.detail ? (
            <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-slate-600">{step.detail}</p>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function SecurityBadge({ event }: { event: RuntimeToolSecurityEvent }) {
  const normalizedStatus = String(event.status || "").toLowerCase();
  const className =
    normalizedStatus === "blocked" || normalizedStatus === "rejected"
      ? "border-red-200 bg-red-50 text-red-700"
      : normalizedStatus === "pending"
        ? "border-amber-200 bg-amber-50 text-amber-700"
        : "border-emerald-200 bg-emerald-50 text-emerald-700";

  return (
    <div className={clsx("rounded-lg border px-2.5 py-2 text-xs", className)}>
      <div className="flex items-center gap-2 font-medium">
        <ShieldAlert className="h-3.5 w-3.5" />
        <span>{event.tool_name || event.event_type}</span>
        <span className="ml-auto text-[11px] opacity-80">{formatClock(event.timestamp)}</span>
      </div>
      <div className="mt-1 text-[11px] leading-[1.35rem] opacity-90">{event.detail}</div>
      {event.payload?.backend ? (
        <div className="mt-1 text-[11px] opacity-80">backend: {String(event.payload.backend)}</div>
      ) : null}
      {event.payload?.checkpoint && typeof event.payload.checkpoint === "object" ? (
        <div className="mt-1 text-[11px] opacity-80">checkpoint 已创建</div>
      ) : null}
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
  onResolve: (approvalId: string, decision: "approved" | "rejected") => Promise<void>;
}) {
  const isResolved = approval.status !== "pending";

  return (
    <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-4 shadow-sm">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 rounded-full bg-amber-100 p-2 text-amber-700">
          <AlertTriangle className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-amber-900">工具审批请求</div>
              <div className="text-xs text-amber-800/80">{approval.tool_name}</div>
            </div>
            <div className="text-[11px] text-amber-900/70">{formatClock(approval.created_at)}</div>
          </div>

          <div className="mt-2 whitespace-pre-wrap rounded-xl bg-white/70 px-3 py-2 text-xs leading-5 text-slate-700">
            {JSON.stringify(approval.arguments ?? {}, null, 2)}
          </div>

          <div className="mt-2 text-xs text-amber-900/80">
            {String((approval.policy as Record<string, unknown>)?.reason || "该工具调用需要手动审批。")}
          </div>

          {isResolved ? (
            <div className="mt-3 text-xs font-medium text-slate-600">
              当前状态: {approval.status === "approved" ? "已批准" : "已拒绝"}
            </div>
          ) : (
            <div className="mt-3 flex gap-2">
              <button
                disabled={busy}
                onClick={() => void onResolve(approval.approval_id, "approved")}
                className="inline-flex items-center gap-2 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-medium text-white transition hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-60"
              >
                <CheckCircle2 className="h-4 w-4" />
                批准
              </button>
              <button
                disabled={busy}
                onClick={() => void onResolve(approval.approval_id, "rejected")}
                className="inline-flex items-center gap-2 rounded-lg bg-red-600 px-3 py-2 text-xs font-medium text-white transition hover:bg-red-500 disabled:cursor-not-allowed disabled:opacity-60"
              >
                <XCircle className="h-4 w-4" />
                拒绝
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function TurnExecutionPanel({
  artifacts,
  collapsed,
  busyApprovalId,
  onResolveApproval,
  onToggle,
}: {
  artifacts: TurnArtifacts;
  collapsed: boolean;
  busyApprovalId: string;
  onResolveApproval: (approvalId: string, decision: "approved" | "rejected") => Promise<void>;
  onToggle: () => void;
}) {
  const pendingApprovals = artifacts.approvals.filter((approval) => approval.status === "pending");
  const hasContent = artifacts.steps.length > 0 || artifacts.events.length > 0 || artifacts.approvals.length > 0;

  if (!hasContent) {
    return null;
  }

  return (
    <div className="mr-auto flex max-w-3xl justify-start">
      <div className="mr-3 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-amber-600">
        <Wrench className="h-4 w-4 text-white" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="overflow-hidden rounded-2xl rounded-bl-none border border-slate-200 bg-white shadow-sm">
          <button
            type="button"
            onClick={onToggle}
            className="flex w-full items-center justify-between gap-3 border-b border-slate-100 bg-slate-50 px-4 py-3 text-left"
          >
            <div>
              <p className="text-sm font-semibold text-slate-800">本轮执行详情</p>
              <p className="text-xs text-slate-500">
                Agent 链路 {artifacts.steps.length} 条，安全事件 {artifacts.events.length} 条，审批 {pendingApprovals.length} 条
              </p>
            </div>
            <div className="shrink-0 text-slate-400">
              {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            </div>
          </button>

          {!collapsed ? (
            <div className="space-y-3 px-4 py-3">
              {artifacts.approvals.map((approval) => (
                <ApprovalCard
                  key={approval.approval_id}
                  approval={approval}
                  busy={busyApprovalId === approval.approval_id}
                  onResolve={onResolveApproval}
                />
              ))}

              {artifacts.steps.map((step) => (
                <AgentStepCard key={step.id} step={step} />
              ))}

              {artifacts.events.length > 0 ? (
                <div className="max-h-36 space-y-2 overflow-y-auto pr-1">
                  {artifacts.events.map((event) => (
                    <SecurityBadge key={event.id} event={event} />
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function MessageBubble({ message }: { message: RuntimeMessage }) {
  const styles = getBubbleStyle(message.role);

  return (
    <div className={clsx("flex max-w-3xl", styles.container)}>
      {message.role !== "user" && (
        <div
          className={clsx(
            "mt-auto flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full",
            styles.iconClass
          )}
        >
          {styles.icon}
        </div>
      )}

      <div className="flex flex-col">
        <div className={clsx("rounded-2xl px-4 py-3 shadow-sm", styles.bubble)}>
          <p className="whitespace-pre-wrap leading-relaxed">{message.content}</p>
        </div>
        <span
          className={clsx("mt-1 text-xs text-slate-400", message.role === "user" ? "mr-1 text-right" : "ml-1")}
        >
          {formatClock(message.timestamp)}
        </span>
      </div>

      {message.role === "user" && (
        <div
          className={clsx(
            "mt-auto flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full",
            styles.iconClass
          )}
        >
          {styles.icon}
        </div>
      )}
    </div>
  );
}

export default function Chat() {
  const [runtime, setRuntime] = useState<RuntimeState | null>(null);
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [isCommandRunning, setIsCommandRunning] = useState(false);
  const [showCommandHelp, setShowCommandHelp] = useState(false);
  const [error, setError] = useState("");
  const [approvalBusyId, setApprovalBusyId] = useState("");
  const [turnArtifactsByKey, setTurnArtifactsByKey] = useState<Record<string, TurnArtifacts>>({});
  const [collapsedTurns, setCollapsedTurns] = useState<Record<string, boolean>>({});

  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const previousLatestTurnKeyRef = useRef("");
  const shouldAutoScrollRef = useRef(true);
  const forceScrollRef = useRef(false);

  const conversation = runtime?.conversation ?? [];
  const blocks = useMemo(() => buildConversationBlocks(conversation), [conversation]);
  const latestTurnKey = [...blocks].reverse().find((block) => block.inputCount !== null)?.key ?? "";
  const latestTurnArtifacts = latestTurnKey ? turnArtifactsByKey[latestTurnKey] : undefined;
  const showTypingBubble = isSending && (latestTurnArtifacts?.steps.length ?? 0) === 0;
  const normalizedCommandInput = input.trim().startsWith("/") ? input.trim().slice(1).toLowerCase() : "";
  const commandToken = normalizedCommandInput.split(/\s+/)[0] ?? "";
  const commandSuggestions = useMemo(() => {
    if (!input.trim().startsWith("/")) {
      return CHAT_COMMANDS;
    }
    return CHAT_COMMANDS.filter(
      (command) =>
        command.name.startsWith(commandToken) ||
        command.usage.toLowerCase().includes(commandToken)
    );
  }, [input, commandToken]);
  const shouldShowCommandPanel = showCommandHelp || input.trim().startsWith("/");

  useEffect(() => {
    let mounted = true;

    api
      .getRuntime()
      .then((state) => {
        if (!mounted) {
          return;
        }
        setRuntime(state);
        forceScrollRef.current = true;
      })
      .catch((requestError) => {
        if (mounted) {
          setError(requestError instanceof Error ? requestError.message : "加载失败");
        }
      });

    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    setTurnArtifactsByKey((previous) => syncTurnArtifacts(previous, runtime, blocks));
  }, [runtime, blocks]);

  useEffect(() => {
    const userTurnKeys = blocks.filter((block) => block.inputCount !== null).map((block) => block.key);
    if (userTurnKeys.length === 0 || !latestTurnKey) {
      previousLatestTurnKeyRef.current = latestTurnKey;
      return;
    }

    setCollapsedTurns((previous) => {
      const next = { ...previous };
      const previousLatestTurnKey = previousLatestTurnKeyRef.current;

      if (!previousLatestTurnKey) {
        userTurnKeys.slice(0, -1).forEach((turnKey) => {
          if (next[turnKey] === undefined) {
            next[turnKey] = true;
          }
        });
        if (next[latestTurnKey] === undefined) {
          next[latestTurnKey] = false;
        }
        return next;
      }

      if (previousLatestTurnKey !== latestTurnKey) {
        next[previousLatestTurnKey] = true;
        next[latestTurnKey] = false;
      }

      return next;
    });

    previousLatestTurnKeyRef.current = latestTurnKey;
  }, [blocks, latestTurnKey]);

  useEffect(() => {
    let cancelled = false;

    const pollRuntime = async () => {
      try {
        const nextRuntime = await api.getRuntime();
        if (!cancelled) {
          setRuntime((previous) => mergeRuntimeState(previous, nextRuntime));
        }
      } catch {
        // Ignore transient polling errors and keep the next polling tick alive.
      }
    };

    void pollRuntime();
    const timer = window.setInterval(() => {
      void pollRuntime();
    }, isSending ? 500 : 1500);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [isSending]);

  useEffect(() => {
    if (!(forceScrollRef.current || shouldAutoScrollRef.current)) {
      return;
    }

    const behavior: ScrollBehavior = forceScrollRef.current ? "smooth" : "auto";
    forceScrollRef.current = false;

    window.requestAnimationFrame(() => {
      messagesEndRef.current?.scrollIntoView({ behavior, block: "end" });
    });
  }, [blocks, turnArtifactsByKey, isSending, error]);

  function handleScroll() {
    const container = scrollContainerRef.current;
    if (!container) {
      return;
    }
    const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    shouldAutoScrollRef.current = distanceFromBottom < 96;
  }

  function applyCommandRuntimeState(state: RuntimeState) {
    setRuntime(state);
    setTurnArtifactsByKey({});
    setCollapsedTurns({});
    previousLatestTurnKeyRef.current = "";
    shouldAutoScrollRef.current = true;
    forceScrollRef.current = true;
  }

  function focusInput() {
    window.requestAnimationFrame(() => {
      inputRef.current?.focus();
    });
  }

  async function runChatCommand(rawCommand: string) {
    const commandName = rawCommand.trim().slice(1).split(/\s+/)[0]?.toLowerCase() as ChatCommandName | "";
    if (!commandName) {
      setShowCommandHelp(true);
      focusInput();
      return;
    }

    const knownCommand = CHAT_COMMANDS.find((command) => command.name === commandName);
    if (!knownCommand) {
      setError(`未知指令：/${commandName}。输入 / 查看可用指令。`);
      setShowCommandHelp(true);
      focusInput();
      return;
    }

    setIsCommandRunning(true);
    setError("");

    try {
      if (commandName === "help") {
        setShowCommandHelp(true);
        setInput("");
        return;
      }

      if (commandName === "refresh") {
        const state = await api.getRuntime();
        setRuntime(state);
        forceScrollRef.current = true;
        setInput("");
        setShowCommandHelp(false);
        return;
      }

      const state = commandName === "clear" ? await api.clearDialogue() : await api.continueDialogue();
      applyCommandRuntimeState(state);
      setInput("");
      setShowCommandHelp(false);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "指令执行失败");
    } finally {
      setIsCommandRunning(false);
    }
  }

  async function handleSend() {
    const message = input.trim();
    if (!message || isSending || isCommandRunning) {
      return;
    }

    if (message.startsWith("/")) {
      await runChatCommand(message);
      return;
    }

    const optimisticMessage: RuntimeMessage = {
      id: `optimistic-user-${Date.now()}`,
      topic_group: runtime?.meta.topic_group ?? 0,
      role: "user",
      content: message,
      timestamp: new Date().toISOString().replace("T", " ").slice(0, 19),
    };

    setRuntime((previous) => {
      if (previous) {
        return {
          ...previous,
          conversation: [...previous.conversation, optimisticMessage],
          meta: {
            ...previous.meta,
            input_count: previous.meta.input_count + 1,
          },
        };
      }

      return {
        conversation: [optimisticMessage],
        agent_steps: [],
        pending_tool_approvals: [],
        recent_tool_security_events: [],
        contexts: {
          agent: [],
          role_play: [],
          simple: [],
        },
        due_tasks: [],
        meta: {
          topic_group: 0,
          input_count: 1,
          runtime_initialized: false,
        },
      };
    });

    setInput("");
    setIsSending(true);
    setError("");
    shouldAutoScrollRef.current = true;
    forceScrollRef.current = true;

    try {
      const state = await api.sendMessage(message);
      setRuntime(state);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "发送失败");
    } finally {
      setIsSending(false);
    }
  }

  async function handleResolveApproval(approvalId: string, decision: "approved" | "rejected") {
    setApprovalBusyId(approvalId);
    setError("");

    try {
      const result = await api.resolveToolApproval({ approval_id: approvalId, decision });
      setTurnArtifactsByKey((previous) => {
        const next: Record<string, TurnArtifacts> = {};

        Object.entries(previous).forEach(([turnKey, artifacts]) => {
          const targetApproval = artifacts.approvals.find((approval) => approval.approval_id === approvalId);
          next[turnKey] = targetApproval
            ? {
                ...artifacts,
                approvals: mergeApprovals(artifacts.approvals, [result.approval]),
              }
            : artifacts;
        });

        return next;
      });

      const state = await api.getRuntime();
      setRuntime(state);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "审批处理失败");
    } finally {
      setApprovalBusyId("");
    }
  }

  return (
    <div className="flex h-full flex-col bg-slate-50">
      <header className="z-10 border-b border-slate-200 bg-white px-6 py-4 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold text-slate-800">实时对话</h1>
            <p className="text-sm text-slate-500">
              当前主题组 {runtime?.meta.topic_group ?? 0}，累计输入 {runtime?.meta.input_count ?? 0} 次
            </p>
          </div>
          <button
            type="button"
            title="清空当前对话"
            onClick={() => void runChatCommand("/clear")}
            disabled={isSending || isCommandRunning}
            className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 shadow-sm transition-colors hover:border-red-200 hover:bg-red-50 hover:text-red-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isCommandRunning ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Eraser className="h-4 w-4" />}
            清空对话
          </button>
        </div>
      </header>

      <div ref={scrollContainerRef} onScroll={handleScroll} className="flex-1 overflow-y-auto p-6">
        <div className="space-y-6">
          {blocks.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center text-slate-400">
              <MessageSquare className="mb-4 h-16 w-16 opacity-50" />
              <p className="text-lg">还没有真实对话数据，发送一条消息试试。</p>
            </div>
          ) : (
            blocks.map((block) => {
              const artifacts =
                block.inputCount !== null ? turnArtifactsByKey[block.key] ?? { steps: [], events: [], approvals: [] } : null;
              const isCollapsed = block.inputCount !== null ? (collapsedTurns[block.key] ?? false) : false;

              return (
                <div key={block.key} className="space-y-3">
                  {block.userMessage ? <MessageBubble message={block.userMessage} /> : null}

                  {artifacts ? (
                    <TurnExecutionPanel
                      artifacts={artifacts}
                      collapsed={isCollapsed}
                      busyApprovalId={approvalBusyId}
                      onResolveApproval={handleResolveApproval}
                      onToggle={() =>
                        setCollapsedTurns((previous) => ({
                          ...previous,
                          [block.key]: !(previous[block.key] ?? false),
                        }))
                      }
                    />
                  ) : null}

                  {block.replies.map((reply) => (
                    <MessageBubble key={reply.id} message={reply} />
                  ))}
                </div>
              );
            })
          )}

          {showTypingBubble ? (
            <div className="mr-auto flex max-w-3xl items-center justify-start space-x-2 text-slate-400">
              <div className="mr-1 mt-auto flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-indigo-600">
                <Bot className="h-5 w-5 text-white" />
              </div>
              <div className="flex items-center space-x-1 rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
                <div className="h-2 w-2 animate-bounce rounded-full bg-slate-400" style={{ animationDelay: "0ms" }} />
                <div
                  className="h-2 w-2 animate-bounce rounded-full bg-slate-400"
                  style={{ animationDelay: "150ms" }}
                />
                <div
                  className="h-2 w-2 animate-bounce rounded-full bg-slate-400"
                  style={{ animationDelay: "300ms" }}
                />
              </div>
            </div>
          ) : null}

          {error ? (
            <div className="max-w-3xl rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          ) : null}

          <div ref={messagesEndRef} />
        </div>
      </div>

      <div className="border-t border-slate-200 bg-white p-4">
        {shouldShowCommandPanel ? (
          <div className="mx-auto mb-3 max-w-4xl rounded-lg border border-slate-200 bg-slate-50 p-3 shadow-sm">
            <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-700">
              <Command className="h-4 w-4" />
              可用指令
            </div>
            <div className="grid gap-2 md:grid-cols-2">
              {(commandSuggestions.length > 0 ? commandSuggestions : CHAT_COMMANDS).map((command) => (
                <button
                  key={command.name}
                  type="button"
                  onClick={() => {
                    setInput(command.usage);
                    setShowCommandHelp(false);
                    focusInput();
                  }}
                  className="flex min-h-[64px] items-start gap-3 rounded-lg border border-slate-200 bg-white p-3 text-left transition-colors hover:border-blue-200 hover:bg-blue-50"
                >
                  <span className="mt-0.5 flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md bg-slate-100 text-slate-600">
                    {getCommandIcon(command.name)}
                  </span>
                  <span className="min-w-0">
                    <span className="block text-sm font-semibold text-slate-800">
                      <code className="mr-2 rounded bg-slate-100 px-1.5 py-0.5 text-xs text-blue-700">
                        {command.usage}
                      </code>
                      {command.title}
                    </span>
                    <span className="mt-1 block text-xs leading-5 text-slate-500">{command.description}</span>
                  </span>
                </button>
              ))}
            </div>
          </div>
        ) : null}
        <div className="relative mx-auto flex max-w-4xl items-center">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(event) => {
              setInput(event.target.value);
              if (event.target.value.trim().startsWith("/")) {
                setShowCommandHelp(false);
              }
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void handleSend();
              }
            }}
            placeholder="输入消息，或输入 / 查看指令。"
            className="w-full resize-none overflow-hidden rounded-xl border border-slate-200 bg-slate-50 py-3 pl-4 pr-12 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-blue-500"
            rows={1}
            style={{ minHeight: "48px", maxHeight: "120px" }}
          />
          <button
            onClick={() => void handleSend()}
            disabled={!input.trim() || isSending || isCommandRunning}
            className="absolute right-2 rounded-lg bg-blue-600 p-2 text-white transition-colors hover:bg-blue-700 disabled:opacity-50 disabled:hover:bg-blue-600"
          >
            {isCommandRunning ? <LoaderCircle className="h-5 w-5 animate-spin" /> : <Send className="h-5 w-5" />}
          </button>
        </div>
      </div>
    </div>
  );
}
