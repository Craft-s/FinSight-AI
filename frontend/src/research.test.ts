import { describe, expect, it } from 'vitest'
import { documentStatusLabel, isResearchPending, reportFilename, researchRequestError, researchStatusLabel } from './research'
import { locale, setLocale, t } from './i18n'
import type { ResearchRequest } from './types'

const body:ResearchRequest={cik:'0000320193',task_type:'summary',filing_ids:['filing-one'],config_id:'verified-model',language:'zh-CN'}
describe('research request safeguards',()=>{
  it('labels document preparation separately from AI research in both languages',()=>{
    const previous=locale.value
    try{
      setLocale('zh-CN')
      expect(t(documentStatusLabel('succeeded'))).toBe('正文准备已完成')
      expect(t(researchStatusLabel('succeeded'))).toBe('研究已完成')
      setLocale('en-US')
      expect(t(documentStatusLabel('succeeded'))).toBe('Document preparation completed')
      expect(t(researchStatusLabel('succeeded'))).toBe('Research completed')
    }finally{setLocale(previous)}
  })
  it('requires two distinct filings for comparison and exactly one for other tasks',()=>{
    expect(researchRequestError({...body,task_type:'comparison',filing_ids:['same','same']})).toBeTruthy()
    expect(researchRequestError({...body,task_type:'comparison',filing_ids:['one','two']})).toBe('')
    expect(researchRequestError({...body,filing_ids:['one','two']})).toBeTruthy()
    expect(researchRequestError({...body,filing_ids:[]})).toBeTruthy()
  })
  it('requires a verified configuration selection and a nonblank question for Q&A',()=>{
    expect(researchRequestError({...body,config_id:''})).toBeTruthy()
    expect(researchRequestError({...body,task_type:'question',question:'   '})).toBeTruthy()
    expect(researchRequestError({...body,task_type:'question',question:'现金流风险是什么？'})).toBe('')
    expect(researchRequestError({...body,task_type:'question',question:'x'.repeat(501)})).toBeTruthy()
  })
  it('does not poll terminal states including cancellation',()=>{
    expect(isResearchPending('queued')).toBe(true)
    expect(isResearchPending('running')).toBe(true)
    for(const status of ['succeeded','failed','cancelled'])expect(isResearchPending(status)).toBe(false)
  })
  it('generates a safe filename without trusting report titles or path separators',()=>{
    expect(reportFilename('../0000320193','../report/<script>')).toBe('FinSight-0000320193-reportscript.md')
    expect(reportFilename('','')).toBe('FinSight-company-report.md')
  })
})
