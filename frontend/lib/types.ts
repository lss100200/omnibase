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
// User profile and personal model providers
// -----------------------------------------------------------
export interface UserProfileRead {
  user_id: string
  display_name: string
  locale: string
  theme: 'system' | 'light' | 'dark'
  assistant_name: string
  assistant_tone: 'concise' | 'balanced' | 'detailed'
  assistant_instructions: string
  version: number
  created_at: string | null
  updated_at: string | null
}

export interface ProviderCredentialRead {
  id: string
  display_name: string
  provider_id: string
  base_url: string
  model_id: string
  key_fingerprint: string | null
  secret_configured: boolean
  is_default: boolean
  is_active: boolean
  version: number
  last_test_status:
    | 'passed'
    | 'auth_failed'
    | 'timeout'
    | 'identity_mismatch'
    | 'unreachable'
    | 'failed'
    | null
  last_test_latency_ms: number | null
  last_tested_at: string | null
  revoked_at: string | null
  created_at: string
  updated_at: string
}

export interface ProviderCredentialList {
  items: ProviderCredentialRead[]
  total: number
  operator_fallback_available: boolean
}

export interface ProviderTestResult {
  status: NonNullable<ProviderCredentialRead['last_test_status']>
  latency_ms: number | null
  requested_model_id: string
  actual_model_id: string | null
}

export interface ProviderRuntimePosture {
  credential_source: 'personal' | 'operator_default' | 'unavailable'
  provider_id: string | null
  model_id: string | null
  credential_id: string | null
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

// -----------------------------------------------------------
// Workspaces / AI spaces (P34.4 control-plane browser surface)
// -----------------------------------------------------------
export type WorkspaceDesiredState = 'stopped' | 'running' | 'paused' | 'archived'

export interface WorkspaceRead {
  id: string
  template_id: string
  owner_user_id: string
  parent_workspace_id: string | null
  restored_from_snapshot_id: string | null
  display_name: string
  desired_state: WorkspaceDesiredState
  observed_state: string
  generation: number
  version: number
  quota: Record<string, unknown>
  last_error_code: string | null
  archived_at: string | null
  created_at: string
  updated_at: string
}

export interface WorkspaceList {
  items: WorkspaceRead[]
  total: number
}

export interface WorkspaceTemplateRead {
  id: string
  template_key: string
  version: number
  display_name: string
  digest: string
  template_spec: Record<string, unknown>
  state: string
  created_at: string
}

export interface WorkspaceTemplateList {
  items: WorkspaceTemplateRead[]
  total: number
}

export interface WorkspaceMembershipRead {
  id: string
  workspace_id: string
  user_id: string
  role: 'viewer' | 'member' | 'operator' | 'maintainer' | 'owner'
  state: string
  version: number
  created_at: string
  updated_at: string
}

export interface WorkspaceMembershipList {
  items: WorkspaceMembershipRead[]
  total: number
}

export interface WorkspaceRunRead {
  id: string
  workspace_id: string
  kind: 'batch' | 'interactive'
  generation: number
  desired_state: string
  observed_state: string
  version: number
  request_digest: string
  last_result_digest: string | null
  last_error_code: string | null
  created_at: string
  updated_at: string
}

export interface WorkspaceRunList {
  items: WorkspaceRunRead[]
  total: number
}

export interface WorkspaceSnapshotRead {
  id: string
  workspace_id: string
  source_generation: number
  manifest_digest: string
  snapshot_metadata: Record<string, unknown>
  state: string
  created_at: string
}
