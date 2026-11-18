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
  BarChartOutline,
  DocumentsOutline,
  BriefcaseOutline,
  PeopleOutline,
} from '@vicons/ionicons5'
import ProfileMenu from '@/components/ProfileMenu.vue'
import { useAuthStore } from '@/stores/auth'
import { api } from '@/api/client'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const narrow = window.matchMedia('(max-width: 759px)')
const collapsed = ref(narrow.matches)
function onWidth(event: MediaQueryListEvent) { collapsed.value = event.matches }
let sessionTimer:ReturnType<typeof setInterval>|undefined
let checking=false
async function checkSession() {
  if(checking || !auth.isAuthed || document.hidden) return
  checking=true
  try { await api.get('/auth/me') }
  catch { /* A rejected session redirects from the API client; transient errors retry later. */ }
  finally { checking=false }
}
onMounted(()=>{
  narrow.addEventListener('change',onWidth)
  window.addEventListener('focus',checkSession)
  document.addEventListener('visibilitychange',checkSession)
  sessionTimer=setInterval(checkSession,15000)
})
onUnmounted(()=>{
  narrow.removeEventListener('change',onWidth)
  window.removeEventListener('focus',checkSession)
  document.removeEventListener('visibilitychange',checkSession)
  clearInterval(sessionTimer)
})
const menuItems = computed(()=>[
  ...(auth.isSuperAdmin?[{label:'用户管理',key:'users',icon:PeopleOutline}]:[]),
  { label: '岗位管理', key: 'jobs', icon: BriefcaseOutline },
  { label: '简历管理', key: 'applications', icon: DocumentsOutline },
  { label: 'AI 分析', key: 'analysis', icon: BarChartOutline },
])
const activeMenu = computed(() => route.name === 'job-candidates' ? 'jobs' : route.name === 'application-detail' ? 'applications' : route.name)

function logout() {
  auth.logout()
  router.replace('/login')
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
      <RouterLink to="/jobs" class="logo-bar" aria-label="新百 CENBEST · 返回岗位管理">
        <svg class="logo-mark" :viewBox="collapsed ? '0 0 66 56' : '0 0 129 56'" aria-hidden="true" focusable="false">
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
        <RouterLink v-for="item in menuItems" :key="item.key" :to="'/' + item.key" class="side-link"
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

      <NLayoutContent class="content recruiting-content" content-style="height:100%;">
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
  position: relative;
  color: #e8f2f3;
  background: var(--rs-gradient-ink) !important;
  border-right: 0 !important;
  box-shadow: 8px 0 28px rgba(10, 35, 43, .12);
}
.sider::after {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  background:
    radial-gradient(circle at 16% 92%, rgba(43, 204, 188, .16), transparent 28%),
    linear-gradient(115deg, transparent 0 42%, rgba(255, 255, 255, .018) 42% 43%, transparent 43% 100%);
}
.sider :deep(.n-layout-sider-scroll-container) {
  position: relative;
  z-index: 1;
  background: transparent;
}
.sider :deep(.n-layout-toggle-button) {
  color: #fff;
  border: 1px solid #7188a5;
  background: #405b7c;
  transform: none !important;
  transition: background-color var(--rs-motion-fast), border-color var(--rs-motion-fast);
}
.sider :deep(.n-layout-toggle-button:hover) { color:#fff; border-color:#7188a5; background:#405b7c; transform:none !important; }

.side-nav { display:grid; gap:7px; padding:14px 10px; }
.side-link {
  position: relative;
  display:flex; align-items:center; gap:11px; min-height:45px; padding:0 13px;
  border: 1px solid transparent; border-radius:10px; color:#c8d8dc; text-decoration:none;
  font-size:14px; font-weight: 520; white-space:nowrap; overflow: hidden;
  transition: color var(--rs-motion-fast), background-color var(--rs-motion-fast), border-color var(--rs-motion-fast), transform var(--rs-motion-fast) var(--rs-ease);
}
.side-link::before {
  content: '';
  position: absolute;
  left: 0;
  width: 3px;
  height: 18px;
  border-radius: 0 4px 4px 0;
  background: #64d7cb;
  opacity: 0;
  transform: scaleY(.4);
  transition: opacity var(--rs-motion-fast), transform var(--rs-motion-panel) var(--rs-ease);
}
.side-link:hover { color:#fff; background:rgba(255,255,255,.07); transform: translateX(2px); }
.side-link.current { color:#effffb; border-color: rgba(99, 218, 204, .19); background:linear-gradient(90deg, rgba(32, 177, 162, .23), rgba(255,255,255,.055)); box-shadow: inset 0 1px 0 rgba(255,255,255,.04); }
.side-link.current::before { opacity: 1; transform: scaleY(1); }
.side-link:focus-visible { outline:2px solid #7bddd3; outline-offset:-2px; }
.side-icon { width:20px; height:20px; flex:none; color:inherit; filter: drop-shadow(0 2px 5px rgba(0,0,0,.1)); }
.collapsed .side-link { justify-content:center; padding:0; }
.collapsed .side-link:hover { transform: none; }

.logo-bar {
  height: var(--rs-header-height);
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 0 17px;
  border-bottom: 1px solid rgba(189, 226, 226, .14);
  overflow: hidden;
  color: inherit;
  text-decoration: none;
  background: rgba(7, 28, 35, .12);
}
.logo-mark { display:block; flex:none; width:88px; height:auto; overflow:hidden; filter: drop-shadow(0 3px 8px rgba(0,0,0,.14)); }
.collapsed .logo-bar { padding:0; justify-content:center; }
.collapsed .logo-mark { width:37px; }
.logo-name {
  font-weight: 650;
  font-size: 13px;
  letter-spacing: .5px;
  white-space: nowrap;
  color: #dbe9eb;
}
.logo-bar:focus-visible { outline: 2px solid #7bddd3; outline-offset: -2px; }

.header {
  height: var(--rs-header-height);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 25px;
  border-bottom: 1px solid rgba(215, 225, 229, .84) !important;
  background: rgba(255, 255, 255, .88);
  backdrop-filter: blur(16px) saturate(140%);
  box-shadow: 0 2px 14px rgba(26, 55, 65, .035);
}
.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.page-title {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  color: #24404c;
  font-size: 14px;
  font-weight: 650;
}
.page-title::before {
  content: '';
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--rs-primary);
  box-shadow: 0 0 0 4px var(--rs-primary-soft);
}
.header-right {
  display: flex;
  align-items: center;
  gap: 14px;
}
.content {
  height:calc(100dvh - var(--rs-header-height));
  background: transparent;
}

@media (max-width: 759px) {
  .header { padding: 0 16px; }
  .page-title { font-size: 13px; }
}
</style>
