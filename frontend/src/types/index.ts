// ─── Authentication types ──────────────────────────────────────────
export interface LoginRequest {
  email: string
  password: string
}

export interface LoginResponse {
  access_token: string
  refresh_token: string
  token_type: 'bearer'
  expires_in: number
  user: User
}

export interface RefreshRequest {
  refresh_token: string
}

export interface User {
  id: string
  email: string
  full_name?: string
  status: string
  is_bootstrap: boolean
  permissions: string[]
  created_at: string
}

// ─── Bootstrap types ───────────────────────────────────────────────
export interface BootstrapTokenResponse {
  token: string
}

export interface BootstrapStatus {
  bootstrap_required: boolean
  admin_email?: string
}

export interface CreateAdminRequest {
  token: string
  email: string
  password: string
  full_name: string
}

// ─── Chat types ────────────────────────────────────────────────────
export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface ChatRequest {
  messages: ChatMessage[]
  model?: string
  provider?: string
  temperature?: number
  max_tokens?: number
}

export interface ChatResponse {
  response_type: string
  message: ChatMessage
  delta: string | null
  answer: {
    final_answer: string
    completion_status: string
    evidence: unknown[]
    assumptions: unknown[]
    truncation_info: unknown | null
    processing_location: string
    model_identity: string
    as_of_time: string
    execution_timestamp: string
  }
  finish_reason: string
}

// ─── Conversation types ────────────────────────────────────────────
export interface Conversation {
  id: string
  title: string
  created_at: string
  updated_at: string
  message_count: number
}

export interface ConversationMessage {
  role: string
  content: string
  created_at: string
}

// ─── Document types ────────────────────────────────────────────────
export interface Document {
  id: string
  filename: string
  file_size: number
  mime_type: string
  status: string
  content_hash: string
  extraction_method: string
  embedding_model: string
  is_indexed: boolean
  chunk_count: number
  created_at: string
  updated_at: string
}

export interface RetrievalResult {
  chunk_id: string
  document_id: string
  content: string
  relevance_score: number
  mmr_score: number
  page_ref: number | null
  section_ref: number | null
  row_ref: number | null
  citation: {
    source_type: string
    source_id: string
    title: string
  }
}

export interface RetrievalResponse {
  query: string
  top_k: number
  lambda_param: number
  candidate_count: number
  results: RetrievalResult[]
}

export interface RetrieveRequest {
  query: string
  top_k?: number
  fetch_k?: number
  lambda_param?: number
}

// ─── Job types ─────────────────────────────────────────────────────
export type JobStatusType = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface Job {
  id: string
  job_type: string
  status: JobStatusType
  payload: Record<string, unknown> | null
  result: Record<string, unknown> | null
  priority: number
  attempts: number
  max_retries: number
  error_message: string | null
  created_at: string
  started_at: string | null
  completed_at: string | null
  expires_at: string | null
  worker_id: string | null
}

export interface CreateJobRequest {
  job_type: 'document_ingest' | 'report_generation'
  payload?: Record<string, unknown>
  priority?: number
}

// ─── Health ────────────────────────────────────────────────────────
export interface HealthResponse {
  status: string
  database: string
  version: string
  timestamp: string
}
