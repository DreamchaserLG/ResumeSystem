import { defineStore } from 'pinia'
import { api } from '@/api/client'

export interface Session {
  userId: number
  displayName: string
  role: 'super_admin' | 'dept_hr'
  departmentName?: string
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    session: JSON.parse(localStorage.getItem('rs-session') ?? 'null') as Session | null,
  }),
  getters: {
    isAuthed: (s) => !!s.session,
    isSuperAdmin: (s) => s.session?.role === 'super_admin',
  },
  actions: {
    setSession(s: Session) {
      this.session = s
      localStorage.setItem('rs-session', JSON.stringify(s))
    },
    logout() {
      api.post('/auth/logout').catch(() => {})
      this.session = null
      localStorage.removeItem('rs-session')
      localStorage.removeItem('rs-token')
    },
  },
})
