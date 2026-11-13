<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { DocumentTextOutline } from '@vicons/ionicons5'
import { api } from '@/api/client'
import { label, locationText, stageKey, stagesFor, type Detail, type Issue, type TransferJob } from '@/api/hr'

const route = useRoute(), router = useRouter()
const detail = ref<Detail>()
const transferJobs = ref<TransferJob[]>([])
const transferLoaded = ref(false)
const transferOpen = ref(false)
const reviewOpen = ref(false)
const evidenceOpen = ref(false)
const fullLoaded = ref(false)
const error = ref('')
const message = ref('')
const busy = ref(false)
const preview = ref('')
const picked = ref<Issue>()
const action = ref('correct')
const value = ref('')
const reason = ref('')
const nextStage = ref('review')
const available = computed(() => stagesFor(detail.value?.rounds))
const activeKey = computed(() => detail.value ? stageKey(detail.value.stage) : 'review')
const returnTo = computed(() => ({ name:'applications', query: detail.value ? { job:detail.value.job_id } : {} }))
const transfer = ref<number | ''>('')
const transferReason = ref('')
const purgeReason = ref('')
const id = String(route.params.id)
const base = '/hr/applications/' + id
async function load() {
  try {
    detail.value = await api.get<Detail>(base + (fullLoaded.value ? '' : '?include_evidence=false'))
    nextStage.value = stageKey(detail.value.stage)
  } catch (e) { error.value = (e as Error).message }
}
async function loadEvidence() {
  if (fullLoaded.value) return
  try {
    const full = await api.get<Detail>(base)
    if (detail.value) detail.value = { ...detail.value, evidence: full.evidence, issues: full.issues }
    fullLoaded.value = true
  } catch (e) { error.value = (e as Error).message }
}
async function toggleTransfer() {
  transferOpen.value = !transferOpen.value
  if (!transferOpen.value || transferLoaded.value) return
  try {
    transferJobs.value = (await api.get<{ items: TransferJob[] }>(base + '/transfer-options')).items
    transferLoaded.value = true
  } catch (e) { error.value = (e as Error).message }
}
onMounted(async () => {
  await load()
  if (route.hash === "#transfer") {
    await toggleTransfer()
  } else if (route.hash) {
    reviewOpen.value = route.hash === '#review'
    evidenceOpen.value = route.hash.startsWith('#span-')
    await loadEvidence(); await nextTick()
    document.getElementById(route.hash.slice(1))?.scrollIntoView()
  }
})
onUnmounted(() => { if (preview.value) URL.revokeObjectURL(preview.value) })
async function operation(task: () => Promise<unknown>, success: string, reload = true) {
  busy.value = true; error.value = ''; message.value = ''
  try { await task(); message.value = success; if (reload) await load() }
  catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
async function updateStage() {
  const current = detail.value
  const target = available.value.find(item => stageKey(item) === nextStage.value)
  if (!current || !target || nextStage.value === activeKey.value) return
  await operation(async () => {
    const updated = await api.put<Pick<Detail, 'stage' | 'workflow_version'>>(base + '/stage', {
      code:target.code, round_id:target.round_id, workflow_version:current.workflow_version,
    })
    if (detail.value) detail.value = { ...detail.value, ...updated }
    nextStage.value = stageKey(updated.stage)
  }, '状态已更新。', false)
}
async function moveApplication() {
  const current = detail.value
  if (!current || !transfer.value) return
  await operation(async () => {
    const moved = await api.post<{ job_id:number; remains_visible:boolean }>(base + '/transfer', {
      job_id:transfer.value, reason:transferReason.value, workflow_version:current.workflow_version,
    })
    if (!moved.remains_visible) {
      await router.push({ name:'applications', query:{ job:current.job_id, transferred:'1' } })
      return
    }
    transfer.value = ''; transferReason.value = ''; transferLoaded.value = false; transferOpen.value = false
    await load()
  }, '已调剂，状态回到待审核。', false)
}
function select(issue: Issue) { picked.value = issue; value.value = ''; reason.value = ''; action.value = issue.field_key === 'extension' ? 'dismiss' : 'correct' }
async function save() {
  const issue = picked.value
  if (!issue || !detail.value?.active_version_id) return
  await operation(async () => {
    await api.post(base + '/corrections', { version_id: detail.value?.active_version_id, record_no: issue.record_no, field_key: issue.field_key, action: action.value, value: value.value, reason: reason.value })
    picked.value = undefined
  }, '复核结果已发布。')
}
async function purge() {
  if (!window.confirm('确认永久清理此申请及无其他申请引用的简历资料？')) return
  await operation(async () => {
    await api.post(base + '/purge', { reason: purgeReason.value })
    await router.push('/applications')
  }, '', false)
}
function hidePreview() {
  if (preview.value) URL.revokeObjectURL(preview.value)
  preview.value = ''
}
async function showPreview() {
  await operation(async () => {
    if (preview.value) URL.revokeObjectURL(preview.value)
    preview.value = URL.createObjectURL(await api.get<Blob>(base + '/preview'))
  }, '', false)
}
</script>

<template>
  <main class="recruit-workspace rs-workspace application-detail">
    <header class="workspace-heading">
      <div><RouterLink class="workspace-kicker detail-back" :to="returnTo">‹ 返回简历列表</RouterLink>
        <h1>{{ detail?.name || '申请 #' + id }}</h1><p v-if="detail">{{ detail.job_title }}</p></div>
      <div class="heading-actions"><button class="rs-button" :disabled="busy" @click="load">刷新</button></div>
    </header>
    <p v-if="error" class="workspace-notice danger" role="alert">{{ error }}</p>
    <p v-if="message" class="workspace-notice" role="status">{{ message }}</p>
    <template v-if="detail">
      <section class="workspace-surface stage-panel" aria-label="当前招聘状态">
        <div class="stage-summary">
          <div class="stage-mark" aria-hidden="true"><DocumentTextOutline/></div>
          <div><p class="stage-caption">当前招聘状态</p><h2 class="workspace-chip stage-display" :class="detail.stage.code">{{ detail.stage.label }}</h2></div>
        </div>
        <form class="rs-form" @submit.prevent="updateStage">
          <label>更新状态<select v-model="nextStage"><option v-for="item in available" :key="stageKey(item)" :value="stageKey(item)">{{ item.label }}</option></select></label>
          <button class="rs-button primary" :disabled="busy || nextStage === activeKey">保存</button>
        </form>
      </section>
      <section class="workspace-surface rs-panel detail-actions">
        <div class="detail-tools">
          <button class="rs-button transfer-button" type="button" :aria-expanded="transferOpen" @click="toggleTransfer">调剂岗位</button>
          <button v-if="detail.has_preview && !preview" class="rs-button" @click="showPreview">查看脱敏预览</button>
          <button v-if="preview" class="rs-button" @click="hidePreview">收起预览</button>
          <button class="rs-button" :aria-expanded="reviewOpen" @click="reviewOpen = !reviewOpen; reviewOpen && loadEvidence()">字段复核</button>
          <button class="rs-button" :aria-expanded="evidenceOpen" @click="evidenceOpen = !evidenceOpen; evidenceOpen && loadEvidence()">原文证据</button>
        </div>
        <form v-if="transferOpen" class="rs-form detail-form" @submit.prevent="moveApplication">
          <label>目标岗位<select v-model="transfer" required><option value="">选择招聘中的岗位</option><option v-for="job in transferJobs" :key="job.id" :value="job.id">{{ job.department_name }} · {{ job.title }}</option></select></label>
          <label class="grow">调剂说明<input v-model="transferReason" maxlength="300" required></label>
          <button class="rs-button primary" :disabled="busy || !transfer">确认调剂</button>
          <p class="rs-muted transfer-hint">简历将进入目标岗位的待审核阶段。</p>
        </form>
        <img v-if="preview" class="rs-preview" :src="preview" alt="脱敏简历首页">
      </section>
      <section v-if="reviewOpen" id="review" class="workspace-surface rs-panel">
        <h2>待复核字段</h2>
        <p v-if="!detail.issues.length" class="rs-empty">{{ fullLoaded ? '当前版本没有待复核字段。' : '正在加载。' }}</p>
        <table v-else class="rs-table"><thead><tr><th>字段</th><th>原因</th><th>原文与位置</th><th>操作</th></tr></thead><tbody>
          <tr v-for="issue in detail.issues" :key="issue.record_no + ':' + issue.field_key"><td>{{ issue.label }}</td><td>{{ label(issue.reason) }}</td>
            <td>{{ issue.evidence || '暂无可展示的原文' }} <small v-if="issue.locator">· {{ locationText(issue.locator) }}</small></td>
            <td><button class="rs-button" @click="select(issue)">复核</button></td></tr>
        </tbody></table>
        <form v-if="picked" class="rs-form" @submit.prevent="save">
          <label>处理 {{ picked.label }}<select v-model="action"><option v-if="picked.field_key !== 'extension'" value="correct">更正字段</option><option value="dismiss">{{ picked.field_key === 'extension' ? '保留原值' : '证据不足，关闭待办' }}</option></select></label>
          <label v-if="action === 'correct'" class="grow">核实后的值<input v-model="value" required></label><label class="grow">处理说明<input v-model="reason" required></label>
          <button class="rs-button primary" :disabled="busy">提交复核</button>
        </form>
      </section>
      <section v-if="evidenceOpen" class="workspace-surface rs-panel">
        <h2>字段与原文证据</h2>
        <p v-if="!detail.evidence.length" class="rs-muted">{{ fullLoaded ? '当前版本暂无可查看的字段证据。' : '正在加载。' }}</p>
        <div class="rs-evidence"><article v-for="entry in detail.evidence" :id="'span-' + entry.span_no" :key="entry.record_no + ':' + entry.field_key + ':' + entry.span_no">
          <strong>{{ label(entry.field_key) }}</strong> · {{ locationText(entry.locator) }}<p>{{ entry.text }}</p>
        </article></div>
      </section>
      <details class="workspace-surface rs-panel">
        <summary>更多操作</summary>
        <div class="detail-tools detail-form">
          <button class="rs-button" :disabled="busy || detail.parse_status === 'parsing'" @click="operation(() => api.post(base + '/reparse'), '已提交重新解析。')">重新解析</button>
          <button class="rs-button" @click="operation(() => api.download(base + '/original'), '原件已下载。', false)">下载已授权原件</button>
        </div>
        <p v-if="detail.fail_reason" class="rs-error">{{ detail.fail_reason }}</p>
        <form class="rs-form detail-form" @submit.prevent="purge">
          <label class="grow">清理理由<input v-model="purgeReason" required></label><button class="rs-button danger" :disabled="busy">永久清理</button>
        </form>
      </details>
    </template>
  </main>
</template>

<style scoped>
.application-detail { gap:12px; align-content:start; }
.detail-back { text-decoration:none; }
.detail-back:hover { text-decoration:underline; }
.stage-panel { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:16px; min-height:110px; padding:14px 15px; border-radius:16px; box-shadow:0 2px 9px #1a345506; }
.stage-summary { display:flex; align-items:center; gap:15px; min-width:0; }
.stage-mark { flex:none; width:54px; height:54px; display:grid; place-items:center; border-radius:15px; background:linear-gradient(145deg,#e6eeff,#d8e5fa); color:#36568e; }
.stage-mark svg { width:24px; height:24px; }
.stage-caption { color:#596982; font-size:13px; margin:0 0 6px; }
.stage-display { margin:0; font-size:15px; padding:5px 9px; white-space:normal; overflow-wrap:anywhere; }
.stage-panel select,.detail-form select { max-width: min(100%, 420px); }
.detail-tools { display:flex; flex-wrap:wrap; gap:8px; }
.detail-actions { padding:12px 15px; }
.transfer-button { color:#3858a1; background:#f9fbff; }
.detail-form { margin-top:14px; }
.transfer-hint { flex-basis:100%; margin:0; font-size:12px; }
.rs-preview { display:block; margin:14px auto 0; border-radius:9px; }
.application-detail summary { cursor:pointer; font-size:14px; color:#495d78; }
@media (max-width:680px) {
  .stage-panel { padding:12px; }
  .stage-mark { width:42px; height:42px; border-radius:11px; }
}
</style>
