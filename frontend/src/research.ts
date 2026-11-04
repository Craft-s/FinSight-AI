import type { ResearchRequest, ResearchStatus, ResearchTaskType } from './types'

export const RESEARCH_TYPES: {value: ResearchTaskType; label: string}[] = [
  {value:'summary',label:'单份财报总结'}, {value:'comparison',label:'两期财报对比'},
  {value:'event',label:'事件解读'}, {value:'question',label:'公司财报问答'},
]
export function researchTypeLabel(value: string) { return RESEARCH_TYPES.find(item => item.value === value)?.label || value }
export function researchStatusLabel(value: string) { return ({queued:'排队中',running:'研究处理中',succeeded:'研究已完成',failed:'研究失败',cancelled:'已取消'} as Record<string,string>)[value] || value }
export function documentStatusLabel(value: string) { return ({queued:'正文任务排队中',running:'正在准备正文',succeeded:'正文准备已完成',failed:'正文准备失败',cancelled:'正文任务已取消'} as Record<string,string>)[value] || value }
export function isResearchPending(status: ResearchStatus | string) { return status === 'queued' || status === 'running' }
export function researchRequestError(body: Pick<ResearchRequest,'task_type'|'filing_ids'|'question'|'config_id'>): string {
  if (!body.config_id) return '请选择已验证的模型配置。'
  const unique = new Set(body.filing_ids.filter(Boolean))
  if (unique.size !== body.filing_ids.length || unique.size !== (body.task_type === 'comparison' ? 2 : 1)) return body.task_type === 'comparison' ? '请选择两份不同的定期财报。' : '请选择一份申报。'
  if (body.task_type === 'question' && !body.question?.trim()) return '请输入要研究的问题。'
  if (body.question && body.question.trim().length > 500) return '研究问题不能超过500个字符。'
  return ''
}
export function reportFilename(cik: string, id: string) { return `FinSight-${cik.replace(/[^0-9]/g,'') || 'company'}-${id.replace(/[^a-zA-Z0-9-]/g,'').slice(0,36) || 'report'}.md` }
