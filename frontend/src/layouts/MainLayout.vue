<script setup lang="ts">
import { computed, h, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NLayout,
  NLayoutSider,
  NLayoutHeader,
  NLayoutContent,
  NMenu,
  NIcon,
  NAvatar,
  NDropdown,
  type MenuOption,
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
const collapsed = ref(window.innerWidth < 760)
const menuTheme = {
  itemTextColor: '#c7d4ec', itemIconColor: '#b5c7e4',
  itemTextColorHover: '#ffffff', itemIconColorHover: '#ffffff',
  itemTextColorActive: '#ffffff', itemIconColorActive: '#ffffff',
  itemTextColorActiveHover: '#ffffff', itemIconColorActiveHover: '#ffffff',
  itemColorHover: '#263856', itemColorActive: '#344d79', itemColorActiveHover: '#405b89',
}

function icon(comp: any) {
  return () => h(NIcon, null, { default: () => h(comp) })
}

const allMenus: MenuOption[] = [
  {
    label: '工作台',
    key: 'dashboard',
    icon: icon(GridOutline),
  },
  {
    label: '简历管理',
    key: 'applications',
    icon: icon(DocumentsOutline),
  },
  {
    label: '候选人检索',
    key: 'search',
    icon: icon(SearchOutline),
  },
  {
    label: '复核队列',
    key: 'review',
    icon: icon(CreateOutline),
  },
  {
    label: '岗位管理',
    key: 'jobs',
    icon: icon(BriefcaseOutline),
  },
  {
    label: '审计日志',
    key: 'audit',
    icon: icon(ShieldCheckmarkOutline),
  },
]

allMenus.push({ label: '系统设置', key: 'admin', icon: icon(ShieldCheckmarkOutline) })
const menus = computed(() => allMenus.filter(item => (auth.isSuperAdmin ? ['dashboard','jobs','audit','admin'] : ['dashboard','jobs','applications','search','review']).includes(String(item.key))))

const userOptions = [{ label: '退出登录', key: 'logout', icon: icon(LogOutOutline) }]

function onMenuSelect(key: string) {
  router.push(`/${key}`)
}

function onUserAction(key: string) {
  if (key === 'logout') {
    auth.logout()
    router.push('/login')
  }
}

</script>

<template>
  <NLayout has-sider style="height: 100vh">
    <!-- 侧边栏 -->
    <NLayoutSider
      bordered
      collapse-mode="width"
      :collapsed-width="68"
      :width="232"
      :collapsed="collapsed"
      show-trigger
      @update:collapsed="(v: boolean) => (collapsed = v)"
      class="sider"
    >
      <div class="logo-bar">
        <div class="logo-mark">R</div>
        <transition name="fade">
          <span v-if="!collapsed" class="logo-name">智能简历筛选</span>
        </transition>
      </div>
      <NMenu
        :value="route.name as string"
        :options="menus"
        :collapsed="collapsed"
        :theme-overrides="menuTheme"
        :collapsed-width="68"
        :collapsed-icon-size="20"
        :indent="18"
        @update:value="onMenuSelect"
      />
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
  background: var(--rs-bg);
}
</style>
