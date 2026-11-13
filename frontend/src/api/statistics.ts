import { api } from './client'

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
