import { api } from './client'

export interface Counts { applications: number; hired: number }
export interface AtlasRegion extends Counts { id: string }
export interface AtlasMajor extends Counts { major: string }
export interface AtlasSchool extends Counts {
  id: number; region: string; school: string; city: string | null; location: string | null
  tier: string | null; qs_rank: number | null; campus_map_url: string | null; majors: AtlasMajor[]
}
export interface AtlasData extends Counts {
  scope: 'super_admin' | 'dept_hr'
  gender_allowed: boolean; school_tags_allowed: boolean; degree_allowed: boolean; gender: Record<string, number>
  regions: AtlasRegion[]; schools: AtlasSchool[]
  trend: (Counts & { day:string })[]
  period: { start:string; end:string; unit:string }
}
export const fetchAtlas = (query: URLSearchParams) => api.get<AtlasData>('/statistics/atlas?' + query)
