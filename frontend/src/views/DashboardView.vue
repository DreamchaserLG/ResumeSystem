<script setup lang="ts">
import { computed } from 'vue'
import { useAuthStore } from '@/stores/auth'
const auth = useAuthStore()
const links = computed(() => auth.isSuperAdmin ? [
  { to: '/admin', title: '系统设置', text: '部门、账号、授权和集成' },
  { to: '/audit', title: '审计日志', text: '查阅系统操作记录' },
  { to: '/jobs', title: '岗位管理', text: '查看各部门岗位' },
] : [
  { to: '/applications', title: '简历管理', text: '导入简历并跟进招聘决定' },
  { to: '/review', title: '复核队列', text: '核对有疑问的字段' },
  { to: '/search', title: '候选人检索', text: '按条件和经历查找候选人' },
  { to: '/jobs', title: '岗位管理', text: '创建或调整本部门岗位' },
])
</script>
<template>
  <main class="rs-page rs-workspace">
    <header class="rs-heading"><div><h1>工作台</h1><p>{{ auth.session?.displayName }}</p></div></header>
    <div class="rs-two-col">
      <section v-for="item in links" :key="item.to" class="rs-card rs-panel">
        <h2><RouterLink class="inline" :to="item.to">{{ item.title }}</RouterLink></h2><p class="rs-muted">{{ item.text }}</p>
      </section>
    </div>
  </main>
</template>
