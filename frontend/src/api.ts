import axios from 'axios'
import type { Company, Filing, Health, LLMConfig, LLMConfigInput, LLMModelsResponse, LLMStatus, LLMTestResult, MetricsResponse, OverviewJob, OverviewRequest, Page, SyncJob, FilingDocument, EvidenceCitation, DocumentJob, JobAccepted, ResearchRequest, ResearchJob, ResearchReport } from './types'
import type { CnCompany, CnFiling, CnMetrics, CnSource, CnStatus } from './types'
import { cnSearchCompany, type ResearchMarket } from './market'

const client = axios.create({ baseURL: '/api/v1', timeout: 25000 })
const authorized = (token: string) => ({ headers: { 'X-Admin-Token': token } })
export const api = {
  health: async () => (await axios.get<Health>('/health', { timeout: 6000 })).data,
  search: async (q: string, signal?: AbortSignal, market: ResearchMarket = 'US'): Promise<{items: Company[]}> => {
    if (market === 'CN') {
      const result = (await client.get<{items: CnCompany[]}>('/cn/companies/search', { params: { q }, signal })).data
      return { items: result.items.map(cnSearchCompany) }
    }
    return (await client.get<{ items: Company[] }>('/companies/search', { params: { q, market: 'US' }, signal })).data
  },
  cnStatus: async () => (await client.get<CnStatus>('/cn/status')).data,
  cnCompany: async (symbol: string) => (await client.get<CnCompany>(`/cn/companies/${encodeURIComponent(symbol)}`)).data,
  cnMetrics: async (symbol: string, frequency: 'annual'|'cumulative') => (await client.get<CnMetrics>(`/cn/companies/${encodeURIComponent(symbol)}/metrics`, {params: {frequency,years:5}})).data,
  cnFilings: async (symbol: string, page = 1) => (await client.get<Page<CnFiling> & {source_kind: string; limitations: string[]}>(`/cn/companies/${encodeURIComponent(symbol)}/filings`, {params: {page,page_size:10}})).data,
  cnSync: async (symbol: string, token: string) => (await client.post<JobAccepted>(`/cn/companies/${encodeURIComponent(symbol)}/sync`, {}, authorized(token))).data,
  cnDirectorySync: async (token: string) => (await client.post<JobAccepted>('/cn/directory/sync', {}, authorized(token))).data,
  cnJob: async (id: string, token: string) => (await client.get<SyncJob>(`/cn/sync/jobs/${encodeURIComponent(id)}`, authorized(token))).data,
  cnSource: async (id: string) => (await client.get<CnSource>(`/cn/sources/${encodeURIComponent(id)}`)).data,
  company: async (cik: string) => (await client.get<Company>(`/companies/${encodeURIComponent(cik)}`)).data,
  metrics: async (cik: string, options: { frequency?: string; selection_policy?: string; as_of?: string; accession?: string } = {}) => (await client.get<MetricsResponse>(`/companies/${encodeURIComponent(cik)}/metrics`, { params: { frequency: 'annual', years: 5, ...options } })).data,
  filings: async (cik: string, type: string, page: number) => (await client.get<Page<Filing>>(`/companies/${encodeURIComponent(cik)}/filings`, { params: { type: type || undefined, page, page_size: 10 } })).data,
  events: async (cik: string, page: number) => (await client.get<Page<Filing>>(`/companies/${encodeURIComponent(cik)}/events`, { params: { page, page_size: 10 } })).data,
  sync: async (cik: string, token: string) => (await client.post<SyncJob>(`/companies/${encodeURIComponent(cik)}/sync`, {}, { headers: { 'X-Admin-Token': token } })).data,
  job: async (id: string) => (await client.get<SyncJob>(`/sync/jobs/${encodeURIComponent(id)}`)).data,
  llmStatus: async () => (await client.get<LLMStatus>('/llm/status')).data,
  llmConfigs: async (token: string) => (await client.get<{items: LLMConfig[]}>('/llm/configs', authorized(token))).data,
  saveLLMConfig: async (body: LLMConfigInput, token: string) => (await client.post<LLMConfig>('/llm/configs', body, authorized(token))).data,
  llmModels: async (id: string, token: string, refresh = false) => (await client.post<LLMModelsResponse>(`/llm/configs/${encodeURIComponent(id)}/models`, { refresh }, {...authorized(token), timeout: 70000})).data,
  testLLMConfig: async (id: string, version: number, token: string) => (await client.post<LLMTestResult>(`/llm/configs/${encodeURIComponent(id)}/test`, {version}, {...authorized(token), timeout: 150000})).data,
  activateLLMConfig: async (id: string, version: number, token: string) => (await client.post<LLMConfig>(`/llm/configs/${encodeURIComponent(id)}/activate`, {version}, authorized(token))).data,
  deleteLLMConfig: async (id: string, version: number, token: string) => (await client.post(`/llm/configs/${encodeURIComponent(id)}/delete`, {version}, authorized(token))).data,
  createOverview: async (cik: string, body: OverviewRequest, token: string) => (await client.post<OverviewJob>(`/companies/${encodeURIComponent(cik)}/overview`, body, authorized(token))).data,
  overviewJob: async (id: string, token: string) => (await client.get<OverviewJob>(`/overview/jobs/${encodeURIComponent(id)}`, authorized(token))).data,
  documents: async (filingId: string) => (await client.get<{items: FilingDocument[]; retrieval_mode: string}>(`/filings/${encodeURIComponent(filingId)}/documents`)).data,
  syncDocuments: async (filingId: string, token: string) => (await client.post<JobAccepted>(`/filings/${encodeURIComponent(filingId)}/documents/sync`, {}, authorized(token))).data,
  documentJob: async (id: string, token: string) => (await client.get<DocumentJob>(`/documents/jobs/${encodeURIComponent(id)}`, authorized(token))).data,
  evidence: async (cik: string, q: string, filingId?: string) => (await client.get<{items: EvidenceCitation[]; retrieval_mode: string; scope_note?:string}>(`/companies/${encodeURIComponent(cik)}/evidence`, {params:{q, ...(filingId ? {filing_id: filingId} : {})}})).data,
  createResearch: async (body: ResearchRequest, token: string) => (await client.post<JobAccepted>('/research/jobs', body, authorized(token))).data,
  researchJob: async (id: string, token: string) => (await client.get<ResearchJob>(`/research/jobs/${encodeURIComponent(id)}`, authorized(token))).data,
  retryResearch: async (id: string, token: string) => (await client.post<JobAccepted>(`/research/jobs/${encodeURIComponent(id)}/retry`, {}, authorized(token))).data,
  cancelResearch: async (id: string, token: string) => (await client.post<ResearchJob>(`/research/jobs/${encodeURIComponent(id)}/cancel`, {}, authorized(token))).data,
  researchHistory: async (token: string, page = 1, pageSize = 20) => (await client.get<Page<ResearchJob>>('/research/history', {...authorized(token),params:{page,page_size:pageSize}})).data,
  researchReport: async (id: string, token: string) => (await client.get<ResearchReport>(`/research/reports/${encodeURIComponent(id)}`, authorized(token))).data,
  downloadReport: async (id: string, token: string) => (await client.get<Blob>(`/research/reports/${encodeURIComponent(id)}/download`, {...authorized(token),params:{format:'md'},responseType:'blob'})).data,
}
export function errorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const body = error.response?.data
    const modelErrors: Record<string, string> = {
      AUTH_INVALID: '模型凭据无效或无权访问，请检查 API Key。', MODEL_NOT_FOUND: '未找到指定模型，请检查精确模型 ID。', MODEL_NOT_SUPPORTED: '指定模型不支持当前文本调用或参数。', RATE_LIMITED: '模型服务限流或配额不足，请稍后重试。', PROVIDER_UNAVAILABLE: '模型服务暂时不可用，请检查网络或服务状态。', TIMEOUT: '模型调用超时，请稍后重试。', INVALID_RESPONSE: '模型响应无效，未保存未经验证的结论。', CONFIG_VERSION_CONFLICT: '配置已更新，请刷新配置后重新操作。',
    }
    if (body?.error?.code && modelErrors[String(body.error.code)]) return modelErrors[String(body.error.code)]
    if (body?.error?.message) return String(body.error.message)
    if (error.code === 'ECONNABORTED') return '请求超时，请稍后重试。'
    if (!error.response) return '暂时无法连接数据服务，请确认后端已启动。'
    return `请求失败（HTTP ${error.response.status}），请稍后重试。`
  }
  return error instanceof Error ? error.message : '发生未知错误，请重试。'
}
