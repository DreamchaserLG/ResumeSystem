<script setup lang="ts">
import { computed, h, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NLayout,
  NLayoutSider,
  NLayoutHeader,
  NLayoutContent,
  NIcon,
  NAvatar,
  NDropdown,
} from 'naive-ui'
import {
  GridOutline,
  DocumentsOutline,
  SearchOutline,
  CreateOutline,
  BriefcaseOutline,
  ShieldCheckmarkOutline,
  LogOutOutline,
} from '@vicons/ionicons5'
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
  { label: '简历管理', key: 'applications', icon: DocumentsOutline },
  { label: '候选人检索', key: 'search', icon: SearchOutline },
  { label: '复核队列', key: 'review', icon: CreateOutline },
  { label: '审计日志', key: 'audit', icon: ShieldCheckmarkOutline },
  { label: '系统设置', key: 'admin', icon: ShieldCheckmarkOutline },
]
const menus = computed(() => menuItems.filter(item => (auth.isSuperAdmin
  ? ['dashboard','jobs','audit','admin'] : ['dashboard','jobs','applications','search','review']).includes(item.key)))
const activeMenu = computed(() => route.name === 'application-detail' ? 'applications' : route.name)

const userOptions = [{ label: '退出登录', key: 'logout', icon: () => h(NIcon, null, { default: () => h(LogOutOutline) }) }]

function onUserAction(key: string) {
  if (key === 'logout') {
    auth.logout()
    router.push('/login')
  }
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
      <div class="logo-bar">
        <div class="logo-mark">R</div>
        <transition name="fade">
          <span v-if="!collapsed" class="logo-name">智能简历筛选</span>
        </transition>
      </div>
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
          <NDropdown :options="userOptions" @select="onUserAction">
            <div class="user-chip">
              <NAvatar round :size="30" class="user-avatar">
                {{ auth.session?.displayName?.slice(0, 1) ?? 'U' }}
              </NAvatar>
              <div class="user-meta">
                <span class="user-name">{{ auth.session?.displayName ?? '未登录' }}</span>
                <span class="user-role">
                  {{ auth.isSuperAdmin ? '超级管理员' : '部门 HR' }}
                </span>
              </div>
            </div>
          </NDropdown>
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
  background: #172641 !important;
  color: #e9f0ff;
}
.sider :deep(.n-layout-sider-scroll-container) {
  background: linear-gradient(180deg,#182844,#122039);
}
.sider :deep(.n-layout-toggle-button) {
  background:#314b75; color:#fff; border:1px solid #59739f;
}

.side-nav { display:grid; gap:6px; padding:8px; }
.side-link { display:flex; align-items:center; gap:9px; min-height:42px; padding:0 10px; border-radius:8px; color:#c7d4ec; text-decoration:none; font-size:14px; white-space:nowrap; }
.side-link:hover { background:#263856; color:#fff; }
.side-link.current { background:#344d79; color:#fff; }
.side-link:focus-visible { outline:2px solid #a9c4f0; outline-offset:-2px; }
.side-icon { width:20px; height:20px; flex:none; color:inherit; }
.collapsed .side-link { justify-content:center; padding:0; }

.logo-bar {
  height: var(--rs-header-height);
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 16px;
  border-bottom: 1px solid #2d4062;
  overflow: hidden;
}
.logo-mark {
  width: 32px;
  height: 32px;
  flex: none;
  border-radius: 8px;
  background: var(--rs-gradient);
  color: #fff;
  display: grid;
  place-items: center;
  font-weight: 800;
  font-size: 16px;
  box-shadow: 0 4px 14px rgba(91, 124, 250, 0.4);
}
.logo-name {
  font-weight: 700;
  font-size: 15px;
  letter-spacing: 0.5px;
  white-space: nowrap;
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.15s;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

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
.icon-btn {
  width: 34px;
  height: 34px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--rs-text-secondary);
  cursor: pointer;
  display: grid;
  place-items: center;
  transition: all 0.15s;
}
.icon-btn:hover {
  background: var(--rs-primary-soft);
  color: var(--rs-primary);
}
.user-chip {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 5px 10px 5px 5px;
  border-radius: 10px;
  cursor: pointer;
  transition: background 0.15s;
}
.user-chip:hover {
  background: var(--rs-primary-soft);
}
.user-avatar {
  background: var(--rs-gradient);
  color: #fff;
  font-weight: 700;
}
.user-meta {
  display: flex;
  flex-direction: column;
  line-height: 1.25;
}
.user-name {
  font-size: 13px;
  font-weight: 600;
}
.user-role {
  font-size: 11px;
  color: var(--rs-text-tertiary);
}

.content {
  height:calc(100dvh - var(--rs-header-height));
  background: var(--rs-bg);
}
</style>
