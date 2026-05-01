import { useEffect, useMemo, useRef, useState } from "react";
import { Button, Checkbox, Empty, Input, Modal, Tag, message } from "antd";
import { Database, Pencil, Plus, RefreshCcw, Search, Trash2, X } from "lucide-react";

import { api } from "@/services/api";
import type { CollectionDetail, CollectionPointId, CollectionRecord, CollectionSummary } from "@/types";

const INTENTION_COLLECTION_KEY = "intention";
const INTENTION_COLLECTION_FALLBACK_NAME = "IntentionSelection_512";

type EditorState =
  | { kind: "new-group"; functionName: string; text: string }
  | { kind: "new-text"; functionName: string; text: string }
  | { kind: "rename-group"; oldFunctionName: string; newFunctionName: string }
  | { kind: "edit-record"; recordId: CollectionPointId; functionName: string; text: string }
  | null;

interface IntentionGroup {
  functionName: string;
  records: CollectionRecord[];
}

interface BatchCreateState {
  functionName: string;
  texts: string;
}

interface BatchMoveState {
  functionName: string;
}

function getFunctionName(record: CollectionRecord) {
  return String(record.FunctionName ?? "").trim() || "未命名 FunctionName";
}

function getText(record: CollectionRecord) {
  return String(record.text ?? "").trim();
}

function stripRecordMeta(record: CollectionRecord) {
  const payload = { ...record };
  delete payload.id;
  return payload;
}

function isSameId(left: CollectionPointId | undefined, right: CollectionPointId | undefined) {
  return String(left ?? "") === String(right ?? "");
}

function getRecordId(record: CollectionRecord) {
  return record.id !== undefined && record.id !== null ? record.id : null;
}

function toIdKey(id: CollectionPointId | null | undefined) {
  return String(id ?? "");
}

function getSelectableIds(records: CollectionRecord[]) {
  return records
    .map((record) => getRecordId(record))
    .filter((id): id is CollectionPointId => id !== null);
}

function uniqIds(ids: CollectionPointId[]) {
  const uniqueIds = new Map<string, CollectionPointId>();
  for (const id of ids) {
    uniqueIds.set(toIdKey(id), id);
  }
  return Array.from(uniqueIds.values());
}

function normalizeBatchTexts(rawText: string) {
  const seen = new Set<string>();
  const lines: string[] = [];
  let ignoredCount = 0;

  for (const rawLine of rawText.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) {
      ignoredCount += 1;
      continue;
    }
    if (seen.has(line)) {
      ignoredCount += 1;
      continue;
    }
    seen.add(line);
    lines.push(line);
  }

  return { lines, ignoredCount };
}

function getErrorMessage(requestError: unknown, fallback: string) {
  return requestError instanceof Error ? requestError.message : fallback;
}

