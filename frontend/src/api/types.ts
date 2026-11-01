export type Role = "admin" | "member" | "viewer";
export type Access = "owner" | "edit" | "read";
export type VersionStatus = "queued" | "processing" | "ready" | "failed";
export type AnswerStatus = "answered" | "not_found" | "refused" | "error";

export interface User { id: string; email: string; full_name: string; role: Role; is_active: boolean; created_at: string }

export interface Stage { status: "running" | "done" | "failed"; ms?: number; error?: string; [k: string]: unknown }

export interface Version {
  id: string; version_no: number; filename: string; mime: string; size_bytes: number; page_count: number | null;
  language: string | null; status: VersionStatus; stage: string | null; stages: Record<string, Stage>;
  error: string | null; created_at: string; processed_at: string | null;
}

export interface DocumentSummary {
  id: string; title: string; owner_id: string; owner_email: string | null; access: Access;
  collection_id: string | null; doc_type: string | null; doc_type_source: string | null;
  created_at: string; updated_at: string; current_version: Version | null;
}

export interface DocumentDetail extends DocumentSummary {
  versions: Version[];
  summary: { text: string; source: string } | null;
  fields: { kind: string; data: Record<string, unknown>; source: string;
            validation: { valid: boolean; missing_fields: string[]; issues: string[] } } | null;
  entities: { type: string; value: string; normalized: string | null; page_no: number }[];
  shares: { user_id: string; email: string; permission: "read" | "edit" }[] | null;
}

export interface Page<T> { items: T[]; total: number }

export interface PageContent { page_no: number; page_count: number; text: string; ocr_used: boolean;
  ocr_confidence: number | null; tables: string[][][] }

export interface Collection { id: string; name: string; created_at: string; document_count: number }

export interface SearchHit { chunk_id: number; document_id: string; document_title: string; doc_type: string | null;
  page_no: number; char_start: number; char_end: number; text: string; score: number;
  vector_similarity: number | null; keyword_rank: number | null }

export interface Citation { ordinal: number; chunk_id?: number; document_id: string; document_title: string;
  page_no: number; cited_text: string; char_start?: number; char_end?: number }

export interface AskResult {
  message_id: string; status: AnswerStatus; answer: string; provider: string;
  sentences: { text: string; citations: number[]; supported: boolean }[];
  citations: Citation[]; standalone_question: string; retrieved: number; latency_ms: number; notes: string[];
}

export interface HistoryMessage { id: string; role: "user" | "assistant"; content: string; status: AnswerStatus | null;
  provider: string | null; created_at: string; citations: Citation[] }

export interface Conversation { id: string; title: string; scope: { document_ids?: string[] }; created_at: string }

export interface SystemInfo { llm_mode: "claude" | "extractive"; llm_model: string | null; embedding_model: string;
  documents_leave_server: boolean }
