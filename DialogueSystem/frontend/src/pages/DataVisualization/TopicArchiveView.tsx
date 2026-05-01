import { useMemo, useState } from "react";
import {
  Archive,
  Bot,
  Calendar,
  ChevronDown,
  ChevronRight,
  Clock,
  FileText,
  Hash,
  MessageSquare,
  Search,
  User,
} from "lucide-react";
import clsx from "clsx";

import type { CollectionRecord } from "@/types";

interface TopicRecord {
  role: string;
  content: string;
  message_id?: number;
  timestamp?: number;
}

interface ArchiveCard {
  id: number | string;
  source_file: string;
  source_session_prefix: string;
  source_topic_group: number | null;
  topic_message_count: number;
  summary_text: string;
  topic_records: TopicRecord[];
  topic_excerpt: string;
  archived_at: string;
  updated_at: string;
}

function parseArchiveCard(record: CollectionRecord): ArchiveCard {
  const topicRecords: TopicRecord[] = [];
  const rawRecords = record.topic_records;
  if (Array.isArray(rawRecords)) {
    for (const item of rawRecords) {
      if (item && typeof item === "object" && !Array.isArray(item)) {
        topicRecords.push({
          role: String((item as Record<string, unknown>).role || "assistant"),
          content: String((item as Record<string, unknown>).content || ""),
          message_id: (item as Record<string, unknown>).message_id as number | undefined,
          timestamp: (item as Record<string, unknown>).timestamp as number | undefined,
        });
      }
    }
  }

  return {
    id: record.id ?? 0,
    source_file: String(record.source_file || ""),
    source_session_prefix: String(record.source_session_prefix || ""),
    source_topic_group: record.source_topic_group as number | null,
    topic_message_count: Number(record.topic_message_count || 0),
    summary_text: String(record.summary_text || ""),
    topic_records: topicRecords,
    topic_excerpt: String(record.topic_excerpt || ""),
    archived_at: String(record.archived_at || ""),
    updated_at: String(record.updated_at || ""),
  };
}

function formatShortDate(timestamp: string) {
  if (!timestamp) return "";
  return timestamp.length >= 16 ? timestamp.slice(0, 16) : timestamp;
}

function formatDateOnly(timestamp: string) {
  if (!timestamp) return "";
  return timestamp.length >= 10 ? timestamp.slice(0, 10) : timestamp;
}

function formatTime(timestamp: string) {
  if (!timestamp) return "";
  return timestamp.length >= 16 ? timestamp.slice(11, 16) : "";
}

function truncateText(text: string, maxLength: number) {
  if (text.length <= maxLength) return text;
  return text.slice(0, maxLength - 1) + "\u2026";
}

function ChatBubble({ record }: { record: TopicRecord }) {
  const isUser = record.role === "user";

  return (
    <div className={clsx("flex gap-2", isUser ? "flex-row-reverse" : "flex-row")}>
      <div
        className={clsx(
          "flex h-6 w-6 shrink-0 items-center justify-center rounded-full",
          isUser ? "bg-blue-500" : "bg-indigo-500",
        )}
      >
        {isUser ? (
          <User className="h-3 w-3 text-white" />
        ) : (
          <Bot className="h-3 w-3 text-white" />
        )}
      </div>
      <div
        className={clsx(
          "max-w-[85%] rounded-xl px-3 py-2 text-sm leading-relaxed",
          isUser
            ? "rounded-br-sm bg-blue-50 text-blue-900"
            : "rounded-bl-sm bg-slate-100 text-slate-800",
        )}
      >
        {truncateText(record.content, 300)}
      </div>
    </div>
  );
}

