import { api } from './client'

export interface Department { id: number; name: string; company_id?: number; company_name?: string }
export const jobStatusOptions = [
  { value: 'paused', label: '未开始' },
  { value: 'open', label: '进行中' },
  { value: 'closed', label: '已结束' },
] as const
export const jobStatusLabel = (value: string) => jobStatusOptions.find(option => option.value === value)?.label ?? value
export interface InterviewRound { id: number; name: string; position: number }
export type StageCode = 'review' | 'progress' | 'interview' | 'rejected' | 'hired'
export interface Stage { code: StageCode; label: string; round_id: number | null }
export interface HiringRequirements { requirements:string; required_skills:string[]; experience_tags:string[]; recruitment_start:string|null; recruitment_end:string|null }
export interface JobPostingSource { platform:string; posting:string; url:string; match:'affiliated'|'nearby'|'market' }
export const postingSourceLabel = {affiliated:'关联企业同名岗位',nearby:'本集团相近岗位',market:'市场对标'}
export interface JobPostingReference {
  category:string|null; jd_sources:JobPostingSource[]; reference_current:boolean; jd_researched_on:string|null
  recruitment_window_is_reference:boolean
}
export interface Job extends HiringRequirements, JobPostingReference { id: number; title: string; status: string; department_id: number; department_name: string; jd: string | null; planned_headcount: number | null; target_date: string | null; application_count: number; manageable: boolean; rounds: InterviewRound[]; company_id: number; company_name: string; recruitment_type: string; recruiter_name: string; recruiter_user_id: number | null; department_recruiter_name: string | null; updated_at: string; source_category: string | null }
export type TransferJob = Pick<Job, 'id' | 'title' | 'department_id' | 'department_name'>
export const stagesFor = (rounds: InterviewRound[] = []): Stage[] => [
  { code: 'review', label: '待审核', round_id: null },
  { code: 'progress', label: '推进中', round_id: null },
  ...rounds.map(r => ({ code: 'interview' as const, label: r.name, round_id: r.id })),
  { code: 'hired', label: '录取', round_id: null },
  { code: 'rejected', label: '淘汰', round_id: null },
]
export const stageDisplay = (stage: Stage) => stage.code === 'interview' ? '推进中 · ' + stage.label : stage.label
export const stageKey = (stage: Stage) => stage.round_id ? 'interview:' + stage.round_id : stage.code
export interface Application {
  id: number; job_id: number | null; job_title: string; name: string | null; status: string
  handling_department_id: number; active_version_id: number | null; pending_version_id: number | null
  parse_status: string; extractor_version: string | null; fail_reason: string | null; submitted_at: string
}
export interface Evidence {
  record_no: number; field_key: string; span_no: number; text: string
  locator: { page?: number; bbox?: number[]; docx_para?: number; docx_table?: number[] }
  extraction_method: string; ocr_confidence: number | null
}
export interface Issue {
  record_no: number; field_key: string; reason: string; span_no: number | null
  label: string; evidence: string | null; locator: Evidence['locator'] | null; ocr_confidence: number | null
}
export const schoolTagLabels: Record<string,string> = {
  is_985:'985', is_211:'211', is_double_first_class:'双一流', study_abroad:'留学', qs_top100:'QS 前 100',
}
export interface Detail extends Application {
  original_mime_type?: string | null
  education: { record_no:number; school:string; degree:string | null; tags:string[]; qs_rank:number | null; qs_edition:number | null }[]
  evidence: Evidence[]; issues: Issue[]; has_preview: boolean
  reviewed_extensions: { record_no:number; raw_label:string; value:string }[]
  stage: Stage; rounds: InterviewRound[]; workflow_version: number
}
export interface Options { departments: Department[]; hr_department_ids: number[]; skills: string[]; school_tags: { job_id: number; tag_code: string }[]; capabilities: { code: string; name: string }[] }
export type CapabilityTag = Options['capabilities'][number]
export type CapabilityMatch = CapabilityTag & { similarity:number | null }
export interface Page<T> { items: T[]; has_more: boolean }
export interface SearchResult { items: (Pick<Application, 'id' | 'job_title' | 'name'> & { evidence: Evidence[] })[]; total: number; cached: boolean }
export const labels: Record<string, string> = {
  pending: '待筛选', active: '推进中', closed: '已结束', on_hold: '暂缓',
  parsing: '解析中', published: '已发布', failed: '解析失败',
  open: '招聘中', paused: '暂停', advance: '推进', reject: '不推进', hold: '暂缓',
  conflict: '字段冲突', low_confidence: '识别置信度低', not_found: '缺少证据',
  ambiguous_school: '学校别名歧义', ambiguous_skill: '技能别名歧义', pending_review: '待确认', unregistered: '未登记字段',
  'profile.name': '姓名', 'profile.city': '城市', 'profile.summary': '个人简介',
  'work.company': '公司', 'work.title': '岗位', 'work.period': '工作时间', 'work.description': '工作描述',
  'education.school_raw': '学校', 'education.degree_level': '学历', 'education.major': '专业', 'education.period': '教育时间',
  'project.project_name': '项目', 'project.description': '项目描述', 'project.role': '项目角色', 'skill.name': '技能',
  bachelor: '本科', master: '硕士', phd: '博士', associate: '大专', high_school: '高中',
}
export const label = (key: string) => labels[key] || key
export const dateLabel = (time: string) => new Date(time).toLocaleString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' })
export function locationText(loc: Evidence['locator'] | null) {
  if (loc?.page) return '第 ' + loc.page + ' 页'
  if (loc?.docx_para !== undefined) return 'Word 段落 ' + (loc.docx_para + 1)
  if (loc?.docx_table) return 'Word 表格第 ' + (loc.docx_table[0] + 1) + ' 行'
  return '人工确认'
}
export const loadJobs = () => api.get<{ items: Job[] }>('/jobs').then(r => r.items)

