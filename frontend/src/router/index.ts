import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('@/views/LoginView.vue'), meta: { public: true } },
    {
      path: '/', component: () => import('@/layouts/MainLayout.vue'),
      children: [
        { path: '', redirect: { name:'jobs' } },
        { path: 'users', name:'users', component: () => import('@/views/UserManagementView.vue'),
          meta: { title:'用户管理', role:'super_admin' } },
        { path: 'jobs', name: 'jobs', component: () => import('@/views/JobsView.vue'), meta: { title: '岗位管理' } },
        { path: 'jobs/:jobId(\\d+)/candidates', name:'job-candidates', component: () => import('@/views/JobCandidatesView.vue'),
          meta: { title:'岗位候选人' } },
        { path: 'applications', name: 'applications', component: () => import('@/views/ApplicationsView.vue'),
          meta: { title:'简历管理' } },
        { path: 'talent', name: 'talent', component: () => import('@/views/TalentPoolView.vue'),
          meta: { title:'人才管理' } },
        { path:'recruitment-board', name:'recruitment-board', component:() => import('@/views/RecruitmentDashboardView.vue'),
          meta:{ title:'招聘看板', role:'super_admin' } },
        { path: 'analysis', name: 'analysis', redirect: { name:'jobs' } },
        { path: 'applications/:id', name: 'application-detail', redirect: to => ({
          name:'applications', query:{ ...to.query, candidate:String(to.params.id) },
        }) },
        { path: 'dashboard', name:'dashboard', redirect: { name:'jobs' } },
        { path: 'statistics', name:'statistics', redirect: { name:'analysis' } },
        { path: 'search', name:'search', redirect: { name:'applications' } },
        { path: 'review', name:'review', redirect: { name:'applications' } },
        { path: 'admin', name:'admin', redirect: { name:'users' } },
        { path: 'audit', name:'audit', redirect: { name:'jobs' } },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect:{ name:'jobs' } },
  ],
})

router.beforeEach(async to => {
  const auth = useAuthStore()
  if (!auth.isAuthed && !auth.resumeIssue) await auth.restoreSession()
  if (!to.meta.public && !auth.isAuthed) return { name:'login', query:{next:to.fullPath,reason:auth.resumeIssue || undefined} }
  if (to.name==='login' && auth.isAuthed) return { name:'jobs' }
  if (to.meta.role && auth.session?.role!==to.meta.role) return { name:'jobs' }
})

export default router
