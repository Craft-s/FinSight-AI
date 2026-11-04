export type SourceKind = 'sec_live' | 'sec_snapshot' | 'synthetic_fixture' | string
export interface Company { cik: string; ticker: string; name: string; market: string; symbol?: string; exchange?: string | null; source_kind?: SourceKind; fiscal_year_end?: string | null; last_synced_at?: string | null; is_stale?: boolean }
export interface CnCompany { symbol: string; code: string; name: string; market: 'CN'; exchange: 'SH'|'SZ'|'BJ'; currency: 'CNY'; source_kind: SourceKind; last_synced_at?: string | null; is_stale?: boolean }
export interface CnStatus { source_kind: SourceKind; directory_count: number; directory_updated_at: string | null; limitations: string[] }
export interface CnMetrics extends MetricsResponse { frequency: 'annual'|'cumulative'; limitations: string[] }
export interface CnFiling { id: string; title: string; filing_date: string; report_date?: string | null; form_type: string; source_url: string }
export interface CnSource { id: string; kind: string; source_kind: SourceKind; source_url: string; sha256: string; fetched_at: string; parser_version: string; source_metadata: Record<string,unknown> }
export interface Metric { metric_key: string; label: string; period_type: string; fiscal_year: number | null; fiscal_quarter?: number | null; period_start: string | null; period_end: string; value: string | null; unit: string; calc_method: string; provenance: Record<string, unknown>; missing_reason: string | null; yoy?: string | null; yoy_reason?: string | null }
export interface MetricsResponse { items: Metric[]; source_kind: SourceKind; selection_policy: string; currency: string }
export interface Filing { id: string; accession_number: string; form_type: string; filing_date: string; report_date?: string | null; source_url: string; amended: boolean; items?: string | string[] | null }
export interface Page<T> { items: T[]; total: number; page: number; page_size: number }
export interface SyncJob { id?: string; job_id?: string; status: string; error_code?: string | null; error_message?: string | null; message?: string; created_at?: string }
export interface Health { status?: string; source_kind?: SourceKind; data_mode?: SourceKind; database?: string; redis?: string }

export type LLMProvider = 'openai' | 'deepseek' | 'gemini' | 'ollama'
export interface LLMConfig { id: string; provider: LLMProvider; model_id: string; has_api_key: boolean; version: number; verified_version: number | null; is_active: boolean; last_tested_at: string | null; options: Record<string, number | string>; is_verified: boolean; created_at: string; updated_at: string }
export interface LLMStatus { available: boolean; encryption_ready: boolean; ollama_configured: boolean; providers: { id: LLMProvider; label: string; mode: string; configured: boolean; options: string[] }[] }
export interface LLMModel { id: string; display_name: string; provider: LLMProvider; supports_text_generation?: boolean | null; context_window?: number | null }
export interface LLMModelsResponse { items: LLMModel[]; cached?: boolean }
export interface LLMConfigInput { id?: string; version?: number; provider: LLMProvider; model_id: string; api_key?: string; options: Record<string, number | string> }
export interface LLMTestResult { config: LLMConfig; duration_ms: number; usage: Record<string, unknown> }
export interface OverviewRequest { config_id: string; frequency: string; selection_policy: string; as_of?: string; accession?: string; language: string }
export interface OverviewFact { id: string; metric_key: string; label: string; value: string | null; unit: string; period: string; provenance: Record<string, unknown> }
export interface OverviewResult { title: string; summary: string; observations: { text: string; metric_refs: string[] }[]; facts: OverviewFact[]; limitations: string[]; disclaimer: string; provider: string; model_id: string; config_version: number; usage: Record<string, unknown>; duration_ms: number; source_kind: string; language: string }
export interface OverviewJob { id?: string; job_id?: string; cik?: string; status: string; reused?: boolean; error_code?: string | null; error_message?: string | null; created_at?: string; started_at?: string | null; finished_at?: string | null; result?: OverviewResult | null }

export type ResearchTaskType = 'summary' | 'comparison' | 'event' | 'question'
export type ResearchStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
export interface FilingDocument { id: string; filing_id: string; title: string; source_url: string; sha256: string; source_kind: string; fetched_at: string; parser_version: string; role?: 'primary'|'exhibit'; source_metadata?:Record<string,unknown> }
export interface EvidenceCitation { citation_id: string; filing_id: string; accession: string; form_type: string; filing_date: string; section: string; source_url: string; excerpt: string; chunk_id: string; document_sha256: string; location: string | Record<string, unknown> }
export interface ResearchJob { id: string; job_id: string; cik: string; task_type: ResearchTaskType; status: ResearchStatus; error_code: string | null; error_message: string | null; report_id: string | null; created_at: string; started_at?: string | null; finished_at: string | null }
export interface JobAccepted { job_id: string; status: string; reused: boolean }
export interface DocumentJob { id: string; job_id: string; status: string; error_code: string | null; error_message: string | null; created_at: string; finished_at: string | null }
export interface ResearchRequest { cik: string; task_type: ResearchTaskType; filing_ids: string[]; question?: string; config_id: string; language: string; force?: boolean }
export interface ResearchClaim { text: string; kind: 'disclosed_fact' | 'inference'; citation_ids: string[]; metric_refs: string[]; reported_values: {metric_ref: string; value: string; unit: string}[] }
export interface ResearchReport { id: string; job_id: string; cik: string; task_type: ResearchTaskType; title: string; language: string; created_at: string; provider: string; model_id: string; source_kind: string; content: {claims: ResearchClaim[]; citations: EvidenceCitation[]; facts: OverviewFact[]; uncertainties: string[]; insufficient_evidence: boolean; validation: Record<string, unknown>; disclaimer?: string; limitations?:string[]; retrieval_mode?:string; filings?:{id:string;accession:string;form_type:string;filing_date:string;report_date?:string|null}[]}; usage: Record<string, unknown>; duration_ms: number }
