<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, computed } from 'vue'
import {
  ChevronDownOutline, CloseOutline, PencilOutline, CameraOutline, LogOutOutline,
  MailOutline, CallOutline, LocationOutline, CalendarOutline, BusinessOutline,
} from '@vicons/ionicons5'
import { useMessage } from 'naive-ui'
import { api } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

interface Profile {
  display_name: string; job_title: string; phone: string; office: string; bio: string
  email: string; departments: string[]; role: string; created_at: string; has_avatar: boolean
}
const auth = useAuthStore()
const messages = useMessage()
const emit = defineEmits<{ logout: [] }>()
const trigger = ref<HTMLButtonElement>()
const dialog = ref<HTMLDialogElement>()
const closeButton = ref<HTMLButtonElement>()
const nameInput = ref<HTMLInputElement>()
const editButton = ref<HTMLButtonElement>()
const fileInput = ref<HTMLInputElement>()
const profile = ref<Profile>()
const draft = ref<Pick<Profile, 'display_name' | 'job_title' | 'phone' | 'office' | 'bio'>>()
const avatar = ref('')
const preview = ref('')
const file = ref<File>()
const removeAvatar = ref(false)
const open = ref(false)
const editing = ref(false)
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const avatarError = ref('')
const name = computed(() => profile.value?.display_name || auth.session?.displayName || '个人资料')
const initials = computed(() => Array.from(name.value.trim()).slice(0, 1).join(''))
const role = computed(() => auth.isSuperAdmin ? '超级管理员' : '部门 HR')
const shownAvatar = computed(() => editing.value ? preview.value || (removeAvatar.value ? '' : avatar.value) : avatar.value)
const details = computed(() => profile.value ? [
  { label:'企业邮箱', value:profile.value.email, icon:MailOutline },
  { label:'所属部门', value:profile.value.departments.join('、') || '—', icon:BusinessOutline },
  { label:'工作电话', value:profile.value.phone || '未填写', icon:CallOutline },
  { label:'办公地点', value:profile.value.office || '未填写', icon:LocationOutline },
  { label:'账号创建', value: new Intl.DateTimeFormat('zh-CN', { dateStyle:'long', timeZone:'Asia/Shanghai' }).format(new Date(profile.value.created_at)), icon:CalendarOutline },
] : [])
let alive = true
let avatarRequest = 0
function revoke(value: string) { if (value) URL.revokeObjectURL(value) }
async function loadAvatar(hasAvatar: boolean) {
  const request = ++avatarRequest
  if (!hasAvatar) { revoke(avatar.value); avatar.value = ''; return }
  try {
    const blob = await api.get<Blob>('/auth/profile/avatar')
    if (!alive || request !== avatarRequest) return
    revoke(avatar.value)
    avatar.value = URL.createObjectURL(blob)
  } catch {
    if (alive && request === avatarRequest) { revoke(avatar.value); avatar.value = '' }
  }
}
function applyProfile(data: Profile) {
  profile.value = data
  const session = auth.session
  if (session && session.displayName !== data.display_name) auth.setSession({ ...session, displayName:data.display_name })
  void loadAvatar(data.has_avatar)
}
async function load() {
  if (loading.value) return
  error.value = ''; loading.value = true
  try {
    const data = await api.get<Profile>('/auth/profile')
    if (alive) applyProfile(data)
  } catch (e) { if (alive) error.value = (e as Error).message }
  finally { loading.value = false }
}
onMounted(load)
onUnmounted(() => { alive = false; revoke(avatar.value); revoke(preview.value) })

