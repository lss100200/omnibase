import {
  addBudgets,
  buildRowsQuery,
  parseCitationResponse,
  parseRowsResponse,
  parseSchemaResponse,
  parseSearchResponse,
  requireLogicalId,
  type CitationReadResponse,
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
}
