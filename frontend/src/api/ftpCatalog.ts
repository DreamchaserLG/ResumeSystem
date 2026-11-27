import { api } from './client'

export interface CatalogFolder {
  path: string
  department_id: number | null
  job_id: number | null
  writable: boolean
}

export interface FtpCatalog {
  enabled: boolean
  synced_at: string | null
  folders: CatalogFolder[]
}

export type SyncStatus =
  | { status: 'pending' | 'running' | 'busy' }
  | { status: 'failed'; message: string }
  | { status: 'complete'; folders: number; added: number; updated: number; deleted: number;
      unchanged: number; skipped: number; queued?: number; message?: string }

export const fetchCatalog = () => api.get<FtpCatalog>('/job-catalog')
export const createCatalogFolder = (parent: string, name: string, department_id: number | null) =>
  api.post<{path: string; job_id: number | null}>('/job-catalog/folders', {parent, name, department_id})
export const startCatalogSync = () => api.post<{id: string}>('/job-catalog/sync')
export const fetchSync = (id: string) => api.get<SyncStatus>('/job-catalog/sync/' + id)
