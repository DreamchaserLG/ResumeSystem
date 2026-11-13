<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NLayout,
  NLayoutSider,
  NLayoutHeader,
  NLayoutContent,
} from 'naive-ui'
import {
  GridOutline,
  BarChartOutline,
  DocumentsOutline,
  SearchOutline,
  CreateOutline,
  BriefcaseOutline,
  ShieldCheckmarkOutline,
} from '@vicons/ionicons5'
import ProfileMenu from '@/components/ProfileMenu.vue'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const narrow = window.matchMedia('(max-width: 759px)')
const collapsed = ref(narrow.matches)
function onWidth(event: MediaQueryListEvent) { collapsed.value = event.matches }
onMounted(() => narrow.addEventListener('change', onWidth))
onUnmounted(() => narrow.removeEventListener('change', onWidth))
const menuItems = [
  { label: '工作台', key: 'dashboard', icon: GridOutline },
  { label: '岗位管理', key: 'jobs', icon: BriefcaseOutline },
  { label: '统计管理', key: 'statistics', icon: BarChartOutline },
  { label: '简历管理', key: 'applications', icon: DocumentsOutline },
  { label: '候选人检索', key: 'search', icon: SearchOutline },
  { label: '复核队列', key: 'review', icon: CreateOutline },
  { label: '审计日志', key: 'audit', icon: ShieldCheckmarkOutline },
  { label: '系统设置', key: 'admin', icon: ShieldCheckmarkOutline },
]
const menus = computed(() => menuItems.filter(item => (auth.isSuperAdmin
  ? ['dashboard','jobs','statistics','audit','admin'] : ['dashboard','jobs','statistics','applications','search','review']).includes(item.key)))
const activeMenu = computed(() => route.name === 'application-detail' ? 'applications' : route.name)

function logout() {
  auth.logout()
  router.push('/login')
}
</script>

<template>
  <NLayout has-sider style="height: 100dvh">
    <!-- 侧边栏 -->
    <NLayoutSider
      bordered
      collapse-mode="width"
      :collapsed-width="68"
      :width="232"
      :collapsed="collapsed"
      show-trigger
      @update:collapsed="(v: boolean) => (collapsed = v)"
      class="sider" :class="{ collapsed }"
    >
      <RouterLink to="/dashboard" class="logo-bar" aria-label="新百 CENBEST · 返回工作台">
        <svg class="logo-mark" viewBox="0 0 129 56" aria-hidden="true" focusable="false">
          <defs>
            <!-- The supplied red artwork is on white. Recover its edge opacity from the green channel. -->
            <filter id="sidebar-brand-ink" color-interpolation-filters="sRGB">
              <feColorMatrix type="matrix" values="
                0 0 0 0 1
                0 0 0 0 .36
                0 0 0 0 .40
                0 -1 0 0 1" />
            </filter>
          </defs>
          <image href="/cenbest.png" width="129" height="56" filter="url(#sidebar-brand-ink)" />
        </svg>
        <span v-if="!collapsed" class="logo-name">招聘工作台</span>
      </RouterLink>
      <nav class="side-nav" aria-label="工作区导航">
        <RouterLink v-for="item in menus" :key="item.key" :to="'/' + item.key" class="side-link"
          :class="{ current: activeMenu === item.key }" :aria-current="activeMenu === item.key ? 'page' : undefined"
          :aria-label="item.label" :title="collapsed ? item.label : undefined">
          <component :is="item.icon" class="side-icon" aria-hidden="true"/>
          <span v-if="!collapsed">{{ item.label }}</span>
        </RouterLink>
      </nav>
    </NLayoutSider>

    <!-- 主体 -->
    <NLayout>
      <NLayoutHeader bordered class="header">
        <div class="header-left">
          <span class="page-title">{{ (route.meta.title as string) ?? '' }}</span>
        </div>
        <div class="header-right">
          <ProfileMenu @logout="logout"/>
        </div>
      </NLayoutHeader>

      <NLayoutContent class="content" content-style="height:100%;">
        <RouterView v-slot="{ Component }">
          <Transition name="fade-slide" mode="out-in">
            <component :is="Component" />
          </Transition>
        </RouterView>
      </NLayoutContent>
    </NLayout>
  </NLayout>
</template>

<style scoped>
.sider {
  background: #273c56 !important;
  color: #eef4fc;
}
.sider :deep(.n-layout-sider-scroll-container) {
  background: linear-gradient(180deg,#304763,#263a54);
}
.sider :deep(.n-layout-toggle-button) {
  background:#405b7c; color:#fff; border:1px solid #7188a5;
}

.side-nav { display:grid; gap:6px; padding:8px; }
.side-link { display:flex; align-items:center; gap:9px; min-height:42px; padding:0 10px; border-radius:8px; color:#dce6f5; text-decoration:none; font-size:14px; white-space:nowrap; }
.side-link:hover { background:#3b526f; color:#fff; }
.side-link.current { background:#465f81; color:#fff; }
.side-link:focus-visible { outline:2px solid #a9c4f0; outline-offset:-2px; }
.side-icon { width:20px; height:20px; flex:none; color:inherit; }
.collapsed .side-link { justify-content:center; padding:0; }

.logo-bar {
  height: var(--rs-header-height);
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 16px;
  border-bottom: 1px solid #48607c;
  overflow: hidden;
  color: inherit;
  text-decoration: none;
}
.logo-mark { display:block; flex:none; width:90px; height:auto; }
.collapsed .logo-bar { padding:0; justify-content:center; }
.collapsed .logo-mark { width:48px; }
.logo-name {
  font-weight: 700;
  font-size: 14px;
  letter-spacing: 0.2px;
  white-space: nowrap;
}
.logo-bar:focus-visible { outline: 2px solid #a9c4f0; outline-offset: -2px; }

.header {
  height: var(--rs-header-height);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  background: var(--rs-bg-card);
}
.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.page-title {
  font-size: 16px;
  font-weight: 600;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 14px;
}
.content {
  height:calc(100dvh - var(--rs-header-height));
  background: var(--rs-bg);
}
</style>
