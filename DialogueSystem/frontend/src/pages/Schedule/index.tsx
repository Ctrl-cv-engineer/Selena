import { type ReactNode, useEffect, useState } from "react";
import { Calendar, Empty } from "antd";
import { Clock, Database, Sparkles } from "lucide-react";
import clsx from "clsx";

import { api } from "@/services/api";
import type { ScheduleTask } from "@/types";

function formatDate(date: Date) {
  return date.toISOString().slice(0, 10);
}

function formatDisplayDate(date: Date) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "long",
    day: "numeric",
    weekday: "short",
  }).format(date);
}

function parseDateTime(value: string) {
  const normalized = String(value || "").trim();
  if (!normalized) return null;
  const parsed = new Date(normalized.replace(" ", "T"));
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed;
}

function formatReminderClock(value: string) {
  const parsed = parseDateTime(value);
  if (parsed) {
    return new Intl.DateTimeFormat("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(parsed);
  }
  const timeMatch = String(value || "").match(/(\d{2}:\d{2})/);
  return timeMatch?.[1] ?? value;
}

function formatMetaTime(value: string) {
  const parsed = parseDateTime(value);
  if (parsed) {
    return new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(parsed);
  }
  return value;
}

function isCompletedTask(taskStatus: string) {
  return String(taskStatus || "").trim() === "已完成";
}

function isCancelledTask(taskStatus: string) {
  return String(taskStatus || "").trim() === "取消";
}

function isDelayedTask(taskStatus: string) {
  return String(taskStatus || "").trim() === "延迟";
}

function isRemindedTask(reminderStatus: string) {
  return String(reminderStatus || "").trim() === "已提醒";
}

function getTaskStatusClass(taskStatus: string) {
  if (isCompletedTask(taskStatus)) {
    return {
      accent: "bg-emerald-400",
      pill: "border-emerald-200 bg-emerald-50 text-emerald-700",
    };
  }
  if (isDelayedTask(taskStatus)) {
    return {
      accent: "bg-amber-400",
      pill: "border-amber-200 bg-amber-50 text-amber-700",
    };
  }
  if (isCancelledTask(taskStatus)) {
    return {
      accent: "bg-rose-400",
      pill: "border-rose-200 bg-rose-50 text-rose-700",
    };
  }
  return {
    accent: "bg-sky-400",
    pill: "border-sky-200 bg-sky-50 text-sky-700",
  };
}

function getReminderStatusClass(reminderStatus: string) {
  if (isRemindedTask(reminderStatus)) {
    return "border-violet-200 bg-violet-50 text-violet-700";
  }
  return "border-stone-200 bg-stone-100 text-stone-600";
}

function countTasks(tasks: ScheduleTask[], matcher: (task: ScheduleTask) => boolean) {
  return tasks.reduce((count, task) => count + (matcher(task) ? 1 : 0), 0);
}

function MetaPill({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={clsx(
        "inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-medium",
        className
      )}
    >
      {children}
    </span>
  );
}

export default function Schedule() {
  const [selectedDate, setSelectedDate] = useState<Date>(new Date());
  const [tasks, setTasks] = useState<ScheduleTask[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const completedCount = countTasks(tasks, (task) => isCompletedTask(task.task_status));
  const activeCount = countTasks(tasks, (task) => !isCompletedTask(task.task_status) && !isCancelledTask(task.task_status));
  const remindedCount = countTasks(tasks, (task) => isRemindedTask(task.reminder_status));

  async function loadTasks(date: Date) {
    setLoading(true);
    try {
      const response = await api.getSchedules(formatDate(date));
      setTasks(response.tasks);
      setError("");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "加载日程失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadTasks(selectedDate);
  }, [selectedDate]);

  function onSelect(date: { toDate: () => Date }) {
    setSelectedDate(date.toDate());
  }

  return (
    <div className="flex h-full flex-col bg-[linear-gradient(180deg,#f8fafc_0%,#f3f4f6_100%)]">
      <header className="z-10 border-b border-stone-200 bg-white/90 px-6 py-4 shadow-sm backdrop-blur">
        <h1 className="text-xl font-semibold text-stone-800">日程数据库</h1>
        <p className="text-sm text-stone-500">左侧选择日期，右侧以更紧凑的方式查看 Selena 的真实任务记录。</p>
      </header>

      <div className="flex min-h-0 flex-1 flex-row overflow-hidden">
        <div className="flex-[3] overflow-y-auto border-r border-stone-200/80 bg-white/70 p-6 lg:flex-[2]">
          <div className="overflow-hidden rounded-[28px] border border-stone-200 bg-white shadow-[0_18px_60px_-32px_rgba(28,25,23,0.32)]">
            <Calendar onSelect={onSelect} />
          </div>
        </div>

        <div className="flex-[2] overflow-y-auto p-6 lg:flex-1">
          <div className="mb-5 overflow-hidden rounded-[28px] border border-stone-200 bg-[linear-gradient(135deg,#ffffff_0%,#f5f3ff_52%,#eef6ff_100%)] shadow-[0_24px_70px_-38px_rgba(59,130,246,0.35)]">
            <div className="border-b border-white/70 px-5 py-5">
              <div className="flex items-start gap-3">
                <div className="rounded-2xl bg-stone-900 p-2.5 text-white shadow-sm">
                  <Database className="h-5 w-5" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="m-0 text-lg font-semibold text-stone-800">当天任务</h2>
                    <MetaPill className="border-white/80 bg-white/80 text-stone-600">
                      <Sparkles className="mr-1 h-3.5 w-3.5" />
                      共 {tasks.length} 条
                    </MetaPill>
                  </div>
                  <p className="mt-1 text-sm text-stone-500">
                    {formatDisplayDate(selectedDate)} · {formatDate(selectedDate)}
                  </p>
                </div>
              </div>
            </div>

            <div className="grid grid-cols-3 gap-3 px-5 py-4">
              <div className="rounded-2xl border border-white/80 bg-white/75 px-4 py-3 shadow-sm">
                <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-stone-400">待处理</div>
                <div className="mt-1 text-2xl font-semibold text-stone-800">{activeCount}</div>
              </div>
              <div className="rounded-2xl border border-white/80 bg-white/75 px-4 py-3 shadow-sm">
                <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-stone-400">已完成</div>
                <div className="mt-1 text-2xl font-semibold text-emerald-600">{completedCount}</div>
              </div>
              <div className="rounded-2xl border border-white/80 bg-white/75 px-4 py-3 shadow-sm">
                <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-stone-400">已提醒</div>
                <div className="mt-1 text-2xl font-semibold text-violet-600">{remindedCount}</div>
              </div>
            </div>
          </div>

          {error ? (
            <div className="mb-4 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 shadow-sm">
              {error}
            </div>
          ) : null}

          {loading ? (
            <div className="rounded-[24px] border border-stone-200 bg-white px-5 py-10 text-sm text-stone-500 shadow-[0_18px_60px_-36px_rgba(28,25,23,0.32)]">
              正在读取 SQLite 日程数据...
            </div>
          ) : tasks.length === 0 ? (
            <div className="flex h-64 flex-col items-center justify-center rounded-[24px] border border-stone-200 bg-white p-8 text-stone-500 shadow-[0_18px_60px_-36px_rgba(28,25,23,0.32)]">
              <Empty description="这一天没有任务记录" />
            </div>
          ) : (
            <div className="space-y-3">
              {tasks.map((task) => {
                const taskStatusClass = getTaskStatusClass(task.task_status);

                return (
                  <article
                    key={task.task_id}
                    className="group relative overflow-hidden rounded-[24px] border border-stone-200 bg-white/95 px-4 py-4 shadow-[0_16px_48px_-36px_rgba(28,25,23,0.35)] transition-all duration-200 hover:-translate-y-0.5 hover:shadow-[0_20px_56px_-32px_rgba(28,25,23,0.4)]"
                  >
                    <div className={clsx("absolute bottom-4 left-0 top-4 w-1 rounded-full", taskStatusClass.accent)} />

                    <div className="ml-2.5">
                      <div className="flex flex-wrap items-center gap-2">
                        <MetaPill className="border-stone-200 bg-stone-50 text-stone-600">
                          <Clock className="mr-1 h-3.5 w-3.5" />
                          {formatReminderClock(task.reminder_time)}
                        </MetaPill>
                        <MetaPill className={taskStatusClass.pill}>{task.task_status}</MetaPill>
                        <MetaPill className={getReminderStatusClass(task.reminder_status)}>
                          {task.reminder_status}
                        </MetaPill>
                        <span className="ml-auto text-[11px] text-stone-400">#{task.task_id}</span>
                      </div>

                      <h3
                        className="mt-3 text-[15px] font-semibold leading-6 text-stone-800"
                        style={{
                          display: "-webkit-box",
                          WebkitLineClamp: 2,
                          WebkitBoxOrient: "vertical",
                          overflow: "hidden",
                        }}
                      >
                        {task.task_content}
                      </h3>

                      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-stone-500">
                        <span className="rounded-full bg-stone-100 px-2.5 py-1">创建 {formatMetaTime(task.created_at)}</span>
                        {task.updated_at && task.updated_at !== task.created_at ? (
                          <span className="rounded-full bg-stone-100 px-2.5 py-1">
                            更新 {formatMetaTime(task.updated_at)}
                          </span>
                        ) : null}
                        {task.reminded_at ? (
                          <span className="rounded-full bg-violet-50 px-2.5 py-1 text-violet-700">
                            提醒于 {formatMetaTime(task.reminded_at)}
                          </span>
                        ) : null}
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
