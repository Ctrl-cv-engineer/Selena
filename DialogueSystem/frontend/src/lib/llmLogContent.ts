const INLINE_DATA_URL_RE = /^data:([^;,]+)[^,]*,/i;

function summarizeInlineDataUrl(value: string): string {
  const mimeType = value.match(INLINE_DATA_URL_RE)?.[1] ?? "unknown";
  return `[inline ${mimeType} omitted, ${value.length} chars]`;
}

function sanitizeLLMLogValue(value: unknown, depth = 0): unknown {
  if (typeof value === "string") {
    return INLINE_DATA_URL_RE.test(value) ? summarizeInlineDataUrl(value) : value;
  }
  if (value == null || typeof value === "number" || typeof value === "boolean") {
    return value;
  }
  if (Array.isArray(value)) {
    if (depth >= 6) {
      return "[nested array omitted]";
    }
    return value.map((item) => sanitizeLLMLogValue(item, depth + 1));
  }
  if (typeof value === "object") {
    if (depth >= 6) {
      return "[nested object omitted]";
    }
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([key, entryValue]) => [
        key,
        sanitizeLLMLogValue(entryValue, depth + 1),
      ])
    );
  }
  return String(value);
}

function stringifySanitizedValue(value: unknown): string {
  try {
    return JSON.stringify(sanitizeLLMLogValue(value), null, 2);
  } catch {
    return String(value ?? "");
  }
}

function formatContentBlock(block: unknown): string {
  if (typeof block === "string") {
    return INLINE_DATA_URL_RE.test(block) ? summarizeInlineDataUrl(block) : block;
  }
  if (block == null) {
    return "";
  }
  if (typeof block === "number" || typeof block === "boolean") {
    return String(block);
  }
  if (Array.isArray(block)) {
    return stringifySanitizedValue(block);
  }
  if (typeof block !== "object") {
    return stringifySanitizedValue(block);
  }

  const record = block as Record<string, unknown>;
  const type = String(record.type ?? "").trim();

  if (type === "text") {
    return typeof record.text === "string" ? record.text : stringifySanitizedValue(record.text);
  }

  if (type === "image_url") {
    const imagePayload =
      record.image_url && typeof record.image_url === "object"
        ? (record.image_url as Record<string, unknown>)
        : null;
    const rawUrl = typeof imagePayload?.url === "string" ? imagePayload.url : "";
    return rawUrl
      ? `[image_url] ${INLINE_DATA_URL_RE.test(rawUrl) ? summarizeInlineDataUrl(rawUrl) : rawUrl}`
      : "[image_url]";
  }

  return stringifySanitizedValue(record);
}

export function formatLLMLogMessageContent(content: unknown): string {
  if (typeof content === "string") {
    return INLINE_DATA_URL_RE.test(content) ? summarizeInlineDataUrl(content) : content;
  }
  if (content == null) {
    return "";
  }
  if (Array.isArray(content)) {
    return content.map((block) => formatContentBlock(block)).join("\n\n");
  }
  if (typeof content === "number" || typeof content === "boolean") {
    return String(content);
  }
  return stringifySanitizedValue(content);
}