function show() {
  dialog.value?.showModal()
  open.value = true
  closeButton.value?.focus()
  if (!profile.value) void load()
}
function dismiss() {
  if (saving.value) return
  dialog.value?.close()
}
function clearDraft() {
  revoke(preview.value); preview.value = ''; file.value = undefined
  removeAvatar.value = false; avatarError.value = ''; draft.value = undefined
  if (fileInput.value) fileInput.value.value = ''
}
function closed() {
  open.value = false; editing.value = false; clearDraft()
  trigger.value?.focus()
}
async function edit() {
  if (!profile.value) return
  const {display_name,job_title,phone,office,bio} = profile.value
  draft.value = {display_name,job_title,phone,office,bio}
  editing.value = true; error.value = ''
  await nextTick()
  nameInput.value?.focus()
}
async function cancel() {
  editing.value = false; error.value = ''; clearDraft()
  await nextTick()
  editButton.value?.focus()
}
function pickAvatar(event: Event) {
  const input = event.target as HTMLInputElement
  const next = input.files?.[0]
  input.value = ''
  if (!next) return
  avatarError.value = ''
  if (!['image/jpeg','image/png','image/webp'].includes(next.type) || next.size > 2 * 1024 * 1024) {
    avatarError.value = '请选择不超过 2 MB 的 JPG、PNG 或 WebP 图片。'
    return
  }
  revoke(preview.value); preview.value = URL.createObjectURL(next)
  file.value = next; removeAvatar.value = false
}
function clearAvatar() {
  revoke(preview.value); preview.value = ''; file.value = undefined
  removeAvatar.value = true; avatarError.value = ''
}
async function save() {
  if (!draft.value || saving.value) return
  error.value = ''; avatarError.value = ''
  if (!draft.value.display_name.trim()) {
    error.value = '请输入显示姓名。'
    nameInput.value?.focus()
    return
  }
  const body = new FormData()
  for (const [key,value] of Object.entries(draft.value)) body.set(key,value.trim())
  if (file.value) body.set('avatar',file.value)
  if (removeAvatar.value) body.set('remove_avatar','true')
  saving.value = true
  try {
    const data = await api.upload<Profile>('/auth/profile',body)
    if (!alive) return
    applyProfile(data)
    await cancel()
    messages.success('个人资料已保存')
  } catch (e) { error.value = (e as Error).message }
  finally { saving.value = false }
}
function logout() {
  dismiss()
  emit('logout')
}
</script>

