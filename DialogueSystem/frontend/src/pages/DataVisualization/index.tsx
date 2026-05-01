import { useEffect, useMemo, useRef, useState } from "react";
import type { Key } from "react";
import { Button, Empty, Input, Modal, Table, Tag, message } from "antd";
import type { TableColumnsType } from "antd";
import { Database, LayoutList, Pencil, Plus, RefreshCcw, Search, Trash2, X } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import clsx from "clsx";

import { api } from "@/services/api";
import TopicArchiveView from "./TopicArchiveView";
import type {
  CollectionDetail,
  CollectionPointId,
  CollectionRecord,
  CollectionSummary,
} from "@/types";

type EditorMode = "create" | "edit";

interface FieldSchema {
  key: string;
  sample: unknown;
}

interface TableRow extends CollectionRecord {
  __key: string;
  __isDraft?: boolean;
}

interface EditorState {
  mode: EditorMode;
  rowKey: string;
  originalId?: CollectionPointId;
  values: Record<string, string>;
}

interface ContextMenuState {
  x: number;
  y: number;
  rowKey: string;
}

const DRAFT_ROW_KEY = "__draft_row__";
const INTENTION_COLLECTION_KEY = "intention";
const TOPIC_ARCHIVE_COLLECTION_KEY = "topic_archive";

function isVectorField(key: string, value: unknown) {
  return (
    (Array.isArray(value) && typeof value[0] === "number") ||
    key.toLowerCase().includes("vector") ||
    key.toLowerCase().includes("embedding")
  );
}

function toDisplayText(value: unknown) {
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "object") {
    return JSON.stringify(value, null, 2);
  }
  return String(value);
}

function getRecordKey(record: CollectionRecord, index: number) {
  if (record.id === undefined || record.id === null || String(record.id).trim() === "") {
    return `row-${index}`;
  }
  return String(record.id);
}

