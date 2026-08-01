/**
 * Backend API type definitions (mirrors backend Pydantic schemas).
 *
 * These types are the single source of truth for backend ↔ frontend contract.
 * When a backend schema changes, update this file and `tsc --noEmit` will flag
 * every call site that needs updating.
 *
 * Naming convention: `<Entity><Action>Request` / `<Entity>Response` etc.
 */

// -----------------------------------------------------------
// Auth
// -----------------------------------------------------------
export interface UserPublic {
  id: string
  email: string
  is_tenant_admin: boolean
  created_at: string
}

export interface TenantPublic {
  id: string
  name: string
  slug: string
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: 'bearer'
  expires_in: number
  user: UserPublic
  tenant: TenantPublic
}

export interface RefreshResponse {
  access_token: string
  token_type: 'bearer'
  expires_in: number
}

export interface RegisterRequest {
  email: string
  password: string
  tenant_name?: string
}

export interface LoginRequest {
  email: string
  password: string
}

// -----------------------------------------------------------
// Documents
// -----------------------------------------------------------
export type DocumentStatus = 'pending' | 'queued' | 'processing' | 'indexed' | 'failed'

export interface DocumentRead {
  id: string
  filename: string
  mime_type: string
  size_bytes: number
  status: DocumentStatus
  page_count: number | null
  error_detail: string | null
  metadata: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface DocumentList {
  items: DocumentRead[]
  total: number
}

export interface DocumentUploadResponse {
  document: DocumentRead
  message: string
}

export interface DocumentDownloadURL {
  url: string
  expires_in_seconds: number
  filename: string
}

// -----------------------------------------------------------
// Database metadata browser
// -----------------------------------------------------------
export interface TableColumn {
  name: string
  type: string
  nullable: boolean
}

export interface TableInfo {
  name: string
  columns: TableColumn[]
  row_count_estimate: number
}

// -----------------------------------------------------------
// Error envelope (matches backend's unified error format)
// -----------------------------------------------------------
export interface ApiErrorDetail {
  code: string
  message: string
  details?: unknown
}

export interface ApiErrorResponse {
  error: ApiErrorDetail
}

// -----------------------------------------------------------
// Health
// -----------------------------------------------------------
export interface ComponentStatus {
  status: 'ok' | 'fail' | 'degraded'
  detail?: string | null
  latency_ms?: number | null
}

export interface HealthResponse {
  status: 'ok' | 'fail' | 'degraded'
  version: string
  env: string
  components: Record<string, ComponentStatus>
}

// -----------------------------------------------------------
// RAG (Phase 1)
// -----------------------------------------------------------
export interface ChunkResult {
  chunk_id: string
  document_id: string
  content: string
  score: number
  rrf_score?: number | null
  chunk_index: number
  page_number: number
  char_start?: number | null
  char_end?: number | null
  chunk_type: string
}

export interface SearchResponse {
  query: string
  results: ChunkResult[]
  total_found: number
  latency_ms: number
}

export interface PlaygroundResponse {
  query: string
  results: ChunkResult[]
  debug: {
    query_embedded: boolean
    vector_results_count: number
    bm25_results_count: number
    fused_count: number
    reranked_count: number
    reranker_available: boolean
  }
  latency_ms: number
}

export interface Citation {
  index: number
  chunk_id: string
  document_id: string
  snippet: string
  page_number: number
  score: number
}
