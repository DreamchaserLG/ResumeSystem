/** 统一 API 客户端：JSON、错误提取、会话头。 */

const BASE = '/api/v1'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = localStorage.getItem('rs-token')
  const headers: Record<string, string> = {
    ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
    ...(init?.headers as Record<string, string> | undefined),
  }
  if (token) headers['X-Session-Token'] = token

  const resp = await fetch(BASE + path, { ...init, headers })
  if (resp.ok && resp.headers.get('Content-Type')?.startsWith('image/')) return await resp.blob() as T
  const body = await resp.json().catch(() => ({}))
  if (resp.status === 401) {
    // 会话过期 / 权限变更：清会话回登录页
    localStorage.removeItem('rs-token')
    localStorage.removeItem('rs-session')
    if (!location.pathname.startsWith('/login')) location.href = '/login'
  }
  if (!resp.ok) {
    throw new ApiError(resp.status, (body as any).detail ?? `请求失败 (${resp.status})`)
  }
  return body as T
}

export const api = {
  upload: <T>(path: string, data: FormData) => request<T>(path, { method: 'POST', body: data }),
  download: async (path: string) => {
    const token = localStorage.getItem('rs-token') || ''
    const resp = await fetch(BASE + path, { headers: { 'X-Session-Token': token } })
    if (!resp.ok) throw new ApiError(resp.status, (await resp.json().catch(() => ({}))).detail || '文件不可用')
    const url = URL.createObjectURL(await resp.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = resp.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1] || 'resume'
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  },
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, data?: unknown) =>
    request<T>(path, { method: 'POST', body: data ? JSON.stringify(data) : undefined }),
  put: <T>(path: string, data?: unknown) =>
    request<T>(path, { method: 'PUT', body: data ? JSON.stringify(data) : undefined }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}
