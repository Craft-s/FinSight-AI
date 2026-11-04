import { beforeEach, describe, expect, it, vi } from 'vitest'

const transport = vi.hoisted(() => ({post: vi.fn(), get: vi.fn()}))
vi.mock('axios', () => ({default: {create: () => transport, get: vi.fn(), isAxiosError: (error: unknown) => typeof error === 'object' && error !== null && 'isAxiosError' in error}}))
import { api, errorMessage } from './api'

beforeEach(() => { transport.post.mockReset(); transport.get.mockReset(); transport.post.mockResolvedValue({data: {}}) })
describe('authorized model actions', () => {
  it('binds test, activation, and deletion to the displayed version and escapes identifiers', async () => {
    await api.testLLMConfig('config/1', 3, 'session-secret')
    await api.activateLLMConfig('config/1', 3, 'session-secret')
    await api.deleteLLMConfig('config/1', 3, 'session-secret')
    for (const [index, action] of ['test', 'activate', 'delete'].entries()) {
      const [url, body, options] = transport.post.mock.calls[index]
      expect(url).toBe(`/llm/configs/config%2F1/${action}`)
      expect(body).toEqual({version: 3})
      expect(options.headers).toEqual({'X-Admin-Token': 'session-secret'})
      expect(body).not.toHaveProperty('token')
    }
  })
  it('uses same-origin overview endpoints and passes the chosen financial basis', async () => {
    const body = {config_id: 'config-1', frequency: 'annual', selection_policy: 'as_of', as_of: '2022-12-31', language: 'en-US'}
    await api.createOverview('0000320193', body, 'session-secret')
    expect(transport.post).toHaveBeenCalledWith('/companies/0000320193/overview', body, {headers: {'X-Admin-Token': 'session-secret'}})
  })
  it('does not expose a raw provider authentication response', () => {
    const response = {isAxiosError: true, response: {status: 401, data: {error: {code: 'AUTH_INVALID', message: 'provider echoed confidential-key'}}}}
    expect(errorMessage(response)).toBe('模型凭据无效或无权访问，请检查 API Key。')
    expect(errorMessage(response)).not.toContain('confidential-key')
  })
})

describe('private research and public evidence transport',()=>{
  it('downloads an authenticated blob without placing credentials in the URL',async()=>{
    transport.get.mockResolvedValue({data:'markdown-fixture'})
    await api.downloadReport('report/one','session-secret')
    expect(transport.get).toHaveBeenCalledWith('/research/reports/report%2Fone/download',{headers:{'X-Admin-Token':'session-secret'},params:{format:'md'},responseType:'blob'})
    expect(String(transport.get.mock.calls[0][0])).not.toContain('session-secret')
  })
  it('preserves the chosen filings, language and task type without an owner override',async()=>{
    const body={cik:'0000320193',task_type:'comparison' as const,filing_ids:['older','newer'],config_id:'verified-model',language:'en-US'}
    await api.createResearch(body,'session-secret')
    expect(transport.post).toHaveBeenCalledWith('/research/jobs',body,{headers:{'X-Admin-Token':'session-secret'}})
    expect(transport.post.mock.calls[0][1]).not.toHaveProperty('owner_id')
  })
  it('keeps document search public and limits it to the selected filing',async()=>{
    transport.get.mockResolvedValue({data:{items:[]}})
    await api.evidence('0000320193','cash flow','filing/one')
    expect(transport.get).toHaveBeenCalledWith('/companies/0000320193/evidence',{params:{q:'cash flow',filing_id:'filing/one'}})
    expect(transport.get.mock.calls[0][1]).not.toHaveProperty('headers')
  })
  it('authorizes cancellation and retry without embedding a token in task data',async()=>{
    await api.cancelResearch('job/one','session-secret')
    await api.retryResearch('job/one','session-secret')
    expect(transport.post.mock.calls.map(call=>call[0])).toEqual(['/research/jobs/job%2Fone/cancel','/research/jobs/job%2Fone/retry'])
    for(const call of transport.post.mock.calls){expect(call[1]).toEqual({});expect(call[2].headers).toEqual({'X-Admin-Token':'session-secret'})}
  })
})

describe('A-share isolated transport', () => {
  it('searches the chosen market and does not manufacture a CIK for a Chinese security', async () => {
    transport.get.mockResolvedValue({data:{items:[{symbol:'600519.SH',code:'600519',name:'TEST ONLY',market:'CN',exchange:'SH',currency:'CNY',source_kind:'synthetic_fixture'}]}})
    const response=await api.search('600519',undefined,'CN')
    expect(transport.get).toHaveBeenCalledWith('/cn/companies/search',{params:{q:'600519'},signal:undefined})
    expect(response.items[0]).toMatchObject({cik:'',ticker:'600519',symbol:'600519.SH',market:'CN'})
    transport.get.mockClear()
    transport.get.mockResolvedValue({data:{items:[]}})
    await api.search('AAPL')
    expect(transport.get).toHaveBeenCalledWith('/companies/search',{params:{q:'AAPL',market:'US'},signal:undefined})
  })
  it('keeps cumulative financials on the CN namespace without a misleading quarterly selection policy', async () => {
    transport.get.mockResolvedValue({data:{items:[]}})
    await api.cnMetrics('600519.SH','cumulative')
    expect(transport.get).toHaveBeenCalledWith('/cn/companies/600519.SH/metrics',{params:{frequency:'cumulative',years:5}})
  })
  it('authorizes CN directory/company sync and job polling in headers only', async () => {
    transport.get.mockResolvedValue({data:{}})
    await api.cnDirectorySync('session-secret')
    await api.cnSync('600519.SH','session-secret')
    await api.cnJob('job/one','session-secret')
    expect(transport.post.mock.calls.map(call=>call[0])).toEqual(['/cn/directory/sync','/cn/companies/600519.SH/sync'])
    for(const call of transport.post.mock.calls){expect(call[1]).toEqual({});expect(call[2].headers).toEqual({'X-Admin-Token':'session-secret'})}
    expect(transport.get).toHaveBeenCalledWith('/cn/sync/jobs/job%2Fone',{headers:{'X-Admin-Token':'session-secret'}})
  })
})
