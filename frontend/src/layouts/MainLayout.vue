<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NLayout,
  NLayoutSider,
  NLayoutHeader,
  NLayoutContent,
} from 'naive-ui'
import {
  ArchiveOutline,
  ArrowUndoOutline,
  BarChartOutline,
  ChevronDownOutline,
  ChevronForwardOutline,
  CreateOutline,
  DocumentsOutline,
  EllipsisHorizontalOutline,
  BriefcaseOutline,
  LinkOutline,
  PeopleOutline,
  PinOutline,
  SearchOutline,
  TrashOutline,
} from '@vicons/ionicons5'
import ProfileMenu from '@/components/ProfileMenu.vue'
import { useAuthStore } from '@/stores/auth'
import { historyTime, useAnalysisHistory } from '@/stores/analysisHistory'
import { api } from '@/api/client'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const narrow = window.matchMedia('(max-width: 759px)')
const collapsed = ref(narrow.matches)
function onWidth(event: MediaQueryListEvent) { collapsed.value = event.matches }
let sessionTimer:ReturnType<typeof setInterval>|undefined
let siderEl: HTMLElement | null = null
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
  document.addEventListener('click',closeMenu)
  /* 收起后点击侧栏任意位置都展开（含空白区），无需命中具体按钮。 */
  siderEl=document.querySelector('.sider')
  siderEl?.addEventListener('click',expandSider)
  sessionTimer=setInterval(checkSession,15000)
})
onUnmounted(()=>{
  narrow.removeEventListener('change',onWidth)
  window.removeEventListener('focus',checkSession)
  document.removeEventListener('visibilitychange',checkSession)
  document.removeEventListener('click',closeMenu)
  siderEl?.removeEventListener('click',expandSider)
  clearInterval(sessionTimer)
})
const menuItems = computed(()=>[
  ...(auth.isSuperAdmin?[{label:'用户管理',key:'users',icon:PeopleOutline}]:[]),
  { label: '岗位管理', key: 'jobs', icon: BriefcaseOutline },
  { label: '简历管理', key: 'applications', icon: DocumentsOutline },
  { label: 'AI 分析', key: 'analysis', icon: BarChartOutline },
])
const activeMenu = computed(() => route.name === 'job-candidates' ? 'jobs' : route.name === 'application-detail' ? 'applications' : route.name)
const history = useAnalysisHistory()
const keyword = ref('')
const activeHistory = computed(() => String(route.query.h ?? ''))
/* 搜索时忽略折叠状态，否则命中的对话藏在收起的文件夹里。 */
const visibleFolders = computed(() => {
  const query = keyword.value.trim().toLowerCase()
  if (!query) return history.folders
  return history.folders
    .map(folder => ({
      ...folder,
      items: folder.items.filter(item =>
        item.question.toLowerCase().includes(query) || item.summary.toLowerCase().includes(query)),
    }))
    .filter(folder => folder.items.length > 0)
})
function openHistory(id: string) { void router.push({ name: 'analysis', query: { h: id } }) }
/* 收起状态点侧边栏任意位置都展开，接近 GPT 的操作手感。 */
function expandSider() { if (collapsed.value) collapsed.value = false }
/* 删除需要二次确认，避免误删本地唯一副本。 */
const pendingDelete = ref('')
let pendingTimer: ReturnType<typeof setTimeout> | undefined
function requestDelete(id: string) {
  if (pendingDelete.value === id) {
    pendingDelete.value = ''
    if (pendingTimer) clearTimeout(pendingTimer)
    history.remove(id)
    menuId.value = ''
    if (activeHistory.value === id) void router.push({ name: 'analysis' })
    return
  }
  pendingDelete.value = id
  if (pendingTimer) clearTimeout(pendingTimer)
  pendingTimer = setTimeout(() => { pendingDelete.value = '' }, 2600)
}

