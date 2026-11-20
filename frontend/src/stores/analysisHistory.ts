/** 本地历史对话：保留多轮问答和思考文本，报表明细仍可按需重算。 */
import { defineStore } from 'pinia'

export interface AnalysisHistoryTurn {
  question: string
  summary: string
  at: number
  source?: 'llm' | 'rules'
  thinking?: string
  fileNames?: string[]
}

export interface AnalysisHistoryItem {
  id: string
  question: string
  summary: string
  turns?: AnalysisHistoryTurn[]
  at: number
  source?: 'llm' | 'rules'
  departmentId?: number
  departmentName?: string
  jobId?: number
  jobTitle?: string
  /** 从首轮问题生成的标题，可由用户重命名。 */
  title?: string
  pinned?: boolean
  archived?: boolean
}

export interface AnalysisFolder {
  key: string
  name: string
  sub: string
  at: number
  items: AnalysisHistoryItem[]
}

const KEY = 'rs-analysis-history'
const FOLD_KEY = 'rs-analysis-folders-collapsed'
const LIMIT = 30

function read(): AnalysisHistoryItem[] {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter((item): item is AnalysisHistoryItem =>
      !!item && typeof item.id === 'string' && typeof item.question === 'string')
  } catch {
    return []
  }
}

function readCollapsed(): string[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(FOLD_KEY) ?? '[]')
    return Array.isArray(parsed) ? parsed.filter((key): key is string => typeof key === 'string') : []
  } catch {
    return []
  }
}

function persist(items: AnalysisHistoryItem[]) {
  try { localStorage.setItem(KEY, JSON.stringify(items)) } catch { /* 存储禁用时历史仅存在于内存 */ }
}

export const useAnalysisHistory = defineStore('analysisHistory', {
  state: () => ({
    items: read() as AnalysisHistoryItem[],
    collapsed: readCollapsed() as string[],
  }),
  getters: {
    /** 未归档的条目，按时间倒序。 */
    activeItems(state): AnalysisHistoryItem[] {
      return state.items.filter(item => !item.archived)
    },
    archived(state): AnalysisHistoryItem[] {
      return state.items.filter(item => item.archived)
    },
    /** 按岗位归组：同一个岗位的多次提问收在同一个“文件夹”里，未选岗位的归入全局查询。 */
    folders(state): AnalysisFolder[] {
      const groups = new Map<string, AnalysisFolder>()
      for (const item of state.items) {
        if (item.archived) continue
        const key = item.jobId ? 'job:' + item.jobId : 'global'
        const group = groups.get(key) ?? {
          key,
          name: item.jobTitle || '全局查询',
          sub: item.departmentName || '',
          at: 0,
          items: [],
        }
        group.items.push(item)
        group.at = Math.max(group.at, item.at)
        if (!group.sub && item.departmentName) group.sub = item.departmentName
        groups.set(key, group)
      }
      /* 置顶优先于时间：组内先排置顶项，含置顶项的组整体上浮。 */
      const weight = (item: AnalysisHistoryItem) => (item.pinned ? 1 : 0)
      return [...groups.values()]
        .map(group => ({
          ...group,
          items: [...group.items].sort((left, right) => weight(right) - weight(left) || right.at - left.at),
        }))
        .sort((left, right) =>
          (right.items.some(item => item.pinned) ? 1 : 0) - (left.items.some(item => item.pinned) ? 1 : 0)
          || right.at - left.at)
    },
  },
  actions: {
    add(entry: Omit<AnalysisHistoryItem, 'id' | 'at'>) {
      const item: AnalysisHistoryItem = { ...entry, id: crypto.randomUUID(), at: Date.now() }
      this.items = [item, ...this.items].slice(0, LIMIT)
      persist(this.items)
      return item
    },
    /** 续接已有会话：追加一轮并提到最前，不新增侧栏条目。 */
    appendTurn(id: string, turn: AnalysisHistoryTurn) {
      const target = this.items.find(item => item.id === id)
      if (!target) return
      target.turns = [...(target.turns || [{question:target.question,summary:target.summary,
        source:target.source,at:target.at}]),turn].slice(-20)
      target.summary = turn.summary
      target.source = turn.source ?? target.source
      target.at = turn.at
      this.items = [target, ...this.items.filter(item => item.id !== id)]
      persist(this.items)
    },
    find(id: string) {
      return this.items.find(item => item.id === id)
    },
    archive(id: string) {
      const target = this.items.find(item => item.id === id)
      if (!target) return
      target.archived = true
      persist(this.items)
    },
    unarchive(id: string) {
      const target = this.items.find(item => item.id === id)
      if (!target) return
      target.archived = false
      persist(this.items)
    },
    remove(id: string) {
      this.items = this.items.filter(item => item.id !== id)
      persist(this.items)
    },
    rename(id: string, title: string) {
      const target = this.items.find(item => item.id === id)
      if (!target) return
      const next = title.trim().slice(0, 60)
      target.title = next || undefined
      persist(this.items)
    },
    togglePin(id: string) {
      const target = this.items.find(item => item.id === id)
      if (!target) return
      target.pinned = !target.pinned
      persist(this.items)
    },
    isCollapsed(key: string) {
      return this.collapsed.includes(key)
    },
    toggleFolder(key: string) {
      this.collapsed = this.isCollapsed(key)
        ? this.collapsed.filter(item => item !== key)
        : [...this.collapsed, key]
      try { localStorage.setItem(FOLD_KEY, JSON.stringify(this.collapsed)) } catch { /* 忽略存储失败 */ }
    },
    clear() {
      this.items = []
      persist(this.items)
    },
  },
})

/** 当天只显示时分；昨天用“昨天”；更早补日期，跨年补年份。 */
export function historyTime(at: number) {
  const date = new Date(at)
  const now = new Date()
  const pad = (value: number) => String(value).padStart(2, '0')
  const time = pad(date.getHours()) + ':' + pad(date.getMinutes())
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const diffDays = Math.floor((startOfToday - new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()) / 86400000)
  if (diffDays <= 0) return time
  if (diffDays === 1) return '昨天 ' + time
  const day = date.getFullYear() === now.getFullYear()
    ? (date.getMonth() + 1) + '-' + pad(date.getDate())
    : date.getFullYear() + '-' + (date.getMonth() + 1) + '-' + pad(date.getDate())
  return day + ' ' + time
}