function formatEditorValue(value: unknown) {
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function buildFieldSchemas(records: CollectionRecord[]) {
  const orderedKeys: string[] = [];
  const samples = new Map<string, unknown>();

  for (const record of records) {
    for (const [key, value] of Object.entries(record)) {
      if (key === "id" || isVectorField(key, value)) {
        continue;
      }
      if (!samples.has(key)) {
        orderedKeys.push(key);
        samples.set(key, value);
      } else if ((samples.get(key) === null || samples.get(key) === undefined) && value !== undefined) {
        samples.set(key, value);
      }
    }
  }

  if (orderedKeys.length === 0) {
    return [{ key: "text", sample: "" }];
  }

  return orderedKeys.map((key) => ({
    key,
    sample: samples.get(key),
  }));
}

function getEditorPlaceholder(sample: unknown) {
  if (Array.isArray(sample)) {
    return "请输入 JSON 数组，例如 []";
  }
  if (sample !== null && typeof sample === "object") {
    return "请输入 JSON 对象，例如 {}";
  }
  if (typeof sample === "number") {
    return "请输入数字";
  }
  if (typeof sample === "boolean") {
    return "请输入 true 或 false";
  }
  return "输入内容";
}

function parseEditorValue(rawValue: string, sample: unknown) {
  if (rawValue.trim() === "") {
    return undefined;
  }

  if (Array.isArray(sample) || (sample !== null && typeof sample === "object")) {
    try {
      return JSON.parse(rawValue);
    } catch {
      throw new Error("对象或数组字段必须输入合法的 JSON。");
    }
  }

  if (typeof sample === "number") {
    const numericValue = Number(rawValue);
    if (Number.isNaN(numericValue)) {
      throw new Error("数字字段必须输入合法数字。");
    }
    return numericValue;
  }

  if (typeof sample === "boolean") {
    const normalizedValue = rawValue.trim().toLowerCase();
    if (["true", "1", "yes"].includes(normalizedValue)) {
      return true;
    }
    if (["false", "0", "no"].includes(normalizedValue)) {
      return false;
    }
    throw new Error("布尔字段必须输入 true 或 false。");
  }

  return rawValue;
}

function stripTableMeta(record: CollectionRecord | TableRow) {
  const payload = { ...record };
  delete payload.id;
  delete payload.__key;
  delete payload.__isDraft;
  return payload;
}

export default function DataVisualization() {
  const { collectionName } = useParams();
  const navigate = useNavigate();
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  const pendingScrollRestoreRef = useRef<{ top: number; left: number } | null>(null);

  const [collections, setCollections] = useState<CollectionSummary[]>([]);
  const [collectionDetail, setCollectionDetail] = useState<CollectionDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [mutating, setMutating] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [error, setError] = useState("");
  const [selectedRowKeys, setSelectedRowKeys] = useState<Key[]>([]);
  const [selectedIds, setSelectedIds] = useState<CollectionPointId[]>([]);
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const [editorState, setEditorState] = useState<EditorState | null>(null);

  const canMutate = Boolean(collectionName && collectionName !== "default" && collectionDetail?.exists && !collectionDetail?.readonly);

  useEffect(() => {
    let mounted = true;
    api
      .getCollections()
      .then((response) => {
        if (!mounted) {
          return;
        }
        const intentionCollection = response.collections.find((collection) => collection.key === INTENTION_COLLECTION_KEY);
        if (collectionName && intentionCollection && collectionName === intentionCollection.name) {
          navigate("/IntentionSelection", { replace: true });
          return;
        }

        const genericCollections = response.collections.filter(
          (collection) => collection.key !== INTENTION_COLLECTION_KEY
        );
        setCollections(genericCollections);
        if ((!collectionName || collectionName === "default") && genericCollections.length > 0) {
          navigate(`/data/${genericCollections[0].name}`, { replace: true });
        }
      })
      .catch((requestError) => {
        if (mounted) {
          setError(requestError instanceof Error ? requestError.message : "加载集合失败。");
        }
      });

    return () => {
      mounted = false;
    };
  }, [collectionName, navigate]);

  useEffect(() => {
    setSelectedRowKeys([]);
    setSelectedIds([]);
    setContextMenu(null);
    setEditorState(null);

    if (!collectionName || collectionName === "default") {
      setCollectionDetail(null);
      return;
    }

    let mounted = true;
    setLoading(true);
    api
      .getCollection(collectionName)
      .then((response) => {
        if (!mounted) {
          return;
        }
        setCollectionDetail(response);
        setError("");
      })
      .catch((requestError) => {
        if (mounted) {
          setError(requestError instanceof Error ? requestError.message : "加载集合数据失败。");
        }
      })
      .finally(() => {
        if (mounted) {
          setLoading(false);
        }
      });

    return () => {
      mounted = false;
    };
  }, [collectionName]);

  useEffect(() => {
    if (!contextMenu) {
      return;
    }

    function closeContextMenu() {
      setContextMenu(null);
    }

    window.addEventListener("click", closeContextMenu);
    window.addEventListener("scroll", closeContextMenu, true);
    return () => {
      window.removeEventListener("click", closeContextMenu);
      window.removeEventListener("scroll", closeContextMenu, true);
    };
  }, [contextMenu]);

  const allRows = useMemo<TableRow[]>(
    () =>
      (collectionDetail?.records ?? []).map((record, index) => ({
        ...record,
        __key: getRecordKey(record, index),
      })),
    [collectionDetail?.records]
  );

  const rowByKey = useMemo(() => new Map(allRows.map((row) => [row.__key, row])), [allRows]);
  const fieldSchemas = useMemo(
    () =>
      collectionDetail?.field_schema && collectionDetail.field_schema.length > 0
        ? collectionDetail.field_schema.map((field) => ({ key: field.key, sample: field.sample }))
        : buildFieldSchemas(collectionDetail?.records ?? []),
    [collectionDetail?.field_schema, collectionDetail?.records]
  );
  const activeCollectionMeta = useMemo(
    () => collections.find((collection) => collection.name === collectionName) ?? null,
    [collectionName, collections]
  );
  const displayCollectionName =
    collectionDetail?.label ||
    activeCollectionMeta?.label ||
    (collectionName && collectionName !== "default" ? collectionName : "选择一个集合");
  const displayCollectionDescription =
    collectionDetail?.description || activeCollectionMeta?.description || "";

  const filteredRows = useMemo(() => {
    const keyword = searchText.trim().toLowerCase();
    if (!keyword) {
      return allRows;
    }
    return allRows.filter((record) =>
      Object.entries(record).some(([key, value]) => {
        if (key.startsWith("__") || isVectorField(key, value)) {
          return false;
        }
        return toDisplayText(value).toLowerCase().includes(keyword);
      })
    );
  }, [allRows, searchText]);

  const tableData = useMemo<TableRow[]>(() => {
    if (!editorState) {
      return filteredRows;
    }

    if (editorState.mode === "create") {
      const draftRow: TableRow = {
        __key: DRAFT_ROW_KEY,
        __isDraft: true,
        id: editorState.values.id || undefined,
      };
      for (const field of fieldSchemas) {
        draftRow[field.key] = editorState.values[field.key] ?? "";
      }
      return [draftRow, ...filteredRows];
    }

    if (filteredRows.some((row) => row.__key === editorState.rowKey)) {
      return filteredRows;
    }

    const editingRow = rowByKey.get(editorState.rowKey);
    return editingRow ? [editingRow, ...filteredRows] : filteredRows;
  }, [editorState, fieldSchemas, filteredRows, rowByKey]);

  function ensureNoActiveEditor() {
    if (!editorState) {
      return true;
    }
    message.warning("请先保存或取消当前正在编辑的行。");
    return false;
  }

  function rememberScrollPosition() {
    const container = scrollContainerRef.current;
    if (!container) {
      pendingScrollRestoreRef.current = null;
      return;
    }
    pendingScrollRestoreRef.current = {
      top: container.scrollTop,
      left: container.scrollLeft,
    };
  }

  async function reloadCurrentCollection(options?: { preserveScroll?: boolean }) {
    if (!collectionName || collectionName === "default") {
      return;
    }

    if (options?.preserveScroll) {
      rememberScrollPosition();
    }
    setLoading(true);
    try {
      const response = await api.getCollection(collectionName);
      setCollectionDetail(response);
      setError("");
      setSelectedRowKeys([]);
      setSelectedIds([]);
      setContextMenu(null);
      setEditorState(null);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "刷新集合失败。");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    const pendingScroll = pendingScrollRestoreRef.current;
    const container = scrollContainerRef.current;
    if (!pendingScroll || !container || loading) {
      return;
    }

    pendingScrollRestoreRef.current = null;
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        const currentContainer = scrollContainerRef.current;
        if (!currentContainer) {
          return;
        }
        currentContainer.scrollTop = pendingScroll.top;
        currentContainer.scrollLeft = pendingScroll.left;
      });
    });
  }, [collectionDetail, loading]);

  function startCreateRow() {
    if (!canMutate || !ensureNoActiveEditor()) {
      return;
    }

    const initialValues: Record<string, string> = { id: "" };
    for (const field of fieldSchemas) {
      initialValues[field.key] = "";
    }

    setEditorState({
      mode: "create",
      rowKey: DRAFT_ROW_KEY,
      values: initialValues,
    });
  }

  function startEditRow(row: TableRow) {
    if (!ensureNoActiveEditor()) {
      return;
    }

    const initialValues: Record<string, string> = {
      id: row.id === undefined || row.id === null ? "" : String(row.id),
    };
    for (const field of fieldSchemas) {
      initialValues[field.key] = formatEditorValue(row[field.key]);
    }

    setContextMenu(null);
    setEditorState({
      mode: "edit",
      rowKey: row.__key,
      originalId: row.id,
      values: initialValues,
    });
  }

  function cancelEditing() {
    if (mutating) {
      return;
    }
    setEditorState(null);
  }

  function updateEditorValue(fieldKey: string, value: string) {
    setEditorState((current) => {
      if (!current) {
        return current;
      }
      return {
        ...current,
        values: {
          ...current.values,
          [fieldKey]: value,
        },
      };
    });
  }

  function buildPayloadFromEditor() {
    if (!editorState) {
      return {};
    }

    const basePayload =
      editorState.mode === "edit"
        ? stripTableMeta(rowByKey.get(editorState.rowKey) ?? {})
        : {};

    for (const field of fieldSchemas) {
      const rawValue = editorState.values[field.key] ?? "";
      const rowValue = editorState.mode === "edit" ? rowByKey.get(editorState.rowKey)?.[field.key] : undefined;
      const sampleValue = rowValue !== undefined ? rowValue : field.sample;
      const parsedValue = parseEditorValue(rawValue, sampleValue);

      if (parsedValue === undefined) {
        delete basePayload[field.key];
      } else {
        basePayload[field.key] = parsedValue;
      }
    }

    return basePayload;
  }

  async function saveEditingRow() {
    if (!collectionName || collectionName === "default" || !editorState) {
      return;
    }

    try {
      setMutating(true);
      const payload = buildPayloadFromEditor();

      if (editorState.mode === "create") {
        await api.createCollectionRecord(collectionName, {
          id: editorState.values.id.trim() || undefined,
          payload,
          auto_vectorize: true,
        });
        message.success("新记录已插入表格。");
      } else {
        if (editorState.originalId === undefined || editorState.originalId === null) {
          throw new Error("缺少原始记录 ID，无法保存。");
        }
        await api.updateCollectionRecord(collectionName, editorState.originalId, {
          payload,
          auto_vectorize: false,
        });
        message.success("记录已保存。");
      }

      await reloadCurrentCollection({ preserveScroll: true });
    } catch (requestError) {
      message.error(requestError instanceof Error ? requestError.message : "保存失败。");
    } finally {
      setMutating(false);
    }
  }

  async function deleteRecordById(recordId: CollectionPointId) {
    if (!collectionName || collectionName === "default") {
      return;
    }

    try {
      setMutating(true);
      await api.deleteCollectionRecord(collectionName, recordId);
      message.success("记录已删除。");
      await reloadCurrentCollection({ preserveScroll: true });
    } catch (requestError) {
      message.error(requestError instanceof Error ? requestError.message : "删除失败。");
    } finally {
      setMutating(false);
    }
  }

  function confirmDeleteRow(row: TableRow) {
    if (row.id === undefined || row.id === null) {
      return;
    }

    setContextMenu(null);
    Modal.confirm({
      title: "删除这条记录？",
      content: `将删除 ID 为 ${String(row.id)} 的记录，此操作不可撤销。`,
      okText: "删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        await deleteRecordById(row.id as CollectionPointId);
      },
    });
  }

  function confirmBatchDelete() {
    if (!collectionName || collectionName === "default" || selectedIds.length === 0) {
      return;
    }

    Modal.confirm({
      title: `删除选中的 ${selectedIds.length} 条记录？`,
      content: "批量删除后无法恢复，请确认。",
      okText: "批量删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        try {
          setMutating(true);
          await api.deleteCollectionRecords(collectionName, selectedIds);
          message.success(`已删除 ${selectedIds.length} 条记录。`);
          await reloadCurrentCollection({ preserveScroll: true });
        } catch (requestError) {
          message.error(requestError instanceof Error ? requestError.message : "批量删除失败。");
        } finally {
          setMutating(false);
        }
      },
    });
  }

  function renderEditorCell(field: FieldSchema) {
    const value = editorState?.values[field.key] ?? "";
    const shouldUseTextArea =
      Array.isArray(field.sample) ||
      (field.sample !== null && typeof field.sample === "object") ||
      value.length > 80;

    if (shouldUseTextArea) {
      return (
        <Input.TextArea
          autoSize={{ minRows: 1, maxRows: 4 }}
          value={value}
          onChange={(event) => updateEditorValue(field.key, event.target.value)}
          placeholder={getEditorPlaceholder(field.sample)}
        />
      );
    }

    return (
      <Input
        value={value}
        onChange={(event) => updateEditorValue(field.key, event.target.value)}
        placeholder={getEditorPlaceholder(field.sample)}
      />
    );
  }

  const columns = useMemo<TableColumnsType<TableRow>>(() => {
    const dynamicColumns: TableColumnsType<TableRow> = [
      {
        title: "id",
        dataIndex: "id",
        key: "id",
        width: 160,
        fixed: "left",
        render: (value: unknown, row: TableRow) => {
          const isEditing = editorState?.rowKey === row.__key;
          if (!isEditing) {
            return <span>{toDisplayText(value)}</span>;
          }
          return (
            <Input
              value={editorState?.values.id ?? ""}
              disabled={editorState?.mode === "edit"}
              onChange={(event) => updateEditorValue("id", event.target.value)}
              placeholder={editorState?.mode === "create" ? "留空则自动分配 ID" : ""}
            />
          );
        },
      },
    ];

    for (const field of fieldSchemas) {
      dynamicColumns.push({
        title: field.key,
        dataIndex: field.key,
        key: field.key,
        ellipsis: true,
        render: (value: unknown, row: TableRow) => {
          if (isVectorField(field.key, value)) {
            return <Tag color="blue">[Vector Hidden]</Tag>;
          }
          if (editorState?.rowKey === row.__key) {
            return renderEditorCell(field);
          }
          return <span className="whitespace-pre-wrap">{toDisplayText(value)}</span>;
        },
      });
    }

    dynamicColumns.push({
      title: "操作",
      key: "actions",
      fixed: "right",
      width: 180,
      render: (_, row: TableRow) => {
        const isEditing = editorState?.rowKey === row.__key;
        if (isEditing) {
          return (
            <div className="flex items-center gap-2">
              <Button type="link" size="small" onClick={() => void saveEditingRow()}>
                保存
              </Button>
              <Button type="link" size="small" onClick={cancelEditing}>
                取消
              </Button>
            </div>
          );
        }

        return (
          <div className="flex items-center gap-2">
            <Button
              type="link"
              size="small"
              onClick={(event) => {
                event.stopPropagation();
                startEditRow(row);
              }}
              disabled={!canMutate || Boolean(editorState)}
            >
              编辑
            </Button>
            <Button
              type="link"
              danger
              size="small"
              onClick={(event) => {
                event.stopPropagation();
                confirmDeleteRow(row);
              }}
              disabled={!canMutate || Boolean(editorState) || row.id === undefined || row.id === null}
            >
              删除
            </Button>
          </div>
        );
      },
    });

    return dynamicColumns;
  }, [canMutate, editorState, fieldSchemas]);

  const contextMenuRow = contextMenu ? rowByKey.get(contextMenu.rowKey) ?? null : null;
  const contextMenuLeft = contextMenu ? Math.min(contextMenu.x, window.innerWidth - 180) : 0;
  const contextMenuTop = contextMenu ? Math.min(contextMenu.y, window.innerHeight - 110) : 0;

  return (
    <div className="flex h-full bg-slate-50">
      <div className="flex w-64 flex-col border-r border-slate-200 bg-white">
        <div className="flex items-center space-x-2 border-b border-slate-200 p-4">
          <Database className="h-5 w-5 text-blue-600" />
          <h2 className="font-bold text-slate-800">Qdrant 集合</h2>
        </div>
        <div className="flex-1 space-y-1 overflow-y-auto p-2">
          {collections.map((collection) => (
            <button
              key={collection.name}
              onClick={() => navigate(`/data/${collection.name}`)}
              className={clsx(
                "w-full rounded-lg px-3 py-2 text-left transition-colors",
                collectionName === collection.name
                  ? "bg-blue-50 font-medium text-blue-700"
                  : "text-slate-600 hover:bg-slate-100"
              )}
            >
              <div className="flex items-center space-x-3">
                <LayoutList className="h-4 w-4" />
                <div className="min-w-0">
                  <div className="truncate">{collection.label || collection.name}</div>
                  <div className="text-xs text-slate-400">
                    {collection.storage_kind === "sqlite"
                      ? collection.description || "本地 SQLite 数据视图"
                      : `${collection.exists ? "已连接" : "未创建"} · 向量维度 ${collection.vector_size || "-"}`}
                  </div>
                </div>
              </div>
            </button>
          ))}
        </div>
      </div>

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {collectionName === TOPIC_ARCHIVE_COLLECTION_KEY && collectionDetail ? (
          <TopicArchiveView
            records={collectionDetail.records ?? []}
            total={collectionDetail.total ?? 0}
          />
        ) : (
        <>
        <header className="z-10 border-b border-slate-200 bg-white px-6 py-4">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <h1 className="text-xl font-bold text-slate-800">
                {displayCollectionName}
              </h1>
              <p className="mt-1 text-sm text-slate-500">
                {collectionDetail?.storage_kind === "sqlite"
                  ? `总记录数 ${collectionDetail?.total ?? 0}${
                      displayCollectionDescription ? ` · ${displayCollectionDescription}` : ""
                    } · 新增和编辑都支持在表格里直接操作。`
                  : `总记录数 ${collectionDetail?.total ?? 0} · ${
                      collectionDetail?.vector_kind === "named"
                        ? `命名向量 ${collectionDetail.vector_names.join(", ") || "-"}`
                        : `单向量维度 ${collectionDetail?.vector_size || "-"}`
                    } · 新增和编辑都支持在表格里直接操作。`}
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <Button
                icon={<RefreshCcw className="h-4 w-4" />}
                onClick={() => {
                  void reloadCurrentCollection({ preserveScroll: true });
                }}
                disabled={!collectionName || collectionName === "default" || mutating}
              >
                刷新
              </Button>
              <Button
                type="primary"
                icon={<Plus className="h-4 w-4" />}
                onClick={startCreateRow}
                disabled={!canMutate || Boolean(editorState) || mutating}
              >
                插入新行
              </Button>
              <Button
                danger
                icon={<Trash2 className="h-4 w-4" />}
                onClick={confirmBatchDelete}
                disabled={!canMutate || Boolean(editorState) || selectedIds.length === 0 || mutating}
              >
                批量删除{selectedIds.length > 0 ? ` (${selectedIds.length})` : ""}
              </Button>
              <div className="w-72">
                <Input
                  placeholder="搜索当前表格..."
                  prefix={<Search className="h-4 w-4 text-slate-400" />}
                  value={searchText}
                  onChange={(event) => setSearchText(event.target.value)}
                  allowClear
                />
              </div>
            </div>
          </div>
        </header>

        <div ref={scrollContainerRef} className="flex-1 overflow-auto p-6">
          <div className="flex h-full flex-col rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            {error ? (
              <div className="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                {error}
              </div>
            ) : null}

            {!collectionName || collectionName === "default" ? (
              <div className="flex flex-1 items-center justify-center">
                <Empty description="从左侧选择一个集合后查看真实数据。" />
              </div>
            ) : (
              <Table<TableRow>
                rowKey="__key"
                columns={columns}
                dataSource={tableData}
                loading={loading}
                rowSelection={{
                  selectedRowKeys,
                  onChange: (keys, rows) => {
                    setSelectedRowKeys(keys);
                    setSelectedIds(
                      rows
                        .map((row) => row.id)
                        .filter((id): id is CollectionPointId => id !== undefined && id !== null)
                    );
                  },
                  getCheckboxProps: (row) => ({
                    disabled:
                      Boolean(editorState) ||
                      row.id === undefined ||
                      row.id === null ||
                      row.__isDraft === true,
                  }),
                }}
                onRow={(row) => ({
                  onContextMenu: (event) => {
                    if (editorState || row.__isDraft) {
                      return;
                    }
                    event.preventDefault();
                    setContextMenu({
                      x: event.clientX,
                      y: event.clientY,
                      rowKey: row.__key,
                    });
                  },
                })}
                scroll={{ y: "calc(100vh - 320px)", x: "max-content" }}
                pagination={{ pageSize: 20 }}
                size="middle"
                locale={{
                  emptyText: collectionDetail?.exists === false ? "该集合尚未创建。" : "没有可展示的数据。",
                }}
              />
            )}
          </div>
        </div>
        </>
        )}
      </div>

      {editorState ? (
        <div className="fixed bottom-6 right-6 z-30 flex items-center gap-3 rounded-full border border-slate-200 bg-white px-4 py-3 shadow-xl">
          <span className="text-sm text-slate-600">
            {editorState.mode === "create" ? "正在插入新行" : `正在编辑记录 ${editorState.values.id || ""}`}
          </span>
          <Button type="primary" size="small" onClick={() => void saveEditingRow()} loading={mutating}>
            保存
          </Button>
          <Button size="small" onClick={cancelEditing} disabled={mutating}>
            <X className="h-4 w-4" />
          </Button>
        </div>
      ) : null}

      {contextMenu && contextMenuRow ? (
        <div
          className="fixed inset-0 z-50"
          onClick={() => setContextMenu(null)}
          onContextMenu={(event) => {
            event.preventDefault();
            setContextMenu(null);
          }}
        >
          <div
            className="absolute min-w-40 rounded-xl border border-slate-200 bg-white p-2 shadow-xl"
            style={{ left: contextMenuLeft, top: contextMenuTop }}
            onClick={(event) => event.stopPropagation()}
            onContextMenu={(event) => event.preventDefault()}
          >
            <button
              className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-slate-700 transition hover:bg-slate-100"
              onClick={() => startEditRow(contextMenuRow)}
            >
              <Pencil className="h-4 w-4" />
              编辑该行
            </button>
            <button
              className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-red-600 transition hover:bg-red-50"
              onClick={() => confirmDeleteRow(contextMenuRow)}
              disabled={contextMenuRow.id === undefined || contextMenuRow.id === null}
            >
              <Trash2 className="h-4 w-4" />
              删除该行
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