export default function IntentionSelection() {
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  const pendingScrollRestoreRef = useRef<{ top: number; left: number } | null>(null);

  const [collectionSummary, setCollectionSummary] = useState<CollectionSummary | null>(null);
  const [collectionDetail, setCollectionDetail] = useState<CollectionDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [mutating, setMutating] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [error, setError] = useState("");
  const [editorState, setEditorState] = useState<EditorState>(null);
  const [selectedIds, setSelectedIds] = useState<CollectionPointId[]>([]);
  const [batchCreateState, setBatchCreateState] = useState<BatchCreateState | null>(null);
  const [batchMoveState, setBatchMoveState] = useState<BatchMoveState | null>(null);

  const targetCollectionName = collectionSummary?.name ?? INTENTION_COLLECTION_FALLBACK_NAME;
  const hasPendingDraft = Boolean(editorState || batchCreateState || batchMoveState);
  const actionDisabled = hasPendingDraft || mutating;

  useEffect(() => {
    let mounted = true;
    api
      .getCollections()
      .then((response) => {
        if (!mounted) {
          return;
        }
        const intentionCollection =
          response.collections.find((collection) => collection.key === INTENTION_COLLECTION_KEY) ??
          response.collections.find((collection) => collection.name === INTENTION_COLLECTION_FALLBACK_NAME) ??
          null;

        setCollectionSummary(intentionCollection);
        if (!intentionCollection) {
          setError("未找到 intention 对应的 Qdrant collection 配置。");
        }
      })
      .catch((requestError) => {
        if (mounted) {
          setError(getErrorMessage(requestError, "加载 IntentionSelection 集合失败。"));
        }
      });

    return () => {
      mounted = false;
    };
  }, []);

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

  async function reloadCollection(options?: { preserveScroll?: boolean }) {
    if (options?.preserveScroll) {
      rememberScrollPosition();
    }
    setLoading(true);
    try {
      const response = await api.getCollection(targetCollectionName, 500);
      setCollectionDetail(response);
      setError("");
      setEditorState(null);
      setBatchCreateState(null);
      setBatchMoveState(null);
      setSelectedIds([]);
    } catch (requestError) {
      setError(getErrorMessage(requestError, "加载 intention 数据失败。"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!collectionSummary) {
      return;
    }
    void reloadCollection();
  }, [collectionSummary]);

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

  const allRecords = collectionDetail?.records ?? [];

  const filteredGroups = useMemo<IntentionGroup[]>(() => {
    const keyword = searchText.trim().toLowerCase();
    const groups = new Map<string, CollectionRecord[]>();

    for (const record of allRecords) {
      const functionName = getFunctionName(record);
      const text = getText(record);
      const matches =
        !keyword ||
        functionName.toLowerCase().includes(keyword) ||
        text.toLowerCase().includes(keyword);
      if (!matches) {
        continue;
      }

      if (!groups.has(functionName)) {
        groups.set(functionName, []);
      }
      groups.get(functionName)?.push(record);
    }

    return Array.from(groups.entries())
      .map(([functionName, records]) => ({
        functionName,
        records: [...records].sort((left, right) => getText(left).localeCompare(getText(right), "zh-Hans-CN")),
      }))
      .sort((left, right) => left.functionName.localeCompare(right.functionName, "zh-Hans-CN"));
  }, [allRecords, searchText]);

  const selectedIdSet = useMemo(() => new Set(selectedIds.map((id) => toIdKey(id))), [selectedIds]);

  const selectedRecords = useMemo(
    () =>
      allRecords.filter((record) => {
        const recordId = getRecordId(record);
        return recordId !== null && selectedIdSet.has(toIdKey(recordId));
      }),
    [allRecords, selectedIdSet]
  );

  const visibleSelectableIds = useMemo(
    () => uniqIds(filteredGroups.flatMap((group) => getSelectableIds(group.records))),
    [filteredGroups]
  );

  const selectedFunctionGroupCount = useMemo(
    () => new Set(selectedRecords.map((record) => getFunctionName(record))).size,
    [selectedRecords]
  );

  const batchCreatePreview = useMemo(
    () => normalizeBatchTexts(batchCreateState?.texts ?? ""),
    [batchCreateState?.texts]
  );

  function ensureNoPendingOperation() {
    if (mutating) {
      message.warning("请等待当前操作完成。");
      return false;
    }
    if (editorState || batchCreateState || batchMoveState) {
      message.warning("请先保存或取消当前编辑 / 批量操作。");
      return false;
    }
    return true;
  }

  function updateSelectedIds(updater: (current: CollectionPointId[]) => CollectionPointId[]) {
    setSelectedIds((current) => uniqIds(updater(current)));
  }

  function clearSelection() {
    setSelectedIds([]);
  }

  function toggleSelectionForIds(ids: CollectionPointId[], checked: boolean) {
    if (ids.length === 0) {
      return;
    }
    const idSet = new Set(ids.map((id) => toIdKey(id)));
    updateSelectedIds((current) => {
      if (checked) {
        return [...current, ...ids];
      }
      return current.filter((id) => !idSet.has(toIdKey(id)));
    });
  }

  function toggleRecordSelection(record: CollectionRecord, checked: boolean) {
    const recordId = getRecordId(record);
    if (recordId === null) {
      return;
    }
    toggleSelectionForIds([recordId], checked);
  }

  function selectAllVisibleRecords() {
    toggleSelectionForIds(visibleSelectableIds, true);
  }

  function startNewGroup() {
    if (!ensureNoPendingOperation()) {
      return;
    }
    setEditorState({
      kind: "new-group",
      functionName: "",
      text: "",
    });
  }

  function startAddText(functionName: string) {
    if (!ensureNoPendingOperation()) {
      return;
    }
    setEditorState({
      kind: "new-text",
      functionName,
      text: "",
    });
  }

  function startRenameGroup(functionName: string) {
    if (!ensureNoPendingOperation()) {
      return;
    }
    setEditorState({
      kind: "rename-group",
      oldFunctionName: functionName,
      newFunctionName: functionName,
    });
  }

  function startEditRecord(record: CollectionRecord) {
    if (!ensureNoPendingOperation()) {
      return;
    }
    if (record.id === undefined || record.id === null) {
      message.error("当前记录缺少 ID，无法编辑。");
      return;
    }
    setEditorState({
      kind: "edit-record",
      recordId: record.id,
      functionName: getFunctionName(record),
      text: getText(record),
    });
  }

  function startBatchCreate(functionName = "") {
    if (!ensureNoPendingOperation()) {
      return;
    }
    setBatchCreateState({
      functionName,
      texts: "",
    });
  }

  function startBatchMove() {
    if (!ensureNoPendingOperation()) {
      return;
    }
    if (selectedIds.length === 0) {
      message.warning("请先勾选要批量处理的记录。");
      return;
    }
    setBatchMoveState({
      functionName: "",
    });
  }

  function cancelEditing() {
    if (mutating) {
      return;
    }
    setEditorState(null);
  }

  function cancelBatchCreate() {
    if (mutating) {
      return;
    }
    setBatchCreateState(null);
  }

  function cancelBatchMove() {
    if (mutating) {
      return;
    }
    setBatchMoveState(null);
  }

  async function saveEditor() {
    if (!editorState) {
      return;
    }

    try {
      setMutating(true);

      if (editorState.kind === "new-group") {
        const functionName = editorState.functionName.trim();
        const text = editorState.text.trim();
        if (!functionName) {
          throw new Error("FunctionName 不能为空。");
        }
        if (!text) {
          throw new Error("text 不能为空。");
        }

        await api.createCollectionRecord(targetCollectionName, {
          payload: {
            FunctionName: functionName,
            text,
          },
          auto_vectorize: true,
        });
        message.success("新的 FunctionName 和首条 text 已创建。");
      }

      if (editorState.kind === "new-text") {
        const functionName = editorState.functionName.trim();
        const text = editorState.text.trim();
        if (!functionName) {
          throw new Error("FunctionName 不能为空。");
        }
        if (!text) {
          throw new Error("text 不能为空。");
        }

        await api.createCollectionRecord(targetCollectionName, {
          payload: {
            FunctionName: functionName,
            text,
          },
          auto_vectorize: true,
        });
        message.success("text 已添加到当前 FunctionName。");
      }

      if (editorState.kind === "rename-group") {
        const nextFunctionName = editorState.newFunctionName.trim();
        if (!nextFunctionName) {
          throw new Error("新的 FunctionName 不能为空。");
        }

        const targetRecords = allRecords.filter(
          (record) => getFunctionName(record) === editorState.oldFunctionName
        );
        for (const record of targetRecords) {
          if (record.id === undefined || record.id === null) {
            continue;
          }
          const payload = stripRecordMeta(record);
          payload.FunctionName = nextFunctionName;
          await api.updateCollectionRecord(targetCollectionName, record.id, {
            payload,
            auto_vectorize: false,
          });
        }
        message.success(`FunctionName 已更新，影响 ${targetRecords.length} 条记录。`);
      }

      if (editorState.kind === "edit-record") {
        const functionName = editorState.functionName.trim();
        const text = editorState.text.trim();
        if (!functionName) {
          throw new Error("FunctionName 不能为空。");
        }
        if (!text) {
          throw new Error("text 不能为空。");
        }

        const originalRecord = allRecords.find((record) => isSameId(record.id, editorState.recordId));
        if (!originalRecord) {
          throw new Error("未找到原始记录，无法保存。");
        }

        const payload = stripRecordMeta(originalRecord);
        payload.FunctionName = functionName;
        payload.text = text;

        await api.updateCollectionRecord(targetCollectionName, editorState.recordId, {
          payload,
          auto_vectorize: text !== getText(originalRecord),
        });
        message.success("记录已更新。");
      }

      await reloadCollection({ preserveScroll: true });
    } catch (requestError) {
      message.error(getErrorMessage(requestError, "保存失败。"));
    } finally {
      setMutating(false);
    }
  }

  async function saveBatchCreate() {
    if (!batchCreateState) {
      return;
    }

    const functionName = batchCreateState.functionName.trim();
    const { lines, ignoredCount } = normalizeBatchTexts(batchCreateState.texts);
    if (!functionName) {
      message.error("FunctionName 不能为空。");
      return;
    }
    if (lines.length === 0) {
      message.error("请至少输入一条有效的 text，每行一条。");
      return;
    }

    try {
      setMutating(true);
      let successCount = 0;
      let firstError = "";

      for (const text of lines) {
        try {
          await api.createCollectionRecord(targetCollectionName, {
            payload: {
              FunctionName: functionName,
              text,
            },
            auto_vectorize: true,
          });
          successCount += 1;
        } catch (requestError) {
          firstError ||= getErrorMessage(requestError, "批量新增失败。");
        }
      }

      if (successCount === 0) {
        throw new Error(firstError || "批量新增失败。");
      }

      const summary = [`已新增 ${successCount} 条 text`];
      if (ignoredCount > 0) {
        summary.push(`忽略 ${ignoredCount} 条空行或重复行`);
      }

      if (firstError) {
        message.warning(`${summary.join("，")}。部分记录未写入：${firstError}`);
      } else {
        message.success(`${summary.join("，")}。`);
      }

      await reloadCollection({ preserveScroll: true });
    } catch (requestError) {
      message.error(getErrorMessage(requestError, "批量新增失败。"));
    } finally {
      setMutating(false);
    }
  }

  async function saveBatchMove() {
    if (!batchMoveState) {
      return;
    }

    const nextFunctionName = batchMoveState.functionName.trim();
    if (!nextFunctionName) {
      message.error("新的 FunctionName 不能为空。");
      return;
    }

    const recordsToMove = selectedRecords.filter(
      (record) => getRecordId(record) !== null && getFunctionName(record) !== nextFunctionName
    );

    if (recordsToMove.length === 0) {
      message.info("选中的记录已经在这个 FunctionName 下了。");
      setBatchMoveState(null);
      return;
    }

    try {
      setMutating(true);
      let successCount = 0;
      let firstError = "";

      for (const record of recordsToMove) {
        const recordId = getRecordId(record);
        if (recordId === null) {
          continue;
        }

        try {
          const payload = stripRecordMeta(record);
          payload.FunctionName = nextFunctionName;
          await api.updateCollectionRecord(targetCollectionName, recordId, {
            payload,
            auto_vectorize: false,
          });
          successCount += 1;
        } catch (requestError) {
          firstError ||= getErrorMessage(requestError, "批量修改 FunctionName 失败。");
        }
      }

      if (successCount === 0) {
        throw new Error(firstError || "批量修改 FunctionName 失败。");
      }

      if (firstError) {
        message.warning(`已更新 ${successCount} 条记录，部分失败：${firstError}`);
      } else {
        message.success(`已将 ${successCount} 条记录移动到 ${nextFunctionName}。`);
      }

      await reloadCollection({ preserveScroll: true });
    } catch (requestError) {
      message.error(getErrorMessage(requestError, "批量修改 FunctionName 失败。"));
    } finally {
      setMutating(false);
    }
  }

  function confirmDeleteRecord(record: CollectionRecord) {
    if (record.id === undefined || record.id === null) {
      message.error("当前记录缺少 ID，无法删除。");
      return;
    }

    Modal.confirm({
      title: "删除这条 text？",
      content: `将从 ${getFunctionName(record)} 中删除这条 text，此操作不可撤销。`,
      okText: "删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        try {
          setMutating(true);
          await api.deleteCollectionRecord(targetCollectionName, record.id);
          message.success("记录已删除。");
          await reloadCollection({ preserveScroll: true });
        } catch (requestError) {
          message.error(getErrorMessage(requestError, "删除失败。"));
        } finally {
          setMutating(false);
        }
      },
    });
  }

  function confirmDeleteGroup(functionName: string) {
    const targetIds = allRecords
      .filter((record) => getFunctionName(record) === functionName)
      .map((record) => record.id)
      .filter((id): id is CollectionPointId => id !== undefined && id !== null);

    if (targetIds.length === 0) {
      message.warning("当前 FunctionName 下没有可删除的有效记录。");
      return;
    }

    Modal.confirm({
      title: `删除 FunctionName "${functionName}"？`,
      content: `这会删除该 FunctionName 下的 ${targetIds.length} 条 text，此操作不可撤销。`,
      okText: "删除全部",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        try {
          setMutating(true);
          await api.deleteCollectionRecords(targetCollectionName, targetIds);
          message.success(`已删除 ${targetIds.length} 条记录。`);
          await reloadCollection({ preserveScroll: true });
        } catch (requestError) {
          message.error(getErrorMessage(requestError, "删除 FunctionName 失败。"));
        } finally {
          setMutating(false);
        }
      },
    });
  }

  function confirmBatchDelete() {
    if (selectedIds.length === 0) {
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
          await api.deleteCollectionRecords(targetCollectionName, selectedIds);
          message.success(`已删除 ${selectedIds.length} 条记录。`);
          await reloadCollection({ preserveScroll: true });
        } catch (requestError) {
          message.error(getErrorMessage(requestError, "批量删除失败。"));
        } finally {
          setMutating(false);
        }
      },
    });
  }

  function renderTopEditor() {
    if (!editorState || editorState.kind !== "new-group") {
      return null;
    }

    return (
      <div className="mb-6 rounded-2xl border border-blue-200 bg-blue-50 p-5">
        <div className="mb-4 text-sm font-medium text-blue-700">新增 FunctionName</div>
        <div className="grid gap-3 lg:grid-cols-[280px_minmax(0,1fr)]">
          <Input
            value={editorState.functionName}
            onChange={(event) =>
              setEditorState((current) =>
                current && current.kind === "new-group"
                  ? { ...current, functionName: event.target.value }
                  : current
              )
            }
            placeholder="输入新的 FunctionName"
          />
          <Input.TextArea
            autoSize={{ minRows: 2, maxRows: 4 }}
            value={editorState.text}
            onChange={(event) =>
              setEditorState((current) =>
                current && current.kind === "new-group" ? { ...current, text: event.target.value } : current
              )
            }
            placeholder="输入该 FunctionName 的第一条 text"
          />
        </div>
        <div className="mt-4 flex gap-3">
          <Button type="primary" onClick={() => void saveEditor()} loading={mutating}>
            保存
          </Button>
          <Button onClick={cancelEditing} disabled={mutating}>
            取消
          </Button>
        </div>
      </div>
    );
  }

  function renderGroupHeader(group: IntentionGroup) {
    const isRenamingGroup =
      editorState?.kind === "rename-group" && editorState.oldFunctionName === group.functionName;
    const groupSelectableIds = getSelectableIds(group.records);
    const groupSelectedCount = groupSelectableIds.filter((id) => selectedIdSet.has(toIdKey(id))).length;
    const groupAllSelected = groupSelectableIds.length > 0 && groupSelectedCount === groupSelectableIds.length;
    const groupIndeterminate = groupSelectedCount > 0 && groupSelectedCount < groupSelectableIds.length;

    if (isRenamingGroup) {
      return (
        <div className="flex flex-wrap items-center gap-3">
          <Input
            className="max-w-md"
            value={editorState.newFunctionName}
            onChange={(event) =>
              setEditorState((current) =>
                current && current.kind === "rename-group"
                  ? { ...current, newFunctionName: event.target.value }
                  : current
              )
            }
            placeholder="新的 FunctionName"
          />
          <Button type="primary" size="small" onClick={() => void saveEditor()} loading={mutating}>
            保存
          </Button>
          <Button size="small" onClick={cancelEditing} disabled={mutating}>
            取消
          </Button>
        </div>
      );
    }

    return (
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex min-w-0 flex-wrap items-center gap-3">
          {groupSelectableIds.length > 0 ? (
            <Checkbox
              checked={groupAllSelected}
              indeterminate={groupIndeterminate}
              onChange={(event) => toggleSelectionForIds(groupSelectableIds, event.target.checked)}
              disabled={actionDisabled}
            />
          ) : null}
          <h2 className="text-lg font-semibold text-slate-800">{group.functionName}</h2>
          <Tag color="blue">{group.records.length} 条 text</Tag>
          {groupSelectedCount > 0 ? <Tag color="processing">已选 {groupSelectedCount}</Tag> : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="small"
            onClick={() => startAddText(group.functionName)}
            disabled={actionDisabled}
          >
            新增 text
          </Button>
          <Button
            size="small"
            onClick={() => startBatchCreate(group.functionName)}
            disabled={actionDisabled}
          >
            批量新增
          </Button>
          <Button
            size="small"
            onClick={() => startRenameGroup(group.functionName)}
            disabled={actionDisabled}
          >
            重命名 FunctionName
          </Button>
          <Button
            danger
            size="small"
            onClick={() => confirmDeleteGroup(group.functionName)}
            disabled={actionDisabled}
          >
            删除 FunctionName
          </Button>
        </div>
      </div>
    );
  }

  function renderNewTextEditor(group: IntentionGroup) {
    const isAddingText =
      editorState?.kind === "new-text" && editorState.functionName === group.functionName;
    if (!isAddingText) {
      return null;
    }

    return (
      <div className="mb-4 rounded-xl border border-emerald-200 bg-emerald-50 p-4">
        <div className="mb-3 text-sm font-medium text-emerald-700">
          向 {group.functionName} 新增 text
        </div>
        <Input.TextArea
          autoSize={{ minRows: 2, maxRows: 4 }}
          value={editorState.text}
          onChange={(event) =>
            setEditorState((current) =>
              current && current.kind === "new-text" ? { ...current, text: event.target.value } : current
            )
          }
          placeholder="输入新的 text"
        />
        <div className="mt-3 flex gap-3">
          <Button type="primary" size="small" onClick={() => void saveEditor()} loading={mutating}>
            保存
          </Button>
          <Button size="small" onClick={cancelEditing} disabled={mutating}>
            取消
          </Button>
        </div>
      </div>
    );
  }

  function renderRecord(record: CollectionRecord) {
    const isEditingRecord =
      editorState?.kind === "edit-record" && isSameId(editorState.recordId, record.id);

    if (isEditingRecord) {
      return (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
          <div className="grid gap-3 lg:grid-cols-[260px_minmax(0,1fr)]">
            <Input
              value={editorState.functionName}
              onChange={(event) =>
                setEditorState((current) =>
                  current && current.kind === "edit-record"
                    ? { ...current, functionName: event.target.value }
                    : current
                )
              }
              placeholder="FunctionName"
            />
            <Input.TextArea
              autoSize={{ minRows: 2, maxRows: 4 }}
              value={editorState.text}
              onChange={(event) =>
                setEditorState((current) =>
                  current && current.kind === "edit-record" ? { ...current, text: event.target.value } : current
                )
              }
              placeholder="text"
            />
          </div>
          <div className="mt-3 flex gap-3">
            <Button type="primary" size="small" onClick={() => void saveEditor()} loading={mutating}>
              保存
            </Button>
            <Button size="small" onClick={cancelEditing} disabled={mutating}>
              取消
            </Button>
          </div>
        </div>
      );
    }

    const recordId = getRecordId(record);
    const selected = recordId !== null && selectedIdSet.has(toIdKey(recordId));
    const text = getText(record);

    return (
      <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
        <div className="flex gap-3">
          <div className="pt-1">
            <Checkbox
              checked={selected}
              disabled={actionDisabled || recordId === null}
              onChange={(event) => toggleRecordSelection(record, event.target.checked)}
            />
          </div>
          <div className="min-w-0 flex-1">
            <div className="whitespace-pre-wrap text-sm leading-7 text-slate-700">
              {text || <span className="text-slate-400">暂无 text</span>}
            </div>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
              <div className="text-xs text-slate-400">ID: {String(record.id ?? "-")}</div>
              <div className="flex gap-2">
                <Button
                  size="small"
                  onClick={() => startEditRecord(record)}
                  disabled={actionDisabled}
                >
                  编辑 text / FunctionName
                </Button>
                <Button
                  danger
                  size="small"
                  onClick={() => confirmDeleteRecord(record)}
                  disabled={actionDisabled}
                >
                  删除
                </Button>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div ref={scrollContainerRef} className="h-full overflow-auto bg-slate-50">
      <div className="mx-auto max-w-7xl px-6 py-6">
        <div className="mb-6 rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="flex items-start gap-3">
              <div className="rounded-2xl bg-blue-100 p-3 text-blue-700">
                <Database className="h-5 w-5" />
              </div>
              <div>
                <h1 className="text-2xl font-bold text-slate-900">IntentionSelection</h1>
                <p className="mt-1 text-sm text-slate-500">
                  按 FunctionName 分组展示 text，并支持批量新增、批量改组、批量删除与单条编辑。
                </p>
                <div className="mt-2 flex flex-wrap gap-3 text-xs text-slate-400">
                  <span>Collection: {targetCollectionName}</span>
                  <span>总记录数: {collectionDetail?.total ?? 0}</span>
                  {collectionDetail && collectionDetail.total > collectionDetail.records.length ? (
                    <span>当前前端仅展示前 {collectionDetail.records.length} 条</span>
                  ) : null}
                </div>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <Button
                icon={<RefreshCcw className="h-4 w-4" />}
                onClick={() => {
                  void reloadCollection({ preserveScroll: true });
                }}
                disabled={mutating}
              >
                刷新
              </Button>
              <Button
                type="primary"
                icon={<Plus className="h-4 w-4" />}
                onClick={startNewGroup}
                disabled={actionDisabled}
              >
                新增 FunctionName
              </Button>
              <Button
                icon={<Plus className="h-4 w-4" />}
                onClick={() => startBatchCreate()}
                disabled={actionDisabled}
              >
                批量新增数据
              </Button>
              <div className="w-72">
                <Input
                  placeholder="搜索 FunctionName 或 text..."
                  prefix={<Search className="h-4 w-4 text-slate-400" />}
                  value={searchText}
                  onChange={(event) => setSearchText(event.target.value)}
                  allowClear
                />
              </div>
            </div>
          </div>

          <div className="mt-5 flex flex-wrap items-center gap-3 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3">
            <Tag color={selectedIds.length > 0 ? "processing" : "default"}>已选 {selectedIds.length} 条</Tag>
            <Button
              size="small"
              onClick={selectAllVisibleRecords}
              disabled={actionDisabled || visibleSelectableIds.length === 0}
            >
              全选当前结果
            </Button>
            <Button size="small" onClick={clearSelection} disabled={actionDisabled || selectedIds.length === 0}>
              清空选择
            </Button>
            <Button
              size="small"
              icon={<Pencil className="h-4 w-4" />}
              onClick={startBatchMove}
              disabled={actionDisabled || selectedIds.length === 0}
            >
              批量改 FunctionName
            </Button>
            <Button
              danger
              size="small"
              icon={<Trash2 className="h-4 w-4" />}
              onClick={confirmBatchDelete}
              disabled={actionDisabled || selectedIds.length === 0}
            >
              批量删除
            </Button>
            <span className="text-xs text-slate-500">支持单条勾选、整组勾选和按筛选结果全选。</span>
          </div>
        </div>

        {error ? (
          <div className="mb-6 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        ) : null}

        {renderTopEditor()}

        {loading ? (
          <div className="rounded-3xl border border-slate-200 bg-white p-10 text-center text-sm text-slate-500">
            正在加载 intention 数据...
          </div>
        ) : filteredGroups.length === 0 ? (
          <div className="rounded-3xl border border-slate-200 bg-white p-10">
            <Empty description="没有可展示的 IntentionSelection 数据。" />
          </div>
        ) : (
          <div className="space-y-5">
            {filteredGroups.map((group) => (
              <section key={group.functionName} className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
                {renderGroupHeader(group)}

                <div className="mt-5">
                  {renderNewTextEditor(group)}
                  <div className="space-y-3">
                    {group.records.map((record) => (
                      <div key={String(record.id ?? `${group.functionName}-${getText(record)}`)}>
                        {renderRecord(record)}
                      </div>
                    ))}
                  </div>
                </div>
              </section>
            ))}
          </div>
        )}

        {editorState ? (
          <div className="fixed bottom-6 right-6 z-30 flex items-center gap-3 rounded-full border border-slate-200 bg-white px-4 py-3 shadow-xl">
            <span className="text-sm text-slate-600">
              {editorState.kind === "new-group" && "正在新增 FunctionName"}
              {editorState.kind === "new-text" && `正在向 ${editorState.functionName} 新增 text`}
              {editorState.kind === "rename-group" && `正在重命名 ${editorState.oldFunctionName}`}
              {editorState.kind === "edit-record" && "正在编辑一条记录"}
            </span>
            <Button type="primary" size="small" onClick={() => void saveEditor()} loading={mutating}>
              保存
            </Button>
            <Button size="small" onClick={cancelEditing} disabled={mutating}>
              <X className="h-4 w-4" />
            </Button>
          </div>
        ) : null}

        {selectedIds.length > 0 ? (
          <div className="fixed bottom-6 left-1/2 z-20 flex -translate-x-1/2 items-center gap-3 rounded-full border border-slate-200 bg-white px-4 py-3 shadow-xl">
            <Tag color="processing" className="mb-0">
              已选 {selectedIds.length} 条
            </Tag>
            <Button size="small" onClick={clearSelection} disabled={actionDisabled}>
              清空选择
            </Button>
            <Button
              size="small"
              icon={<Pencil className="h-4 w-4" />}
              onClick={startBatchMove}
              disabled={actionDisabled}
            >
              批量改 FunctionName
            </Button>
            <Button
              danger
              size="small"
              icon={<Trash2 className="h-4 w-4" />}
              onClick={confirmBatchDelete}
              disabled={actionDisabled}
            >
              批量删除
            </Button>
          </div>
        ) : null}

        <Modal
          title="批量新增数据"
          open={Boolean(batchCreateState)}
          onOk={() => void saveBatchCreate()}
          onCancel={cancelBatchCreate}
          okText="开始新增"
          cancelText="取消"
          confirmLoading={mutating}
          cancelButtonProps={{ disabled: mutating }}
          destroyOnClose
        >
          <div className="space-y-4">
            <div>
              <div className="mb-2 text-sm font-medium text-slate-700">FunctionName</div>
              <Input
                value={batchCreateState?.functionName ?? ""}
                onChange={(event) =>
                  setBatchCreateState((current) =>
                    current ? { ...current, functionName: event.target.value } : current
                  )
                }
                placeholder="输入目标 FunctionName"
              />
            </div>
            <div>
              <div className="mb-2 text-sm font-medium text-slate-700">批量 text</div>
              <Input.TextArea
                rows={8}
                value={batchCreateState?.texts ?? ""}
                onChange={(event) =>
                  setBatchCreateState((current) =>
                    current ? { ...current, texts: event.target.value } : current
                  )
                }
                placeholder="每行一条 text"
              />
            </div>
            <div className="flex flex-wrap gap-2">
              <Tag color="blue">待新增 {batchCreatePreview.lines.length} 条</Tag>
              {batchCreatePreview.ignoredCount > 0 ? (
                <Tag>{batchCreatePreview.ignoredCount} 条空行或重复行会被忽略</Tag>
              ) : null}
            </div>
          </div>
        </Modal>

        <Modal
          title="批量修改 FunctionName"
          open={Boolean(batchMoveState)}
          onOk={() => void saveBatchMove()}
          onCancel={cancelBatchMove}
          okText="开始修改"
          cancelText="取消"
          confirmLoading={mutating}
          cancelButtonProps={{ disabled: mutating }}
          destroyOnClose
        >
          <div className="space-y-4">
            <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
              已选 {selectedRecords.length} 条记录，涉及 {selectedFunctionGroupCount} 个 FunctionName。
            </div>
            <div>
              <div className="mb-2 text-sm font-medium text-slate-700">新的 FunctionName</div>
              <Input
                value={batchMoveState?.functionName ?? ""}
                onChange={(event) =>
                  setBatchMoveState((current) =>
                    current ? { ...current, functionName: event.target.value } : current
                  )
                }
                placeholder="输入要移动到的 FunctionName"
              />
            </div>
          </div>
        </Modal>
      </div>
    </div>
  );
}