/* 条目操作菜单：置顶 / 重命名 / 复制链接 / 归档 / 删除。 */
const menuId = ref('')
const renamingId = ref('')
const renameDraft = ref('')
const copiedMenuId = ref('')
let copiedTimer: ReturnType<typeof setTimeout> | undefined
function toggleMenu(id: string) {
  renamingId.value = ''
  menuId.value = menuId.value === id ? '' : id
}
function closeMenu() { menuId.value = '' }
async function startRename(item: { id: string; title?: string; question: string }) {
  menuId.value = ''
  renamingId.value = item.id
  renameDraft.value = item.title || item.question
  await nextTick()
  ;(document.querySelector('.side-rename') as HTMLInputElement | null)?.focus()
}
function commitRename() {
  if (!renamingId.value) return
  history.rename(renamingId.value, renameDraft.value)
  renamingId.value = ''
}
async function copyLink(id: string) {
  const url = location.origin + '/analysis?h=' + encodeURIComponent(id)
  try { await navigator.clipboard.writeText(url) } catch { /* 剪贴板不可用时仍保留菜单 */ }
  copiedMenuId.value = id
  if (copiedTimer) clearTimeout(copiedTimer)
  copiedTimer = setTimeout(() => { copiedMenuId.value = '' }, 1800)
}
function pickPin(id: string) { history.togglePin(id); menuId.value = '' }
function pickArchive(id: string) { history.archive(id); menuId.value = '' }
function pickUnarchive(id: string) { history.unarchive(id); menuId.value = '' }

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
      @update:collapsed="(v: boolean) => (collapsed = v)"
      class="sider" :class="{ collapsed }"
    >
      <div class="logo-bar">
        <template v-if="!collapsed">
          <RouterLink to="/jobs" class="logo-link" aria-label="新百 CENBEST · 返回岗位管理">
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
            <span class="logo-name">招聘工作台</span>
          </RouterLink>
          <button type="button" class="sider-toggle inside" title="收起侧边栏" aria-label="收起侧边栏" @click.stop="collapsed = true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true" focusable="false">
              <rect x="3" y="4.5" width="18" height="15" rx="3"/>
              <path d="M9.5 4.5v15"/>
            </svg>
          </button>
        </template>
        <template v-else>
          <RouterLink to="/jobs" class="logo-link compact" aria-label="新百 CENBEST · 返回岗位管理" @click="expandSider">
            <svg class="logo-mark" viewBox="0 0 66 56" aria-hidden="true" focusable="false">
              <defs>
                <filter id="sidebar-brand-ink-compact" color-interpolation-filters="sRGB">
                  <feColorMatrix type="matrix" values="
                    0 0 0 0 1
                    0 0 0 0 .36
                    0 0 0 0 .40
                    0 -1 0 0 1" />
                </filter>
              </defs>
              <image href="/cenbest.png" width="129" height="56" filter="url(#sidebar-brand-ink-compact)" />
            </svg>
          </RouterLink>
          <button type="button" class="sider-expand-hover" title="展开侧边栏" aria-label="展开侧边栏" @click.stop="expandSider">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true" focusable="false">
              <rect x="3" y="4.5" width="18" height="15" rx="3"/>
              <path d="M9.5 4.5v15"/>
            </svg>
          </button>
        </template>
      </div>
      <nav class="side-nav" aria-label="工作区导航">
        <RouterLink v-for="item in menuItems" :key="item.key" :to="'/' + item.key" class="side-link"
          :class="{ current: activeMenu === item.key }" :aria-current="activeMenu === item.key ? 'page' : undefined"
          :aria-label="item.label" :title="collapsed ? item.label : undefined" @click="expandSider">
          <component :is="item.icon" class="side-icon" aria-hidden="true"/>
          <span v-if="!collapsed">{{ item.label }}</span>
        </RouterLink>
      </nav>
      <section v-if="!collapsed && (history.items.length || route.name === 'analysis')" class="side-history" aria-label="历史对话">
        <header class="side-history-head">
          <span>历史对话</span>
          <span v-if="history.items.length">{{ history.items.length }}</span>
        </header>
        <p v-if="!history.items.length" class="side-history-empty">暂无对话记录</p>
        <label v-else class="side-search">
          <SearchOutline aria-hidden="true"/>
          <input v-model="keyword" type="search" placeholder="搜索对话" aria-label="搜索历史对话">
        </label>
        <p v-if="history.items.length && !visibleFolders.length" class="side-history-empty">没有匹配的对话</p>
        <div v-for="folder in visibleFolders" :key="folder.key" class="side-folder">
          <button type="button" class="side-folder-head" :aria-expanded="!history.isCollapsed(folder.key)"
            :title="folder.sub ? folder.name + ' · ' + folder.sub : folder.name" @click="history.toggleFolder(folder.key)">
            <ChevronForwardOutline v-if="history.isCollapsed(folder.key) && !keyword.trim()" class="folder-caret" aria-hidden="true"/>
            <ChevronDownOutline v-else class="folder-caret" aria-hidden="true"/>
            <span class="folder-name">{{ folder.name }}</span>
            <b>{{ folder.items.length }}</b>
          </button>
          <template v-if="!history.isCollapsed(folder.key) || keyword.trim()">
            <div v-for="item in folder.items" :key="item.id" class="side-history-row"
              :data-active="activeHistory === item.id ? 'true' : undefined">
              <input v-if="renamingId === item.id" v-model="renameDraft" class="side-rename" maxlength="60"
                aria-label="重命名对话" @keydown.enter.prevent="commitRename" @keydown.esc="renamingId = ''" @blur="commitRename">
              <button v-else type="button" class="side-history-item"
                :aria-current="activeHistory === item.id ? 'true' : undefined" :title="item.question" @click="openHistory(item.id)">
                <span class="side-history-q">
                  <PinOutline v-if="item.pinned" class="pin-mark" aria-hidden="true"/>{{ item.title || item.question }}
                </span>
                <span v-if="item.summary" class="side-history-abstract">{{ item.summary }}</span>
                <span class="side-history-meta"><b>{{ historyTime(item.at) }}</b></span>
              </button>
              <button v-if="renamingId !== item.id" type="button" class="side-more" aria-label="更多操作"
                :aria-expanded="menuId === item.id" @click.stop="toggleMenu(item.id)"><EllipsisHorizontalOutline aria-hidden="true"/></button>
              <div v-if="menuId === item.id" class="side-menu" @click.stop>
                <button type="button" @click="pickPin(item.id)"><PinOutline aria-hidden="true"/><span>{{ item.pinned ? '取消置顶' : '置顶' }}</span></button>
                <button type="button" @click="startRename(item)"><CreateOutline aria-hidden="true"/><span>重命名</span></button>
                <button type="button" @click="copyLink(item.id)"><LinkOutline aria-hidden="true"/><span>{{ copiedMenuId === item.id ? '已复制链接' : '复制分享链接' }}</span></button>
                <button type="button" @click="pickArchive(item.id)"><ArchiveOutline aria-hidden="true"/><span>归档</span></button>
                <button type="button" class="danger" @click="requestDelete(item.id)"><TrashOutline aria-hidden="true"/><span>{{ pendingDelete === item.id ? '确认删除' : '删除' }}</span></button>
              </div>
            </div>
          </template>
        </div>

        <div v-if="history.archived.length" class="side-archive">
          <button type="button" class="side-folder-head" :aria-expanded="!history.isCollapsed('__archived')"
            @click="history.toggleFolder('__archived')">
            <ChevronDownOutline v-if="!history.isCollapsed('__archived')" class="folder-caret" aria-hidden="true"/>
            <ChevronForwardOutline v-else class="folder-caret" aria-hidden="true"/>
            <span class="folder-name">已归档</span>
            <b>{{ history.archived.length }}</b>
          </button>
          <template v-if="!history.isCollapsed('__archived')">
            <div v-for="item in history.archived" :key="item.id" class="side-history-row"
              :data-active="activeHistory === item.id ? 'true' : undefined">
              <button type="button" class="side-history-item is-archived"
                :aria-current="activeHistory === item.id ? 'true' : undefined" :title="item.question" @click="openHistory(item.id)">
                <span class="side-history-q">{{ item.title || item.question }}</span>
                <span v-if="item.summary" class="side-history-abstract">{{ item.summary }}</span>
                <span class="side-history-meta"><b>{{ historyTime(item.at) }}</b></span>
              </button>
              <button type="button" class="side-more" aria-label="更多操作" :aria-expanded="menuId === item.id"
                @click.stop="toggleMenu(item.id)"><EllipsisHorizontalOutline aria-hidden="true"/></button>
              <div v-if="menuId === item.id" class="side-menu" @click.stop>
                <button type="button" @click="pickUnarchive(item.id)"><ArrowUndoOutline aria-hidden="true"/><span>恢复</span></button>
                <button type="button" @click="copyLink(item.id)"><LinkOutline aria-hidden="true"/><span>{{ copiedMenuId === item.id ? '已复制链接' : '复制分享链接' }}</span></button>
                <button type="button" class="danger" @click="requestDelete(item.id)"><TrashOutline aria-hidden="true"/><span>{{ pendingDelete === item.id ? '确认删除' : '删除' }}</span></button>
              </div>
            </div>
          </template>
        </div>
      </section>
    </NLayoutSider>

    <!-- 主体 -->
    <NLayout>
      <NLayoutHeader bordered class="header">
        <div class="header-left">
          <span v-if="route.name !== 'analysis'" class="page-title">{{ (route.meta.title as string) ?? '' }}</span>
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
.sider.collapsed { cursor: pointer; }
.sider :deep(.n-layout-sider-scroll-container) {
  position: relative;
  z-index: 1;
  background: transparent;
}