<template>
  <button ref="trigger" type="button" class="profile-trigger" aria-label="打开个人资料"
    aria-haspopup="dialog" :aria-expanded="open" @click="show">
    <span class="account-avatar small" aria-hidden="true"><img v-if="avatar" :src="avatar" alt=""><span v-else>{{ initials }}</span></span>
    <span class="account-meta"><strong>{{ name }}</strong><small>{{ role }}</small></span>
    <ChevronDownOutline class="profile-chevron" aria-hidden="true"/>
  </button>

  <Teleport to="body">
    <dialog ref="dialog" class="profile-dialog" aria-labelledby="profile-title" @close="closed" @cancel.prevent="dismiss" @click.self="dismiss">
      <section class="profile-panel">
        <header class="profile-heading">
          <h2 id="profile-title">{{ editing ? '编辑个人资料' : '个人资料' }}</h2>
          <button ref="closeButton" type="button" class="profile-icon" aria-label="关闭个人资料" :disabled="saving" @click="dismiss"><CloseOutline aria-hidden="true"/></button>
        </header>
        <div class="profile-body">
          <div class="profile-identity">
            <span class="account-avatar large" aria-hidden="true"><img v-if="shownAvatar" :src="shownAvatar" alt=""><span v-else>{{ initials }}</span></span>
            <div v-if="!editing" class="identity-copy">
              <h3>{{ name }}</h3><p v-if="profile?.job_title">{{ profile.job_title }}</p><span class="account-role">{{ role }}</span>
            </div>
            <div v-else class="avatar-controls">
              <input ref="fileInput" type="file" accept="image/jpeg,image/png,image/webp" hidden @change="pickAvatar">
              <button type="button" class="rs-button" :disabled="saving" @click="fileInput?.click()"><CameraOutline aria-hidden="true"/>更换头像</button>
              <button v-if="shownAvatar" type="button" class="profile-text-button" :disabled="saving" @click="clearAvatar">移除</button>
              <p>{{ file?.name || 'JPG、PNG 或 WebP，最多 2 MB' }}</p>
              <p v-if="avatarError" class="profile-error" role="alert">{{ avatarError }}</p>
            </div>
          </div>
          <p v-if="error && !editing" class="profile-error" role="alert">{{ error }} <button type="button" class="profile-text-button" @click="load">重试</button></p>
          <p v-if="loading && !profile" class="profile-loading" role="status">正在加载个人资料…</p>
          <template v-if="profile && !editing">
            <dl class="profile-facts">
              <div v-for="item in details" :key="item.label">
                <dt><component :is="item.icon" aria-hidden="true"/>{{ item.label }}</dt><dd>{{ item.value }}</dd>
              </div>
            </dl>
            <div v-if="profile.bio" class="profile-bio"><h4>个人简介</h4><p>{{ profile.bio }}</p></div>
          </template>
          <form v-if="editing && draft && profile" id="profile-edit" class="profile-form" @submit.prevent="save">
            <fieldset :disabled="saving">
              <label>显示姓名<input ref="nameInput" v-model="draft.display_name" maxlength="80" required :aria-invalid="error === '请输入显示姓名。'" :aria-describedby="error ? 'profile-save-error' : undefined"></label>
              <label>工作职务<input v-model="draft.job_title" maxlength="80"></label>
              <label>工作电话<input v-model="draft.phone" type="tel" maxlength="32"></label>
              <label>办公地点<input v-model="draft.office" maxlength="80"></label>
              <label class="span-two">个人简介<textarea v-model="draft.bio" maxlength="300" rows="3"></textarea></label>
            </fieldset>
            <p class="profile-hint">企业邮箱、角色和部门由管理员维护。</p>
            <p v-if="error" id="profile-save-error" class="profile-error" role="alert">{{ error }}</p>
          </form>
        </div>
        <footer class="profile-actions">
          <template v-if="editing">
            <button type="button" class="rs-button" :disabled="saving" @click="cancel">取消</button>
            <button type="submit" form="profile-edit" class="rs-button primary" :disabled="saving">{{ saving ? '正在保存…' : '保存资料' }}</button>
          </template>
          <template v-else>
            <button type="button" class="profile-text-button logout" @click="logout"><LogOutOutline aria-hidden="true"/>退出登录</button>
            <button ref="editButton" type="button" class="rs-button primary" :disabled="!profile" @click="edit"><PencilOutline aria-hidden="true"/>编辑资料</button>
          </template>
        </footer>
      </section>
    </dialog>
  </Teleport>
</template>

