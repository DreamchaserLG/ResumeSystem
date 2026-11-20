import { api } from './client'
import type { ResumeCard, ResumeCardPage } from './hr'

export interface TalentStatusOption { code: string; label: string; color: string }

export interface TalentCard {
  id: number; candidate_id: number; status: string; status_label: string | null
  entered_at: string; updated_at: string
  application_id: number; job_title: string | null; department_id: number; department_name: string
  name: string | null; city: string | null; target_role: string | null
  version_id: number; parse_status: string
  capabilities: string[]
}

export interface TalentPage { items: TalentCard[]; counts: Record<string,number>; total: number }
export type TalentResumeCard = ResumeCard & { talent_id:number; talent_status:string; talent_updated_at:string }
export interface TalentSearchPage extends Omit<ResumeCardPage,'items'> {
  items: TalentResumeCard[]; counts: Record<string,number>
}

export interface TalentFollowup {
  id: number; author_id: number | null; author_name: string; body: string
  status: string | null; status_label: string | null; created_at: string
}

export interface TalentInterview {
  id: number; scheduled_at: string; note: string | null; created_at: string
}

export const talentStatusOptions = () => api.get<{ items: TalentStatusOption[] }>('/talent/status-options').then(r => r.items)

export const listTalent = (params: { status?: string; capability?: string; departmentId?: number; query?: string; offset?: number; limit?: number } = {}) => {
  const qs = new URLSearchParams()
  if (params.status) qs.set('status', params.status)
  if (params.capability) qs.set('capability', params.capability)
  if (params.departmentId) qs.set('department_id', String(params.departmentId))
  if (params.query) qs.set('query', params.query)
  if (params.offset) qs.set('offset', String(params.offset))
  if (params.limit) qs.set('limit', String(params.limit))
  const suffix = qs.toString() ? '?' + qs.toString() : ''
  return api.get<TalentPage>('/talent' + suffix)
}

export const talentDetail = (id: number) => api.get<TalentCard & { interviews: TalentInterview[] }>(`/talent/${id}`)
export const searchTalent = (body: object, status?: string) =>
  api.post<TalentSearchPage>('/talent/search' + (status ? '?status=' + encodeURIComponent(status) : ''), body)

export const addTalent = (body: { application_id: number }) =>
  api.post<{ id: number; duplicate: boolean }>('/talent', body)

export const talentFollowups = (id: number, before?: number) =>
  api.get<{ items: TalentFollowup[]; next: number | null }>(`/talent/${id}/followups${before ? '?before=' + before : ''}`)

export const addTalentFollowup = (id: number, body: {content:string; status?:string; status_label?:string}) =>
  api.post<TalentFollowup>(`/talent/${id}/followups`, body)

export const deleteTalentFollowup = (talentId: number, noteId: number) =>
  api.delete(`/talent/${talentId}/followups/${noteId}`)

export const scheduleTalentInterview = (id: number, body: { scheduled_at: string; note?: string | null }) =>
  api.post<{ id: number }>(`/talent/${id}/interview`, body)
