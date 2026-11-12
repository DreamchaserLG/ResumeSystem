import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { public: true },
    },
    {
      path: '/',
      component: () => import('@/layouts/MainLayout.vue'),
      children: [
        { path: '', redirect: '/dashboard' },
        {
          path: 'dashboard',
          name: 'dashboard',
          component: () => import('@/views/DashboardView.vue'),
          meta: { title: '工作台' },
        },
        {
          path: 'applications',
          name: 'applications',
          component: () => import('@/views/ApplicationsView.vue'),
          meta: { title: '简历管理', role: 'dept_hr' },
        },
        {
          path: 'search',
          name: 'search',
          component: () => import('@/views/SearchView.vue'),
          meta: { title: '候选人检索', role: 'dept_hr' },
        },
        {
          path: 'review',
          name: 'review',
          component: () => import('@/views/ReviewQueueView.vue'),
          meta: { title: '复核队列', role: 'dept_hr' },
        },
        { path: 'applications/:id', name: 'application-detail', component: () => import('@/views/ApplicationDetailView.vue'), meta: { title: '申请详情', role: 'dept_hr' } },
        { path: 'admin', name: 'admin', component: () => import('@/views/AdminView.vue'), meta: { title: '系统设置', role: 'super_admin' } },
        {
          path: 'jobs',
          name: 'jobs',
          component: () => import('@/views/JobsView.vue'),
          meta: { title: '岗位管理' },
        },
        {
          path: 'audit',
          name: 'audit',
          component: () => import('@/views/AuditView.vue'),
          meta: { title: '审计日志', role: 'super_admin' },
        },
      ],
    },
  ],
})

router.beforeEach((to) => {
  const authed = localStorage.getItem('rs-session')
  if (!to.meta.public && !authed) return { name: 'login' }
  if (to.name === 'login' && authed) return { name: 'dashboard' }
  if (authed && to.meta.role && JSON.parse(authed).role !== to.meta.role) return { name: 'dashboard' }
})

export default router