export interface ResumeTag {
  id: string; type: 'degree' | 'school' | 'experience' | 'domain' | 'capability' | 'language'; value: string; filterable: boolean
}
export interface ResumeCard {
  id: number; version_id: number | null; job_id: number | null; job_title: string; name: string | null
  candidate_id: number; in_talent: boolean; talent_status: string | null
  target_role: string | null; gender: string | null; city: string | null; address: string | null; phone: string | null
  status: string; stage: Stage; workflow_version: number; parse_status: string; page_count: number; has_avatar: boolean; tags: ResumeTag[]; latest_note?: string | null; salary_midpoint?: number | null; recent_company?: string | null; current_company?: string | null
  talent_status_label?: string | null
  match_percent?: number | null
  match_score?: JobMatchScore | null
  match_skills?: { name:string; matched:boolean }[]
}
export interface ResumeCardPage { items: ResumeCard[]; total: number; page: number; size: number; school_filters: boolean; available_facets?: string[]; semantic_degraded?: boolean }


export interface JobMatchScore {
  total:number; duty:number; skill:number; basis:'uncalibrated_positive_cosine'
  duty_weight:number; skill_weight:number; missing_required_skills:string[]
}
export type RetrievalSource = 'title'|'reference'|'jd'|'reviewed'|'pending'
export type RetrievalStatus = 'ranked'|'no_evidence'|'unindexed'|'needs_review'|'outside_requirements'
export interface JobCandidatePage extends Omit<ResumeCardPage,'items'> {
  job: JobPostingReference & Pick<Job,'id'|'title'|'status'|'department_name'|'jd'|'requirements'|'required_skills'|'experience_tags'|'recruitment_start'|'recruitment_end'|'target_date'>
  items: (ResumeCard & {match_percent:number|null; retrieval_status?:RetrievalStatus; skill_coverage?:number|null})[]
  matched_requirements:number
  unassigned_total:number
  retrieval?:{source:RetrievalSource; eligible:number; indexed:number}
}
export interface JobEvidenceSource {
  span_no:number; locator:Evidence['locator']
}
export interface JobEvidenceBlock {
  record_no:number; kind:string; text:string; locations:JobEvidenceSource[]
}
export interface JobEvidenceRequirement {
  id:number; kind:'role'|'duty'|'qualification'|'qualification_check'|'skill'|'degree'|'months'; text:string
  status:'related'|'supported'|'unverified'|'not_met'|'not_checked'
  mandatory:boolean; weight:number; scopes:string[]; method:'keyword'|'vector'|'structured'|null
  evidence_kind:string|null; matched_skill:string|null; record_no:number|null; points:number|null
}
export interface JobEvidenceReport {
  source:RetrievalSource; version_id:number|null; status:RetrievalStatus|null
  skill_coverage:number|null; score:JobMatchScore|null
  requirements:JobEvidenceRequirement[]; evidence_blocks:JobEvidenceBlock[]
}
