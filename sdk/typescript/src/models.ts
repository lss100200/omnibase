export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

export interface OrderBy {
  columnId: string;
  direction?: "asc" | "desc";
}

export interface RowsQuery {
  columns: string[];
  filter?: Record<string, JsonValue>;
  orderBy?: OrderBy[];
  cursor?: string;
  limit?: number;
  timeoutMs?: number;
  maxBytes?: number;
}

export interface SchemaColumn {
  id: string;
  displayName: string;
  type: string;
  nullable: boolean;
}

export interface SchemaReadResponse {
  resourceId: string;
  resourceVersion: number;
  columns: SchemaColumn[];
}

export interface RowsReadResponse {
  resourceId: string;
  resourceVersion: number;
  rows: Array<Record<string, JsonValue>>;
  nextCursor?: string;
  rowCount: number;
  bytesOut: number;
  truncated: boolean;
}

export interface RagSearchResult {
  citationId: string;
  documentId: string;
  score: number;
  snippet?: string;
  pageNumber?: number;
}

export interface RagSearchResponse {
  resourceId: string;
  results: RagSearchResult[];
  totalFound: number;
  bytesOut: number;
  truncated: boolean;
}

export interface Citation {
  citationId: string;
  documentId: string;
  content: string;
  pageNumber?: number;
  charStart?: number;
  charEnd?: number;
}

export interface CitationReadResponse {
  resourceId: string;
  citations: Citation[];
  bytesOut: number;
  truncated: boolean;
}

const forbiddenRequestKeys = new Set([
  "database_url",
  "minio_key",
  "object_key",
  "path",
  "physical_locator",
  "provider_handle",
  "raw_sql",
  "schema",
  "sql",
  "statement",
  "table",
  "tenant_id",
  "token",
  "workspace_id",
]);

export function requireLogicalId(value: string, label: string): string {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu.test(value)) {
    throw new TypeError(`${label} must be an opaque UUID`);
  }
  return value;
}

export function rejectForbiddenRequestKeys(value: unknown): void {
  if (Array.isArray(value)) {
    value.forEach(rejectForbiddenRequestKeys);
    return;
  }
  if (!isObject(value)) return;
  for (const [key, nested] of Object.entries(value)) {
    if (forbiddenRequestKeys.has(key.toLowerCase())) {
      throw new TypeError(`Gateway requests do not accept ${JSON.stringify(key)}`);
    }
    rejectForbiddenRequestKeys(nested);
  }
}

export function parseSchemaResponse(raw: unknown): SchemaReadResponse {
  const value = exactObject(raw, ["resource_id", "resource_version", "columns"], [], "schema");
  if (!Array.isArray(value.columns)) throw new TypeError("columns must be an array");
  return {
    resourceId: requireLogicalId(asString(value.resource_id, "resource_id"), "resource_id"),
    resourceVersion: asInteger(value.resource_version, "resource_version", 1),
    columns: value.columns.map((item) => {
      const column = exactObject(item, ["id", "display_name", "type", "nullable"], [], "column");
      return {
        id: requireLogicalId(asString(column.id, "column.id"), "column.id"),
        displayName: asString(column.display_name, "display_name"),
        type: asString(column.type, "type"),
        nullable: asBoolean(column.nullable, "nullable"),
      };
    }),
  };
}

export function parseRowsResponse(raw: unknown): RowsReadResponse {
  const value = exactObject(
    raw,
    ["resource_id", "resource_version", "rows", "row_count", "bytes_out", "truncated"],
    ["next_cursor"],
    "rows",
  );
  if (!Array.isArray(value.rows) || !value.rows.every(isObject)) {
    throw new TypeError("rows must be an array of objects");
  }
  value.rows.forEach((row) => assertJsonValue(row, "row"));
  const result: RowsReadResponse = {
    resourceId: requireLogicalId(asString(value.resource_id, "resource_id"), "resource_id"),
    resourceVersion: asInteger(value.resource_version, "resource_version", 1),
    rows: value.rows as Array<Record<string, JsonValue>>,
    rowCount: asInteger(value.row_count, "row_count", 0),
    bytesOut: asInteger(value.bytes_out, "bytes_out", 0),
    truncated: asBoolean(value.truncated, "truncated"),
  };
  if (value.next_cursor !== undefined) result.nextCursor = asString(value.next_cursor, "next_cursor");
  return result;
}

export function parseSearchResponse(raw: unknown): RagSearchResponse {
  const value = exactObject(
    raw,
    ["resource_id", "results", "total_found", "bytes_out", "truncated"],
    [],
    "search",
  );
  if (!Array.isArray(value.results)) throw new TypeError("results must be an array");
  return {
    resourceId: requireLogicalId(asString(value.resource_id, "resource_id"), "resource_id"),
    results: value.results.map((item) => {
      const hit = exactObject(
        item,
        ["citation_id", "document_id", "score"],
        ["snippet", "page_number"],
        "search result",
      );
      const parsed: RagSearchResult = {
        citationId: requireLogicalId(asString(hit.citation_id, "citation_id"), "citation_id"),
        documentId: requireLogicalId(asString(hit.document_id, "document_id"), "document_id"),
        score: asNumber(hit.score, "score"),
      };
      if (hit.snippet !== undefined) parsed.snippet = asString(hit.snippet, "snippet");
      if (hit.page_number !== undefined) parsed.pageNumber = asInteger(hit.page_number, "page_number", 1);
      return parsed;
    }),
    totalFound: asInteger(value.total_found, "total_found", 0),
    bytesOut: asInteger(value.bytes_out, "bytes_out", 0),
    truncated: asBoolean(value.truncated, "truncated"),
  };
}

