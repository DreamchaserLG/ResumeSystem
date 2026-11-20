/** 统一 API 客户端：JSON、错误提取、会话头。 */
import { expireSession, getSessionToken } from './session'

const BASE = '/api/v1'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string, public detail?:unknown) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit, redirectOnUnauthorized=true): Promise<T> {
  const token = path==='/auth/login' ? '' : getSessionToken()
  const headers: Record<string, string> = {
    ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
    ...(init?.headers as Record<string, string> | undefined),
  }
  if (token) headers['X-Session-Token'] = token

  const resp = await fetch(BASE + path, { cache:'no-store', ...init, headers })
  const contentType=resp.headers.get('Content-Type') || ''
  if (resp.ok && (contentType.startsWith('image/') || contentType.startsWith('application/pdf'))) return await resp.blob() as T
  const body = await resp.json().catch(() => ({}))
  const detail = body.detail
  if (resp.status === 401 && redirectOnUnauthorized) expireSession(token,detail)
  if (!resp.ok) {
    const message=typeof detail==='string'?detail:typeof detail?.message==='string'?detail.message:`请求失败 (${resp.status})`
    throw new ApiError(resp.status,message,detail)
  }
  return body as T
}

export const api = {
  upload: <T>(path: string, data: FormData) => request<T>(path, { method: 'POST', body: data }),
  download: async (path: string) => {
    const token = getSessionToken()
    const resp = await fetch(BASE + path, { cache:'no-store', headers: token ? { 'X-Session-Token': token } : {} })
    if (!resp.ok) {
      const detail=(await resp.json().catch(()=>({}))).detail
      if(resp.status===401) expireSession(token,detail)
      throw new ApiError(resp.status, typeof detail==='string'?detail:'文件不可用', detail)
    }
    const url = URL.createObjectURL(await resp.blob())
    const link = document.createElement('a')
    const disposition = resp.headers.get('Content-Disposition') || ''
    const utf8Name = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
    link.href = url
    link.download = utf8Name ? decodeURIComponent(utf8Name) : disposition.match(/filename="([^"]+)"/)?.[1] || 'resume'
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  },
  get: <T>(path: string) => request<T>(path),
  verifySession: <T>() => request<T>('/auth/me', {signal:AbortSignal.timeout(8000)}, false),
  post: <T>(path: string, data?: unknown) =>
    request<T>(path, { method: 'POST', body: data ? JSON.stringify(data) : undefined }),
  put: <T>(path: string, data?: unknown) =>
    request<T>(path, { method: 'PUT', body: data ? JSON.stringify(data) : undefined }),
  patch: <T>(path: string, data?: unknown) =>
    request<T>(path, { method: 'PATCH', body: data ? JSON.stringify(data) : undefined }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}
