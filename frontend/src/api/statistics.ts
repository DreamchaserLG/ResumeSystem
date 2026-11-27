import { api } from './client'
import { expireSession, getSessionToken } from './session'

export interface RecruitmentTotals {
  jobs: number; open_jobs: number; paused_jobs: number; closed_jobs: number
  planned: number; filled: number; gap: number; unfilled_jobs: number; unplanned: number; overdue: number
  applications: number; people: number; resumes: number; hired: number; hired_applications: number
  review: number; progress: number; rejected: number; verify: number; failed: number; stalled: number
  new_applications: number; period_hired: number
}
export interface JobOption { id: number; title: string; department_id: number }
export interface RecruitmentJob extends JobOption {
  department_name: string; status: string; planned_headcount: number | null; target_date: string | null
  period_hired: number; new_applications: number; gap: number | null; filled: number | null; overdue: boolean; hired: number; applications: number
  review: number; progress: number; rejected: number; verify: number; failed: number; stalled: number
}
export interface DepartmentStats {
  id: number; name: string; status: string; admins?: number; open_jobs: number; planned: number
  period_hired: number; new_applications: number; filled: number; gap: number; unplanned: number; hired: number; applications: number
}
export interface TrendPoint { bucket: string; start_date: string; end_date: string; applications: number; hired: number }
export interface Statistics {
  scope: 'super_admin' | 'dept_hr'
  period: { start: string; end: string; unit: 'day' | 'week' | 'month'; timezone: string }
  totals: RecruitmentTotals
  departments: { id: number; name: string }[]
  accounts: { admins: number; super_admins: number; disabled: number; uncovered: number } | null
  department_rows: DepartmentStats[]
  job_options: JobOption[]
  jobs: RecruitmentJob[]
  trend: TrendPoint[]
  rounds: { id: number; job_id: number; title: string; name: string; position: number; total: number }[]
}
export const fetchStatistics = (params: URLSearchParams) => api.get<Statistics>('/statistics?' + params)

export type BoardPeriod = 'week'|'month'|'year'
export type BoardJobStatus = 'open'|'paused'|'closed'
export type BoardStatus = 'unassigned'|'uncontacted'|'interviewing'|'pending_onboard'|'hired'|'rejected'|'blacklist'|'custom'
export interface BoardActivity { applications:number; interviews:number; offers:number }
export interface BoardTrend extends BoardActivity {start:string;end:string}
export interface RecruitmentDashboard {
  revision:string
  period:{kind:BoardPeriod;start:string;end:string;unit:'day'|'week'|'month';timezone:string}
  scope:{department_id:number|null;job_id:number|null;status:BoardStatus|null;job_status:BoardJobStatus|null;adjustments:('job_unavailable'|'job_department_changed'|'department_unavailable')[]}
  options:{departments:{id:number;name:string}[];jobs:JobOption[];talent_statuses:{code:BoardStatus;label:string;color:string}[]}
  totals:BoardActivity & {open_jobs:number;planned:number|null;gap:number|null;period_onboarded:number|null;talent_pool:number;onboarded_people:number}
  stages:Record<string,number>
  trend:BoardTrend[]
  funnel:{applicants:number;talent_pool:number;interviewed:number;offered:number;onboarded:number}
  selection:{people:number;statuses:Partial<Record<BoardStatus,number>>;
    jobs:{id:number|null;title:string;department:string|null;people:number}[]}
  departments:(BoardActivity & {id:number|null;name:string})[]
  jobs:(BoardActivity & JobOption & {
    department:string;status:string;planned_headcount:number|null;onboarded:number;gap:number|null
  })[]
}
export const fetchRecruitmentDashboard = (params:URLSearchParams, signal?:AbortSignal) => api.get<RecruitmentDashboard>('/statistics/board?' + params, signal)

export async function watchRecruitmentBoard(onRevision:(revision:bigint)=>void,signal:AbortSignal) {
  const token = getSessionToken()
  const response = await fetch('/api/v1/statistics/board/updates',{
    signal,cache:'no-store',headers:{'X-Session-Token':token,'Accept':'text/event-stream'},
  })
  if (response.status===401) {
    expireSession(token,(await response.json().catch(()=>({}))).detail)
    return
  }
  if (response.status===403) return
  if (!response.ok || !response.body || !response.headers.get('Content-Type')?.startsWith('text/event-stream')) {
    throw new Error('招聘数据更新连接不可用。')
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let pending = ''
  try {
    while (true) {
      const item = await reader.read()
      if (item.done) break
      pending += item.value
      const lines = pending.split('\n')
      pending = lines.pop() ?? ''
      for (const line of lines) {
        const revision = /^data: ([0-9]+)$/.exec(line)
        if (revision) onRevision(BigInt(revision[1]))
      }
    }
  } finally { reader.releaseLock() }
  if (!signal.aborted) throw new Error('招聘数据更新连接已中断。')
}