function ArchiveCardComponent({ archive }: { archive: ArchiveCard }) {
  const [expanded, setExpanded] = useState(false);
  const hasRecords = archive.topic_records.length > 0;
  const previewRecords = archive.topic_records.slice(0, 4);
  const remainingCount = Math.max(0, archive.topic_records.length - 4);

  return (
    <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm transition-shadow hover:shadow-md">
      <div className="flex items-start justify-between gap-4 border-b border-slate-100 bg-gradient-to-r from-slate-50 to-white px-5 py-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-1 rounded-full bg-indigo-100 px-2.5 py-0.5 text-xs font-semibold text-indigo-700">
              <Hash className="h-3 w-3" />
              {archive.id}
            </span>
            {archive.source_topic_group !== null && (
              <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-medium text-amber-700">
                <MessageSquare className="h-3 w-3" />
                Topic {archive.source_topic_group}
              </span>
            )}
            <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2.5 py-0.5 text-xs font-medium text-emerald-700">
              {archive.topic_message_count} 条消息
            </span>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-slate-400">
            <span className="inline-flex items-center gap-1">
              <Calendar className="h-3 w-3" />
              {formatDateOnly(archive.archived_at)}
            </span>
            <span className="inline-flex items-center gap-1">
              <Clock className="h-3 w-3" />
              {formatTime(archive.archived_at)}
            </span>
            {archive.source_file && (
              <span className="inline-flex items-center gap-1">
                <FileText className="h-3 w-3" />
                {archive.source_file}
              </span>
            )}
          </div>
        </div>
      </div>

      {archive.summary_text && (
        <div className="border-b border-slate-100 px-5 py-4">
          <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">
            摘要
          </div>
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-700">
            {archive.summary_text}
          </p>
        </div>
      )}

      {hasRecords && (
        <div className="px-5 py-4">
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="mb-3 flex w-full items-center gap-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-400 transition hover:text-slate-600"
          >
            {expanded ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )}
            对话记录
            <span className="font-normal">({archive.topic_records.length})</span>
          </button>

          {!expanded && (
            <div className="space-y-2">
              {previewRecords.map((record, index) => (
                <ChatBubble key={`preview-${index}`} record={record} />
              ))}
              {remainingCount > 0 && (
                <button
                  type="button"
                  onClick={() => setExpanded(true)}
                  className="w-full rounded-lg py-1.5 text-center text-xs text-slate-400 transition hover:bg-slate-50 hover:text-slate-600"
                >
                  还有 {remainingCount} 条，点击展开全部
                </button>
              )}
            </div>
          )}

          {expanded && (
            <div className="space-y-2">
              {archive.topic_records.map((record, index) => (
                <ChatBubble key={`full-${index}`} record={record} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function TopicArchiveView({
  records,
  total,
}: {
  records: CollectionRecord[];
  total: number;
}) {
  const [searchText, setSearchText] = useState("");

  const archives = useMemo(
    () => records.map(parseArchiveCard),
    [records],
  );

  const filtered = useMemo(() => {
    const keyword = searchText.trim().toLowerCase();
    if (!keyword) return archives;
    return archives.filter(
      (a) =>
        a.summary_text.toLowerCase().includes(keyword) ||
        a.topic_excerpt.toLowerCase().includes(keyword) ||
        a.source_file.toLowerCase().includes(keyword) ||
        a.topic_records.some((r) => r.content.toLowerCase().includes(keyword)),
    );
  }, [archives, searchText]);

  const dateGroups = useMemo(() => {
    const groups: { date: string; items: ArchiveCard[] }[] = [];
    const map = new Map<string, ArchiveCard[]>();

    for (const archive of filtered) {
      const date = formatDateOnly(archive.archived_at) || "未知日期";
      if (!map.has(date)) {
        map.set(date, []);
        groups.push({ date, items: map.get(date)! });
      }
      map.get(date)!.push(archive);
    }

    return groups;
  }, [filtered]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-4">
        <div>
          <div className="flex items-center gap-2">
            <Archive className="h-5 w-5 text-indigo-600" />
            <h1 className="text-xl font-bold text-slate-800">话题归档 / 情节记忆</h1>
          </div>
          <p className="mt-1 text-sm text-slate-500">
            共 {total} 段归档对话{filtered.length !== archives.length && `，筛选后 ${filtered.length} 段`}
          </p>
        </div>
        <div className="relative w-72">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            placeholder="搜索摘要或对话内容..."
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-3 text-sm outline-none transition focus:border-indigo-300 focus:ring-2 focus:ring-indigo-100"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-6">
        {filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-slate-400">
            <Archive className="mb-4 h-16 w-16 opacity-40" />
            <p className="text-lg">
              {searchText ? "没有找到匹配的归档" : "暂无话题归档记录"}
            </p>
          </div>
        ) : (
          <div className="space-y-8">
            {dateGroups.map((group) => (
              <section key={group.date}>
                <div className="sticky top-0 z-10 mb-4 flex items-center gap-3">
                  <div className="h-px flex-1 bg-slate-200" />
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-white px-3 py-1 text-xs font-medium text-slate-500 shadow-sm ring-1 ring-slate-200">
                    <Calendar className="h-3 w-3" />
                    {group.date}
                  </span>
                  <div className="h-px flex-1 bg-slate-200" />
                </div>
                <div className="grid gap-5 xl:grid-cols-2">
                  {group.items.map((archive) => (
                    <ArchiveCardComponent key={archive.id} archive={archive} />
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
