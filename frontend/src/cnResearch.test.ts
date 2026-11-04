import { describe, expect, it } from 'vitest'
import { CN_RESEARCH_TYPES, cnCitationPage, cnPdfCoverage, cnReportFilename, researchCompanyIdentity, researchCompanyRoute, safeCnPdfUrl } from './cnResearch'
import { safeSecUrl } from './utils'
import type { CnResearchJob, ResearchJob } from './types'

describe('CN research identity and PDF evidence boundaries',()=>{
  const source='https://static.cninfo.com.cn/finalpage/2025-01-01/123456.PDF'
  it('allows only original CNINFO PDF paths and validated page fragments',()=>{
    expect(safeCnPdfUrl(source,2)).toBe(source+'#page=2')
    expect(safeCnPdfUrl(source)).toBe(source)
    expect(safeCnPdfUrl(source.replace('.PDF','.pdf'),2)).toBe(source.replace('.PDF','.pdf')+'#page=2')
    for(const url of [source+'?token=test',source+'#page=2',source+'\n',source+'\r\n',source.replace('https:','http:'),source.replace('static.cninfo.com.cn','static.cninfo.com.cn.evil.test'),source.replace('/123456.PDF','/other.PDF'),source.replace('https://','https://user:password@'),'javascript:alert(1)'])expect(safeCnPdfUrl(url)).toBeNull()
    for(const page of [0,-1,1.5,10001,'2',null])expect(safeCnPdfUrl(source,page)).toBeNull()
    expect(safeSecUrl(source)).toBeNull()
    for(const url of [source.replace('2025-01-01','2025-02-30'),source.replace('/123456.PDF','/hidden/../123456.PDF'),source.replace('/123456.PDF','/12345.PDF'),source.replace('cninfo.com.cn/','cninfo.com.cn:443/')])expect(safeCnPdfUrl(url)).toBeNull()
  })
  it('preserves exchange-qualified symbols in routes and export filenames',()=>{
    const cn={market:'CN',symbol:'000001.SZ'} as CnResearchJob
    const us={cik:'0000000001'} as ResearchJob
    expect(researchCompanyRoute(cn)).toBe('/cn/company/000001.SZ')
    expect(researchCompanyIdentity(cn)).toBe('000001.SZ')
    expect(researchCompanyRoute(us)).toBe('/company/0000000001')
    expect(cnReportFilename('000001.SZ','job/../../one')).toBe('FinSight-CN-000001.SZ-jobone.md')
    expect(CN_RESEARCH_TYPES.map(item=>item.value)).toEqual(['summary','comparison','question'])
  })
  it('never invents a valid page from string or invalid source metadata',()=>{
    const citation={location:{page_start:3,page_end:3}}
    expect(cnCitationPage(citation)).toBe(3)
    expect(cnCitationPage({...citation,location:{page_start:'3'}})).toBeUndefined()
    expect(cnCitationPage({...citation,location:{page_start:0}})).toBeUndefined()
    expect(cnCitationPage({...citation,location:{page_start:3,page_end:2}})).toBeUndefined()
    expect(cnCitationPage({...citation,location:{page_start:3,page_end:'3'}})).toBeUndefined()
  })
  it('exposes partial extraction without estimating unknown page coverage',()=>{
    expect(cnPdfCoverage({page_count:4,coverage:{text_pages:3,empty_or_image_pages:[2],complete_text_extraction:false}})).toEqual({textPages:3,emptyPages:[2],partial:true})
    expect(cnPdfCoverage({page_count:4,coverage:{text_pages:'4',empty_or_image_pages:[0,'2',5]}})).toEqual({textPages:null,emptyPages:[],partial:false})
    expect(cnPdfCoverage({page_count:4})).toEqual({textPages:null,emptyPages:[],partial:false})
    expect(cnPdfCoverage({page_count:4,coverage:{text_pages:2}}).partial).toBe(true)
  })
})