export function parseCitationResponse(raw: unknown): CitationReadResponse {
  const value = exactObject(
    raw,
    ["resource_id", "citations", "bytes_out", "truncated"],
    [],
    "citations",
  );
  if (!Array.isArray(value.citations)) throw new TypeError("citations must be an array");
  return {
    resourceId: requireLogicalId(asString(value.resource_id, "resource_id"), "resource_id"),
    citations: value.citations.map((item) => {
      const citation = exactObject(
        item,
        ["citation_id", "document_id", "content"],
        ["page_number", "char_start", "char_end"],
        "citation",
      );
      const parsed: Citation = {
        citationId: requireLogicalId(asString(citation.citation_id, "citation_id"), "citation_id"),
        documentId: requireLogicalId(asString(citation.document_id, "document_id"), "document_id"),
        content: asString(citation.content, "content"),
      };
      if (citation.page_number !== undefined) parsed.pageNumber = asInteger(citation.page_number, "page_number", 1);
      if (citation.char_start !== undefined) parsed.charStart = asInteger(citation.char_start, "char_start", 0);
      if (citation.char_end !== undefined) parsed.charEnd = asInteger(citation.char_end, "char_end", 0);
      return parsed;
    }),
    bytesOut: asInteger(value.bytes_out, "bytes_out", 0),
    truncated: asBoolean(value.truncated, "truncated"),
  };
}

export function buildRowsQuery(query: RowsQuery): Record<string, JsonValue> {
  if (query.columns.length === 0) throw new TypeError("columns must not be empty");
  if (query.columns.length > 50) throw new TypeError("columns cannot contain more than 50 IDs");
  const columns = query.columns.map((column) => requireLogicalId(column, "column_id"));
  if (new Set(columns).size !== columns.length) throw new TypeError("columns must be unique");
  const limit = query.limit ?? 50;
  bounded(limit, 1, 100, "limit");
  const payload: Record<string, JsonValue> = { columns, limit };
  if (query.filter !== undefined) {
    rejectForbiddenRequestKeys(query.filter);
    assertJsonValue(query.filter, "filter");
    payload.filter = query.filter;
  }
  if (query.orderBy !== undefined) {
    if (query.orderBy.length > 5) throw new TypeError("orderBy cannot contain more than 5 fields");
    payload.order_by = query.orderBy.map((item) => ({
      column_id: requireLogicalId(item.columnId, "column_id"),
      direction: requireDirection(item.direction ?? "asc"),
    }));
  }
  if (query.cursor !== undefined) {
    if (query.cursor.length < 16 || query.cursor.length > 512) throw new TypeError("cursor is invalid");
    payload.cursor = query.cursor;
  }
  addBudgets(payload, query.maxBytes, query.timeoutMs);
  return payload;
}

export function addBudgets(
  payload: Record<string, JsonValue>,
  maxBytes?: number,
  timeoutMs?: number,
): void {
  if (maxBytes !== undefined) payload.max_bytes = bounded(maxBytes, 1, 1_048_576, "maxBytes");
  if (timeoutMs !== undefined) payload.timeout_ms = bounded(timeoutMs, 1, 5000, "timeoutMs");
}

function exactObject(
  raw: unknown,
  required: string[],
  optional: string[],
  label: string,
): Record<string, unknown> {
  if (!isObject(raw)) throw new TypeError(`${label} must be an object`);
  const allowed = new Set([...required, ...optional]);
  const missing = required.filter((key) => !(key in raw));
  const extra = Object.keys(raw).filter((key) => !allowed.has(key));
  if (missing.length > 0 || extra.length > 0) {
    throw new TypeError(`Invalid ${label} fields; missing=${missing.join(",")}, extra=${extra.join(",")}`);
  }
  return raw;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown, label: string): string {
  if (typeof value !== "string") throw new TypeError(`${label} must be a string`);
  return value;
}

function asNumber(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new TypeError(`${label} must be a number`);
  return value;
}

function asInteger(value: unknown, label: string, minimum: number): number {
  if (!Number.isInteger(value) || (value as number) < minimum) {
    throw new TypeError(`${label} must be an integer >= ${minimum}`);
  }
  return value as number;
}

function requireDirection(value: unknown): "asc" | "desc" {
  if (value !== "asc" && value !== "desc") throw new TypeError("direction must be asc or desc");
  return value;
}

function assertJsonValue(value: unknown, label: string, depth = 0): asserts value is JsonValue {
  if (depth > 8) throw new TypeError(`${label} exceeds the maximum nesting depth`);
  if (value === null || typeof value === "string" || typeof value === "boolean") return;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new TypeError(`${label} must not contain NaN or Infinity`);
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((item) => assertJsonValue(item, label, depth + 1));
    return;
  }
  if (isObject(value)) {
    Object.values(value).forEach((item) => assertJsonValue(item, label, depth + 1));
    return;
  }
  throw new TypeError(`${label} must contain JSON values only`);
}

function asBoolean(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") throw new TypeError(`${label} must be a boolean`);
  return value;
}

function bounded(value: number, minimum: number, maximum: number, label: string): number {
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new TypeError(`${label} must be between ${minimum} and ${maximum}`);
  }
  return value;
}
