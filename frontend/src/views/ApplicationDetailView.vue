<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api/client'
import { dateLabel, label, locationText, type Detail, type Issue, type Options } from '@/api/hr'

const route = useRoute(), router = useRouter()
const detail = ref<Detail>()
const options = ref<Options>()
const error = ref('')
const message = ref('')
const busy = ref(false)
const preview = ref('')
const picked = ref<Issue>()
const action = ref('correct')
const value = ref('')
const reason = ref('')
const decision = ref('advance')
const comment = ref('')
const transfer = ref<number | ''>('')
const transferReason = ref('')
const purgeReason = ref('')
const id = String(route.params.id)
const base = '/hr/applications/' + id
async function load() {
  try { detail.value = await api.get<Detail>(base) } catch (e) { error.value = (e as Error).message }
}
onMounted(async () => {
  await load()
  try { options.value = await api.get<Options>('/hr/options') } catch (e) { error.value = (e as Error).message }
  if (route.hash) { await nextTick(); document.getElementById(route.hash.slice(1))?.scrollIntoView() }
})
onUnmounted(() => { if (preview.value) URL.revokeObjectURL(preview.value) })
async function operation(task: () => Promise<unknown>, success: string, reload = true) {
  busy.value = true; error.value = ''; message.value = ''
  try { await task(); message.value = success; if (reload) await load() }
  catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
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
async function showPreview() {
  await operation(async () => {
    if (preview.value) URL.revokeObjectURL(preview.value)
    preview.value = URL.createObjectURL(await api.get<Blob>(base + '/preview'))
  }, '', false)
}
</script>

<template>
  <main class="rs-page rs-workspace">
    <header class="rs-heading"><div><RouterLink class="inline" to="/applications">返回申请列表</RouterLink><h1>{{ detail?.name || '申请 #' + id }}</h1><p v-if="detail">{{ detail.job_title }} · {{ label(detail.status) }}</p></div><button class="rs-button" @click="load">刷新</button></header>
    <p v-if="error" class="rs-error" role="alert">{{ error }}</p><p v-if="message" class="rs-message" role="status">{{ message }}</p>
    <template v-if="detail">
      <section class="rs-card rs-panel rs-workspace">
        <div class="rs-actions"><span class="rs-badge">{{ label(detail.parse_status) }}</span>
          <button class="rs-button" :disabled="busy || detail.parse_status === 'parsing'" @click="operation(() => api.post(base + '/reparse'), '已提交重新解析。')">重新解析</button>
          <button v-if="detail.has_preview && !preview" class="rs-button" @click="showPreview">打开脱敏预览</button>
          <button class="rs-button" @click="operation(() => api.download(base + '/original'), '原件已下载。', false)">下载已授权原件</button>
        </div>
        <p v-if="detail.fail_reason" class="rs-error">{{ detail.fail_reason }}</p>
        <img v-if="preview" class="rs-preview" :src="preview" alt="遮蔽敏感区域后的简历首页">
        <h2>字段与原文证据</h2>
        <p v-if="!detail.evidence.length" class="rs-muted">当前版本暂无可查看的字段证据。</p>
        <div class="rs-evidence"><article v-for="entry in detail.evidence" :id="'span-' + entry.span_no" :key="entry.record_no + ':' + entry.field_key + ':' + entry.span_no">
          <strong>{{ label(entry.field_key) }}</strong> · {{ locationText(entry.locator) }}<p>{{ entry.text }}</p>
        </article></div>
      </section>
      <section id="review" class="rs-card rs-panel">
        <h2>待复核字段</h2>
        <p v-if="!detail.issues.length" class="rs-empty">当前版本没有待复核字段。</p>
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
      <section v-if="detail.active_version_id" class="rs-card rs-panel">
        <h2>招聘决定</h2>
        <form class="rs-form" @submit.prevent="operation(() => api.post(base + '/decisions', { version_id: detail?.active_version_id, decision, comment }), '招聘决定已记录。')">
          <label>决定<select v-model="decision"><option value="advance">推进</option><option value="hold">暂缓</option><option value="reject">不推进</option></select></label>
          <label class="grow">理由<input v-model="comment" required></label><button class="rs-button primary" :disabled="busy">记录决定</button>
        </form>
        <p v-for="item in detail.reviews" :key="item.created_at" class="rs-empty">{{ dateLabel(item.created_at) }} · {{ label(item.decision) }} · {{ item.comment }}</p>
      </section>
      <details class="rs-card rs-panel">
        <summary>移交与清理</summary>
        <form class="rs-form" @submit.prevent="operation(async () => { await api.post(base + '/transfer', { department_id: transfer, reason: transferReason }); await router.push('/applications') }, '已移交。', false)">
          <label>接收部门<select v-model="transfer" required><option value="">选择部门</option><option v-for="dept in options?.departments.filter(d => d.id !== detail?.handling_department_id)" :key="dept.id" :value="dept.id">{{ dept.name }}</option></select></label>
          <label class="grow">移交理由<input v-model="transferReason" required></label><button class="rs-button" :disabled="busy">移交申请</button>
        </form>
        <form class="rs-form" @submit.prevent="purge">
          <label class="grow">清理理由<input v-model="purgeReason" required></label><button class="rs-button danger" :disabled="busy">永久清理</button>
        </form>
      </details>
    </template>
  </main>
</template>