.sider-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  flex: none;
  padding: 0;
  border: 1px solid rgba(215, 225, 229, .9);
  border-radius: 8px;
  background: rgba(255, 255, 255, .65);
  color: #3f5a68;
  cursor: pointer;
  transition: background-color var(--rs-motion-fast), border-color var(--rs-motion-fast), color var(--rs-motion-fast);
}
.sider-toggle svg { width: 17px; height: 17px; }
.sider-toggle:hover { color: var(--rs-primary); border-color: var(--rs-primary); background: var(--rs-primary-soft); }
.sider-toggle:focus-visible { outline: 2px solid var(--rs-primary); outline-offset: 2px; color: var(--rs-primary); }

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

.side-history {
  display: grid;
  gap: 2px;
  align-content: start;
  margin: 4px 10px 14px;
  padding: 10px 0 4px;
  border-top: 1px solid rgba(189, 226, 226, .14);
  max-height: calc(100dvh - 250px);
  overflow: auto;
  scrollbar-width: thin;
  scrollbar-color: rgba(189, 226, 226, .28) transparent;
}
.side-search {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 2px 2px 7px;
  padding: 5px 8px;
  border: 1px solid rgba(189, 226, 226, .18);
  border-radius: 7px;
  background: rgba(255, 255, 255, .05);
  color: #9fbdc1;
  transition: border-color var(--rs-motion-fast), background-color var(--rs-motion-fast);
}
.side-search svg { width: 13px; height: 13px; flex: none; opacity: .8; }
.side-search input {
  flex: 1;
  min-width: 0;
  border: 0;
  background: transparent;
  color: #e2f1f1;
  font: inherit;
  font-size: 12px;
  outline: none;
}
.side-search input::placeholder { color: #7c9ca3; }
.side-search:focus-within { border-color: rgba(123, 221, 211, .55); background: rgba(255, 255, 255, .08); }
.side-history-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 7px 7px;
  color: #86a9af;
  font-size: 11px;
  letter-spacing: .08em;
}
.side-history-empty {
  margin: 0;
  padding: 6px 8px 10px;
  color: #7c9ca3;
  font-size: 12px;
}
.side-history-item {
  display: grid;
  gap: 3px;
  width: 100%;
  padding: 8px 10px;
  border: 1px solid transparent;
  border-radius: 9px;
  background: transparent;
  color: #c9d9dd;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--rs-motion-fast), border-color var(--rs-motion-fast), color var(--rs-motion-fast);
}
.side-history-item:hover { background: rgba(255, 255, 255, .07); color: #fff; }
.side-history-item[aria-current=true] {
  border-color: rgba(99, 218, 204, .19);
  background: linear-gradient(90deg, rgba(32, 177, 162, .2), rgba(255, 255, 255, .04));
  color: #effffb;
}
.side-history-item:focus-visible { outline: 2px solid #7bddd3; outline-offset: -2px; }
.side-folder { display: grid; gap: 1px; }
.side-folder-head {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 7px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: #a6c1c5;
  font-size: 11.5px;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--rs-motion-fast), color var(--rs-motion-fast);
}
.side-folder-head:hover { background: rgba(255, 255, 255, .05); color: #e6f4f4; }
.side-folder-head b { margin-inline-start: auto; color: #7fa1a7; font-size: 10.5px; font-weight: 500; }
.folder-caret { width: 13px; height: 13px; flex: none; opacity: .8; }
.folder-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.side-folder .side-history-item { padding-inline-start: 6px; }
/* 左侧一列小横线，方便快速定位到每一条对话；当前项加粗。 */
.side-history-row { position: relative; padding-inline-start: 10px; }
.side-history-row::before {
  content: '';
  position: absolute;
  left: 0;
  top: 14px;
  width: 2px;
  height: 15px;
  border-radius: 2px;
  background: rgba(189, 226, 226, .3);
  transition: background-color var(--rs-motion-fast), height var(--rs-motion-fast);
}
.side-history-row[data-active=true]::before { top: 11px; width: 3px; height: 21px; background: #eafffb; }
.side-history-row .side-history-item { width: 100%; padding-inline-end: 24px; }
.side-history-q { display: flex; align-items: center; gap: 4px; color: #e4f0f0; font-weight: 560; }
.pin-mark { width: 11px; height: 11px; flex: none; color: #7bddd3; }
.side-history-abstract {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  color: #8fadb2;
  font-size: 11px;
  line-height: 1.5;
  white-space: normal;
  overflow-wrap: anywhere;
}
.side-more {
  position: absolute;
  top: 6px;
  right: 3px;
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #8fadb2;
  cursor: pointer;
  opacity: 0;
  transition: opacity var(--rs-motion-fast), background-color var(--rs-motion-fast), color var(--rs-motion-fast);
}
.side-history-row:hover .side-more,
.side-more[aria-expanded=true],
.side-more:focus-visible { opacity: 1; }
.side-more:hover { background: rgba(255, 255, 255, .12); color: #fff; }
.side-more svg { width: 15px; height: 15px; }
.side-menu {
  position: absolute;
  z-index: 30;
  top: 30px;
  right: 2px;
  display: grid;
  gap: 1px;
  width: 186px;
  padding: 5px;
  border: 1px solid rgba(189, 226, 226, .18);
  border-radius: 10px;
  background: #0f2d34;
  box-shadow: 0 16px 32px rgba(0, 0, 0, .42);
}
.side-menu button {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 9px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: #cfe3e3;
  font: inherit;
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}
.side-menu button:hover { background: rgba(255, 255, 255, .08); color: #fff; }
.side-menu button svg { width: 14px; height: 14px; flex: none; opacity: .85; }
.side-menu button.danger { color: #f0a3a3; }
.side-menu button.danger:hover { background: rgba(163, 59, 59, .3); color: #fff; }
.side-rename {
  width: 100%;
  padding: 7px 9px;
  border: 1px solid rgba(123, 221, 211, .55);
  border-radius: 7px;
  background: rgba(255, 255, 255, .08);
  color: #eaffff;
  font: inherit;
  font-size: 12.5px;
  outline: none;
}
.side-history-item.is-archived { opacity: .72; }
.side-archive {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed rgba(189, 226, 226, .16);
}
.side-history-q {
  overflow: hidden;
  font-size: 12.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.side-history-meta {
  display: flex;
  align-items: center;
  gap: 7px;
  color: #7fa1a7;
  font-size: 10.5px;
  min-width: 0;
}
.side-history-meta b { flex: none; font-weight: 500; font-variant-numeric: tabular-nums; }

.logo-bar {
  height: var(--rs-header-height);
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 10px 0 17px;
  border-bottom: 1px solid rgba(189, 226, 226, .14);
  overflow: hidden;
  color: inherit;
  background: rgba(7, 28, 35, .12);
}
.logo-link {
  display: flex;
  align-items: center;
  gap: 11px;
  min-width: 0;
  color: inherit;
  text-decoration: none;
}
.logo-link:focus-visible { outline: 2px solid #7bddd3; outline-offset: -2px; border-radius: 6px; }
/* 折叠开关放在侧栏内部（原来在顶部栏外侧） */
.sider-toggle.inside {
  margin-inline-start: auto;
  width: 28px;
  height: 28px;
  flex: none;
  border-color: rgba(189, 226, 226, .22);
  background: rgba(255, 255, 255, .07);
  color: #cfe3e3;
}
.sider-toggle.inside:hover {
  border-color: rgba(123, 221, 211, .45);
  background: rgba(255, 255, 255, .16);
  color: #fff;
}
.sider-toggle.inside svg { width: 15px; height: 15px; }
.logo-mark { display:block; flex:none; width:88px; height:auto; overflow:hidden; filter: drop-shadow(0 3px 8px rgba(0,0,0,.14)); }
.collapsed .logo-bar { position: relative; padding:0; justify-content:center; }
.collapsed .logo-link.compact { transition: opacity var(--rs-motion-fast); }
.collapsed .logo-bar:hover .logo-link.compact { opacity: 0; }
/* 收起态默认显示品牌图标，鼠标移上去才换成展开按钮 */
.sider-expand-hover {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  padding: 0;
  border: 1px solid rgba(189, 226, 226, .24);
  border-radius: 9px;
  background: rgba(255, 255, 255, .08);
  color: #d6e9ea;
  cursor: pointer;
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--rs-motion-fast), background-color var(--rs-motion-fast);
}
.collapsed .logo-bar:hover .sider-expand-hover,
.sider-expand-hover:focus-visible { opacity: 1; pointer-events: auto; }
.sider-expand-hover:hover { background: rgba(255, 255, 255, .2); color: #fff; }
.sider-expand-hover svg { width: 17px; height: 17px; }
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
