import type { CnEvidenceCitation, CnFiling, CnFilingDocument, CnResearchJob, CnResearchReport, CnResearchTaskType, ResearchJob, ResearchReport } from './types'

export const CN_RESEARCH_TYPES: {value:CnResearchTaskType;label:string}[] = [{value:'summary',label:'单份财报总结'},{value:'comparison',label:'两期财报对比'},{value:'question',label:'公司财报问答'}]
export const cnFilingLabel = (filing:CnFiling) => `${filing.title} · ${filing.report_date || filing.filing_date}`
export function cnFilingTypeLabel(type:string) { return ({annual:'年报',semiannual:'半年报',q1:'一季报',q3:'三季报',announcement:'其他财报公告'} as Record<string,string>)[type] || type }
export function safeCnPdfUrl(value:unknown,page?:unknown):string|null {
  if(typeof value!=='string')return null
  // Match the unmodified URL before parsing; URL() normalizes dot segments.
  const match=/^https:\/\/static\.cninfo\.com\.cn\/finalpage\/(\d{4}-\d{2}-\d{2})\/\d{6,30}\.[Pp][Dd][Ff]$/.exec(value)
  if(!match||match[0]!==value)return null
  const date=new Date(`${match[1]}T00:00:00Z`)
  if(Number.isNaN(date.valueOf())||date.toISOString().slice(0,10)!==match[1])return null
  if(page!==undefined&&(typeof page!=='number'||!Number.isInteger(page)||page<1||page>10000))return null
  return value+(page===undefined?'':`#page=${page}`)
}
export function cnCitationPage(citation:Pick<CnEvidenceCitation,'location'>):number|undefined {
  const location=citation.location
  if(!location||typeof location!=='object')return undefined
  const page=location.page_start,end=location.page_end
  return typeof page==='number'&&Number.isInteger(page)&&page>0&&page<=10000
    &&typeof end==='number'&&Number.isInteger(end)&&end>=page&&end<=10000?page:undefined
}
export function cnPdfCoverage(document:Pick<CnFilingDocument,'page_count'|'coverage'>) {
  const coverage=document.coverage||{}
  const count=coverage.text_pages
  const textPages=typeof count==='number'&&Number.isInteger(count)&&count>=0&&count<=document.page_count?count:null
  const emptyPages=Array.isArray(coverage.empty_or_image_pages)?coverage.empty_or_image_pages.filter((page):page is number=>typeof page==='number'&&Number.isInteger(page)&&page>0&&page<=document.page_count):[]
  return {textPages,emptyPages,partial:coverage.complete_text_extraction===false||emptyPages.length>0||textPages!==null&&textPages<document.page_count}
}
export function researchCompanyIdentity(record:ResearchJob|ResearchReport|CnResearchJob|CnResearchReport):string {return 'symbol' in record?record.symbol:record.cik}
export function researchCompanyRoute(record:ResearchJob|ResearchReport|CnResearchJob|CnResearchReport):string {return 'symbol' in record?`/cn/company/${encodeURIComponent(record.symbol)}`:`/company/${encodeURIComponent(record.cik)}`}
export function cnReportFilename(symbol:string,id:string):string {return `FinSight-CN-${symbol.replace(/[^A-Z0-9.]/g,'')||'company'}-${id.replace(/[^a-zA-Z0-9-]/g,'').slice(0,36)||'report'}.md`}