<style scoped>
.profile-trigger { display:flex; align-items:center; gap:9px; min-height:44px; padding:4px 9px 4px 6px; border:1px solid transparent; border-radius:var(--rs-radius-sm); background:transparent; color:var(--rs-text); cursor:pointer; }
.profile-trigger:hover,.profile-trigger[aria-expanded=true] { border-color:var(--rs-border); background:var(--rs-bg); }
.account-avatar { display:grid; place-items:center; flex:none; overflow:hidden; border-radius:50%; background:#20395e; color:#fff; font-weight:700; }
.account-avatar.small { width:30px; height:30px; }
.account-avatar.large { width:64px; height:64px; border:3px solid #fff; box-shadow:0 0 0 1px #dbe4f1; font-size:24px; }
.account-avatar img { width:100%; height:100%; object-fit:cover; }
.account-meta { display:grid; text-align:left; gap:2px; line-height:1.25; }
.account-meta strong { max-width:18ch; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:13px; }
.account-meta small { color:var(--rs-text-secondary); font-size:11px; }
.profile-chevron { width:14px; height:14px; color:var(--rs-text-secondary); }
.profile-dialog { position:fixed; inset:0; width:100%; height:100%; max-width:none; max-height:none; margin:0; border:0; background:transparent; padding:0; color:var(--rs-text); overflow:hidden; }
.profile-dialog:not([open]) { display:none; }
.profile-dialog::backdrop { background:#0b19353b; }
.profile-panel { position:absolute; inset-block-start:calc(var(--rs-header-height) + 6px); inset-inline-end:24px; display:grid; grid-template-rows:auto minmax(0,1fr) auto; width:min(426px,calc(100% - 32px)); max-height:calc(100dvh - var(--rs-header-height) - 18px); border:1px solid var(--rs-border); border-radius:16px; box-shadow:var(--rs-shadow-lg); background:var(--rs-bg-card); overflow:hidden; animation:profile-in var(--rs-motion-panel) ease-out both; }
@keyframes profile-in { from { opacity:0; transform:translateY(-5px) scale(.99); } to { opacity:1; transform:none; } }
.profile-heading,.profile-actions { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:10px 16px; }
.profile-heading { border-bottom:1px solid var(--rs-border); }
.profile-heading h2 { font-size:16px; }
.profile-icon { display:grid; place-items:center; width:44px; height:44px; border:0; border-radius:8px; color:var(--rs-text-secondary); background:transparent; cursor:pointer; }
.profile-icon:hover { background:var(--rs-bg); }
.profile-icon svg { width:21px; height:21px; }
.profile-body { min-height:0; overflow:auto; padding:16px; }
.profile-identity { display:flex; align-items:center; gap:14px; background:#edf2fa; padding:14px; border-radius:10px; }
.identity-copy { min-width:0; overflow-wrap:anywhere; }
.identity-copy h3 { font-size:19px; line-height:1.35; }
.identity-copy p,.avatar-controls p { color:#4b5a71; font-size:12px; margin-top:4px; }
.account-role { display:inline-block; margin-top:6px; color:#244579; font-size:11px; font-weight:650; }
.profile-facts { margin-top:10px; }
.profile-facts > div { display:grid; grid-template-columns:104px minmax(0,1fr); gap:8px; align-items:start; border-bottom:1px solid var(--rs-border); padding:9px 2px; font-size:13px; }
.profile-facts dt { display:flex; align-items:center; gap:7px; color:var(--rs-text-secondary); }
.profile-facts svg,.profile-actions svg,.avatar-controls svg { width:16px; height:16px; flex:none; }
.profile-facts dd,.profile-bio p,.avatar-controls p { overflow-wrap:anywhere; }
.profile-bio { margin-top:12px; font-size:13px; }
.profile-bio p { color:var(--rs-text-secondary); white-space:pre-wrap; margin-top:3px; }
.avatar-controls { min-width:0; }
.profile-form { margin-top:13px; }
.profile-form fieldset { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; border:0; }
.profile-form label { display:grid; gap:4px; font-size:13px; font-weight:600; }
.profile-form input,.profile-form textarea { width:100%; min-height:40px; border:1px solid #b7c4d8; background:white; border-radius:7px; padding:8px; font:inherit; font-weight:400; color:var(--rs-text); }
.profile-form textarea { resize:vertical; }
.span-two { grid-column:1 / -1; }
.profile-actions { border-top:1px solid var(--rs-border); background:#f9fbfe; }
.profile-actions .rs-button,.avatar-controls .rs-button { min-height:44px; gap:6px; }
.profile-text-button { display:inline-flex; align-items:center; gap:7px; min-height:44px; border:0; padding:0 7px; border-radius:7px; background:transparent; color:#345078; cursor:pointer; font:inherit; font-size:13px; }
.profile-text-button:hover { background:#eaf0f9; }
.profile-text-button.logout { color:#596881; }
.profile-hint,.profile-loading,.profile-error { color:var(--rs-text-secondary); margin:8px 0 0; font-size:12px; }
.profile-error,.avatar-controls .profile-error { color:#9c252b; }
@media(max-width:600px) { .profile-panel { inset-inline-end:12px; width:calc(100% - 24px); } .account-meta { display:none; } }
@media(max-width:355px) { .profile-form fieldset { grid-template-columns:minmax(0,1fr); } }
@media(prefers-reduced-motion:reduce) { .profile-panel { animation:none; } }
</style>
