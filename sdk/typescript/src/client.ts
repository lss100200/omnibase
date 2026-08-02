import {
  addBudgets,
  buildDerivedChunks,
  buildRowsQuery,
  encodeBase64,
  parseArtifactReadResponse,
  parseArtifactWriteResponse,
  parseCitationResponse,
  parseDerivedCreateResponse,
  parseDerivedDeleteResponse,
  parseRowsResponse,
  parseSchemaResponse,
  parseSearchResponse,
  requireLogicalId,
  type ArtifactReadResponse,
  type ArtifactWriteResponse,
  type CitationReadResponse,
  type DerivedChunkWrite,
  type DerivedCreateResponse,
  type DerivedDeleteResponse,
  type JsonValue,
  type RagSearchResponse,
  type RowsQuery,
  type RowsReadResponse,
  type SchemaReadResponse,
} from "./models.js";
import {
  FetchTransport,
  raiseForError,
  type FetchTransportOptions,
  type Transport,
} from "./transport.js";

export class OmniBaseClient {
  readonly #transport: Transport;

  constructor(transport: Transport) {
    this.#transport = transport;
  }

  static fromFetch(options: FetchTransportOptions): OmniBaseClient {
    return new OmniBaseClient(new FetchTransport(options));
  }

  async readSchema(resourceId: string): Promise<SchemaReadResponse> {
    const response = await this.#transport.request("POST", "/gateway/v1/data/schema/read", {
      resource_id: requireLogicalId(resourceId, "resourceId"),
    });
    raiseForError(response);
    return parseSchemaResponse(response.body);
  }

  async readRows(resourceId: string, query: RowsQuery): Promise<RowsReadResponse> {
    const response = await this.#transport.request("POST", "/gateway/v1/data/rows/read", {
      resource_id: requireLogicalId(resourceId, "resourceId"),
      query: buildRowsQuery(query),
    });
    raiseForError(response);
    return parseRowsResponse(response.body);
  }

  async ragSearch(
    resourceId: string,
    query: string,
    options: { topK?: number; maxBytes?: number; timeoutMs?: number } = {},
  ): Promise<RagSearchResponse> {
    if (!query || query.length > 2000) throw new TypeError("query must contain 1..2000 characters");
    const topK = options.topK ?? 10;
    if (!Number.isInteger(topK) || topK < 1 || topK > 20) {
      throw new TypeError("topK must be between 1 and 20");
    }
    const body: Record<string, JsonValue> = {
      resource_id: requireLogicalId(resourceId, "resourceId"),
      query,
      top_k: topK,
    };
    addBudgets(body, options.maxBytes, options.timeoutMs);
    const response = await this.#transport.request("POST", "/gateway/v1/rag/search", body);
    raiseForError(response);
    return parseSearchResponse(response.body);
  }

  async readCitations(
    resourceId: string,
    citationIds: string[],
    options: { maxBytes?: number; timeoutMs?: number } = {},
  ): Promise<CitationReadResponse> {
    if (citationIds.length < 1 || citationIds.length > 20) {
      throw new TypeError("citationIds must contain between 1 and 20 IDs");
    }
    const normalized = citationIds.map((id) => requireLogicalId(id, "citationId"));
    if (new Set(normalized).size !== normalized.length) {
      throw new TypeError("citationIds must be unique");
    }
    const body: Record<string, JsonValue> = {
      resource_id: requireLogicalId(resourceId, "resourceId"),
      citation_ids: normalized,
    };
    addBudgets(body, options.maxBytes, options.timeoutMs);
    const response = await this.#transport.request("POST", "/gateway/v1/rag/citations/read", body);
    raiseForError(response);
    return parseCitationResponse(response.body);
  }

  async readArtifact(
    resourceId: string,
    resourceVersion: number,
    options: { maxBytes?: number } = {},
  ): Promise<ArtifactReadResponse> {
    if (!Number.isInteger(resourceVersion) || resourceVersion < 1) throw new TypeError("resourceVersion must be >= 1");
    const maxBytes = options.maxBytes ?? 1_048_576;
    if (!Number.isInteger(maxBytes) || maxBytes < 1 || maxBytes > 1_048_576) {
      throw new TypeError("maxBytes must be between 1 and 1048576");
    }
    const response = await this.#transport.request("POST", "/gateway/v1/artifacts/read", {
      resource_id: requireLogicalId(resourceId, "resourceId"),
      resource_version: resourceVersion,
      max_bytes: maxBytes,
    });
    raiseForError(response);
    const artifact = parseArtifactReadResponse(response.body);
    const digest = await crypto.subtle.digest("SHA-256", artifact.content);
    const actual = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
    if (actual !== artifact.contentSha256) throw new TypeError("artifact content_sha256 does not match content");
    return artifact;
  }

  async writeArtifact(input: {
    idempotencyKey: string;
    displayName: string;
    mediaType: string;
    content: Uint8Array;
    sourceResourceIds?: string[];
  }): Promise<ArtifactWriteResponse> {
    if (!input.idempotencyKey || input.idempotencyKey.length > 128) throw new TypeError("idempotencyKey is invalid");
    if (!input.displayName || input.displayName.length > 200) throw new TypeError("displayName is invalid");
    if (input.content.byteLength > 1_048_576) throw new TypeError("content exceeds the artifact byte limit");
    const sources = (input.sourceResourceIds ?? []).map((id) => requireLogicalId(id, "sourceResourceId"));
    if (sources.length > 32 || new Set(sources).size !== sources.length) throw new TypeError("sourceResourceIds are invalid");
    const digest = await crypto.subtle.digest("SHA-256", input.content);
    const contentSha256 = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
    const response = await this.#transport.request("POST", "/gateway/v1/artifacts/write", {
      idempotency_key: input.idempotencyKey,
      display_name: input.displayName,
      media_type: input.mediaType,
      size_bytes: input.content.byteLength,
      content_sha256: contentSha256,
      content_base64: encodeBase64(input.content),
      source_resource_ids: sources,
    });
    raiseForError(response);
    return parseArtifactWriteResponse(response.body);
  }

  async createDerived(input: {
    idempotencyKey: string;
    displayName: string;
    sourceResourceIds: string[];
    chunks: DerivedChunkWrite[];
  }): Promise<DerivedCreateResponse> {
    if (!input.idempotencyKey || input.idempotencyKey.length > 128) throw new TypeError("idempotencyKey is invalid");
    if (!input.displayName || input.displayName.length > 200) throw new TypeError("displayName is invalid");
    const sources = input.sourceResourceIds.map((id) => requireLogicalId(id, "sourceResourceId"));
    if (sources.length < 1 || sources.length > 32 || new Set(sources).size !== sources.length) {
      throw new TypeError("sourceResourceIds are invalid");
    }
    const response = await this.#transport.request("POST", "/gateway/v1/rag/derived/create", {
      idempotency_key: input.idempotencyKey,
      display_name: input.displayName,
      source_resource_ids: sources,
      chunks: buildDerivedChunks(input.chunks, new Set(sources)),
    });
    raiseForError(response);
    return parseDerivedCreateResponse(response.body);
  }

  async deleteDerived(
    resourceId: string,
    resourceVersion: number,
    idempotencyKey: string,
  ): Promise<DerivedDeleteResponse> {
    if (!Number.isInteger(resourceVersion) || resourceVersion < 1) throw new TypeError("resourceVersion must be >= 1");
    if (!idempotencyKey || idempotencyKey.length > 128) throw new TypeError("idempotencyKey is invalid");
    const response = await this.#transport.request("POST", "/gateway/v1/rag/derived/delete", {
      resource_id: requireLogicalId(resourceId, "resourceId"),
      resource_version: resourceVersion,
      idempotency_key: idempotencyKey,
    });
    raiseForError(response);
    return parseDerivedDeleteResponse(response.body);
  }
}
