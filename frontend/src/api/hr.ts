import { api } from './client'

export interface Department { id: number; name: string }
export const jobStatusOptions = [
  { value: 'paused', label: '未开始' },
  { value: 'open', label: '进行中' },
  { value: 'closed', label: '已结束' },
] as const
export const jobStatusLabel = (value: string) => jobStatusOptions.find(option => option.value === value)?.label ?? value
export interface InterviewRound { id: number; name: string; position: number }
export type StageCode = 'review' | 'interview' | 'rejected' | 'hired'
export interface Stage { code: StageCode; label: string; round_id: number | null }
export interface Job { id: number; title: string; status: string; department_id: number; department_name: string; jd: string | null; application_count: number; manageable: boolean; rounds: InterviewRound[] }
export type TransferJob = Pick<Job, 'id' | 'title' | 'department_id' | 'department_name'>
export const stagesFor = (rounds: InterviewRound[] = []): Stage[] => [
  { code: 'review', label: '待审核', round_id: null },
  ...rounds.map(r => ({ code: 'interview' as const, label: r.name, round_id: r.id })),
  { code: 'hired', label: '录取', round_id: null },
  { code: 'rejected', label: '淘汰', round_id: null },
]
export const stageKey = (stage: Stage) => stage.round_id ? 'interview:' + stage.round_id : stage.code
export interface Application {
  id: number; job_id: number; job_title: string; name: string | null; status: string
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
export interface Detail extends Application {
  evidence: Evidence[]; issues: Issue[]; has_preview: boolean
  stage: Stage; rounds: InterviewRound[]; workflow_version: number
}
export interface Options { departments: Department[]; hr_department_ids: number[]; skills: string[]; school_tags: { job_id: number; tag_code: string }[] }
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
  id: string; type: 'degree' | 'school' | 'experience' | 'domain' | 'skill'; value: string; filterable: boolean
}
export interface ResumeCard {
  id: number; version_id: number | null; job_id: number; job_title: string; name: string | null
  target_role: string | null; gender: string | null; city: string | null; address: string | null; phone: string | null
  status: string; stage: Stage; parse_status: string; page_count: number; has_avatar: boolean; tags: ResumeTag[]
}
export interface ResumeCardPage { items: ResumeCard[]; total: number; page: number; size: number }
