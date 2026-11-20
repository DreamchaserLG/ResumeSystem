import { api } from './client'
import { expireSession, getSessionToken } from './session'
import type { Department, Job } from './hr'
import type { TrendPoint, DepartmentStats, RecruitmentJob } from './statistics'

export interface Company { id:number; name:string; parent_id:number|null }
export interface JobDirectory { items:Job[]; departments:Required<Department>[]; companies:Company[] }
export const jobDirectory = () => api.get<JobDirectory>('/jobs')
export interface DepartmentAccount { id:number; display_name:string; email:string; status:'active'|'disabled' }
export interface JobImportDraft {
  title:string; department_name:string; planned_headcount:number|null; recruitment_type:string|null
  recruiter_name:string; target_date:string|null; rounds:string[]; jd:string; warnings:string[]
  requirements?:string; required_skills?:string[]; experience_tags?:string[]; recruitment_start?:string|null; recruitment_end?:string|null
}
export type JobImportStatus =
  | {status:'pending'}
  | {status:'ready'; draft:JobImportDraft}
  | {status:'failed'; error:string}
export interface Followup {
  id:number; author_name:string; body:string; action:'note'|'interview'; scheduled_at:string|null
  attachment_name:string|null; created_at:string
}
export interface NotePage { items:Followup[]; next:number|null }
export const followupPath = (id:number) => '/workspace/applications/'+id+'/followups'

export interface MatchRow { id:number; version_id:number; name?:string|null; hits:string[]; missing:string[] }
export interface MatchReport {
  kind:'match'; job_id:number; job_title:string; skills:string[]; source:'llm'|'rules'; reviewed:number; matches:MatchRow[]
  summary?:string; summary_source?:'llm'|'rules'
}
export interface StatsReport {
  kind:'stats'; start:string; end:string; focus:string;
  metrics:{applications:number; hired:number; open:number; gap:number; unplanned:number; interviews:number|null}
  trend:TrendPoint[]; departments:DepartmentStats[]; jobs:RecruitmentJob[]
}
export interface DocAttachment { name:string; chars:number }
export interface DocReport {
  kind:'doc'; attachments:DocAttachment[]
  summary?:string; summary_source?:'llm'|'rules'
}
export type Report = MatchReport | StatsReport | DocReport
export type AnalysisEvent =
  | { event:'report'; report:Report; model_configured:boolean; model?:string|null }
  | { event:'thinking'; text:string }
  | { event:'delta'; text:string }
  | { event:'reset' }
  | { event:'done'; source:'llm'|'rules' }
  | { event:'context'; context:AnalysisContext }
export interface AnalysisContext {
  phase:'querying'|'generating'|'checking'|'repairing'|'done'
  turn_no:number; window_used:number; window_keep:number
  tokens_used:number; tokens_budget:number; summarized:boolean
  scope_echo:string; scope_source:'memory'|'request'
}
export interface AnalysisRequest {
  question:string; department_id?:number; job_id?:number; days:7|30|90; month_to_date?:boolean
  session_id?:string; attachment_ids?:string[]
}
export interface AnalysisAttachment { id:string; name:string; size:number; status:'pending'|'ready'|'failed'; chars?:number; error?:string }

export async function uploadAttachment(file:File):Promise<AnalysisAttachment> {
  const token=getSessionToken()
  const body=new FormData()
  body.append('file',file)
  const response=await fetch('/api/v1/workspace/analysis/attachments',{method:'POST',cache:'no-store',headers:{'X-Session-Token':token},body})
  if (response.status===401) {
    const detail=(await response.json().catch(()=>({}))).detail
    expireSession(token,detail)
    throw new Error('会话已过期，请重新登录。')
  }
  if (!response.ok) {
    const error=await response.json().catch(()=>({}))
    throw new Error(typeof error.detail==='string' ? error.detail : '附件上传失败，请稍后再试。')
  }
  return response.json() as Promise<AnalysisAttachment>
}

export async function pollAttachment(id:string):Promise<AnalysisAttachment> {
  const token=getSessionToken()
  const response=await fetch('/api/v1/workspace/analysis/attachments/'+id,{cache:'no-store',headers:{'X-Session-Token':token}})
  if (response.status===401) {
    const detail=(await response.json().catch(()=>({}))).detail
    expireSession(token,detail)
    throw new Error('会话已过期，请重新登录。')
  }
  if (!response.ok) {
    const error=await response.json().catch(()=>({}))
    throw new Error(typeof error.detail==='string' ? error.detail : '附件查询失败。')
  }
  return response.json() as Promise<AnalysisAttachment>
}

export async function runAnalysis(body:AnalysisRequest, onEvent:(event:AnalysisEvent)=>void, signal:AbortSignal) {
  const token=getSessionToken()
  const response = await fetch('/api/v1/workspace/analysis', {
    method:'POST', signal, cache:'no-store', headers:{'Content-Type':'application/json','Accept':'text/event-stream','X-Session-Token':token},
    body:JSON.stringify(body),
  })
  if (response.status===401) {
    const detail=(await response.json().catch(()=>({}))).detail
    expireSession(token,detail)
    throw new Error('会话已过期，请重新登录。')
  }
  if (!response.ok || !response.body) {
    const error = await response.json().catch(()=>({}))
    throw new Error(typeof error.detail==='string' ? error.detail : '查询失败，请检查输入后重试。')
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let pending = ''
  let doneEvent = false
  try {
    while (true) {
      const result = await reader.read()
      if (result.done) break
      pending += result.value
      let newline = pending.indexOf('\n')
      while (newline >= 0) {
        const row = pending.slice(0,newline)
        pending = pending.slice(newline+1)
        if (row && (row.startsWith('data: ') || !row.startsWith(':') && !row.startsWith('event:'))) {
          const event = JSON.parse(row.startsWith('data: ') ? row.slice(6) : row) as AnalysisEvent
          if(event.event==='done') doneEvent=true
          onEvent(event)
        }
        newline = pending.indexOf('\n')
      }
    }
    if (!doneEvent) throw new Error('分析连接已中断，请重新查询。')
  } finally { reader.releaseLock() }
}
