import { defineStore } from 'pinia'
import { api, ApiError } from '@/api/client'
import { clearSessionToken, getSessionToken, setSessionToken, takeoverNotice } from '@/api/session'

export interface Session {
  userId: number
  displayName: string
  role: 'super_admin' | 'dept_hr'
  departmentName?: string
}

export interface SessionIdentity {
  user_id: number
  display_name: string
  role: Session['role']
  display_departments: string[]
}

export function sessionFromIdentity(identity:SessionIdentity):Session {
  return {
    userId:identity.user_id, displayName:identity.display_name, role:identity.role,
    departmentName:identity.display_departments.join('、') || undefined,
  }
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    session: null as Session | null,
    resumeIssue: '' as ''|'taken-over'|'verify-failed',
  }),
  getters: {
    isAuthed: (s) => !!s.session && !!getSessionToken(),
    isSuperAdmin: (s) => s.session?.role === 'super_admin',
  },
  actions: {
    setSession(token: string, s: Session) {
      setSessionToken(token)
      this.session = s
      this.resumeIssue = ''
    },
    async restoreSession() {
      const token=getSessionToken()
      if(!token || this.session) return
      try {
        const identity=await api.verifySession<SessionIdentity>()
        if(token===getSessionToken()) this.setSession(token,sessionFromIdentity(identity))
      } catch(error) {
        if(token!==getSessionToken()) return
        if(error instanceof ApiError && error.status===401) {
          clearSessionToken()
          this.resumeIssue=error.detail===takeoverNotice?'taken-over':''
        } else this.resumeIssue='verify-failed'
      }
    },
    updateDisplayName(displayName:string) {
      if(this.session) this.session.displayName=displayName
    },
    logout() {
      if(getSessionToken()) api.post('/auth/logout').catch(() => {})
      this.session = null
      this.resumeIssue = ''
      clearSessionToken()
    },
  },
})
