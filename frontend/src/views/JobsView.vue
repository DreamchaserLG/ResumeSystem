<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useMessage } from 'naive-ui'
import { api } from '@/api/client'
import { jobStatusLabel, jobStatusOptions, loadJobs, type Job, type Options } from '@/api/hr'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()
const jobs = ref<Job[]>([])
const options = ref<Options>()
const edit = ref<number | null>()
const form = reactive({ title: '', jd: '', status: 'paused', department_id: 0, planned_headcount: null as number | null, target_date: '', rounds: [] as { id?: number; name: string }[] })
const editor = ref<HTMLFormElement>()
const error = ref('')
const toast = useMessage()
const busy = ref(false)
const statusFilter = ref(typeof route.query.status === 'string' && jobStatusOptions.some(s=>s.value === route.query.status) ? route.query.status : '')
const visible = computed(() => jobs.value.filter(j => (!statusFilter.value || statusFilter.value === j.status) && (!route.query.job || String(j.id) === route.query.job) && (!route.query.department || String(j.department_id) === route.query.department)))
async function choose(job?: Job) {
  error.value = ''
  edit.value = job?.id ?? null
  Object.assign(form, {
    title: job?.title ?? '', jd: job?.jd ?? '', status: job?.status ?? 'paused',
    department_id: job?.department_id ?? options.value?.hr_department_ids[0] ?? 0,
    planned_headcount: job?.planned_headcount ?? null, target_date: job?.target_date ?? '',
    rounds: job ? job.rounds.map(r => ({ id:r.id, name:r.name })) : ['一面','二面','终面'].map(name => ({ name })),
  })
  await nextTick()
  editor.value?.scrollIntoView({ block: 'nearest' })
  editor.value?.querySelector('input')?.focus()
}
async function load() {
  jobs.value = await loadJobs()
  if (!auth.isSuperAdmin) options.value = await api.get<Options>('/hr/options')
}
onMounted(async () => { try {
  await load()
  const toEdit = jobs.value.find(j => String(j.id) === route.query.edit && j.manageable)
  if (toEdit && !auth.isSuperAdmin) await choose(toEdit)
} catch (e) { error.value = (e as Error).message } })
function move(i: number, delta: number) {
  const target = i + delta
  if (target < 0 || target >= form.rounds.length) return
  const [round] = form.rounds.splice(i, 1)
  form.rounds.splice(target, 0, round)
}
async function submit() {
  busy.value = true; error.value = ''
  try {
    const payload = { ...form, planned_headcount: form.planned_headcount || null, target_date: form.target_date || null }
    if (edit.value) await api.put('/hr/jobs/' + edit.value, payload)
    else await api.post('/hr/jobs', payload)
    toast.success(edit.value ? '岗位已更新。' : '岗位已创建。')
    edit.value = undefined
    await load()
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
</script>

<template>
  <main class="rs-page rs-workspace jobs-page">
    <header class="rs-heading"><div><h1>岗位管理</h1><p>查看岗位与招聘状态</p></div>
      <button v-if="!auth.isSuperAdmin" class="rs-button" @click="choose()">新建岗位</button></header>
    <p v-if="error" class="rs-error" role="alert">{{ error }}</p>
    <form v-if="!auth.isSuperAdmin && edit !== undefined" ref="editor" class="rs-card rs-panel job-editor" @submit.prevent="submit">
      <h2>{{ edit ? '编辑岗位' : '新建岗位' }}</h2>
      <div class="rs-form">
        <label class="grow">岗位名称<input v-model="form.title" maxlength="120" required></label>
        <label>所属部门<select v-model="form.department_id" :disabled="edit !== null" required><option v-for="d in options?.departments.filter(x => options?.hr_department_ids.includes(x.id))" :key="d.id" :value="d.id">{{ d.name }}</option></select></label>
        <label>招聘状态<select v-model="form.status"><option v-for="status in jobStatusOptions" :key="status.value" :value="status.value">{{ status.label }}</option></select></label>
      </div>
      <div class="rs-form">
        <label>计划招聘人数<input v-model.number="form.planned_headcount" type="number" min="1" max="100000" placeholder="未设置"></label>
        <label>目标完成日期<input v-model="form.target_date" type="date"></label>
      </div>
      <label class="jd-field">职位描述<textarea v-model="form.jd" maxlength="4000" rows="2"></textarea></label>
      <fieldset>
        <legend>面试轮次</legend>
        <p class="rs-muted">可以添加、改名或调整轮次；使用「终面」时需放在最后。不需要面试的岗位可留空。</p>
        <div v-for="(round,i) in form.rounds" :key="i" class="round-editor">
          <span>{{ i + 1 }}</span>
          <input v-model="round.name" :aria-label="'第 ' + (i+1) + ' 轮名称'" placeholder="例如：技术面" maxlength="40" required>
          <button class="rs-button" type="button" :aria-label="'上移第 ' + (i+1) + ' 轮'" :disabled="i===0" @click="move(i,-1)">↑</button>
          <button class="rs-button" type="button" :aria-label="'下移第 ' + (i+1) + ' 轮'" :disabled="i===form.rounds.length-1" @click="move(i,1)">↓</button>
          <button class="rs-button" type="button" @click="form.rounds.splice(i,1)">移除</button>
        </div>
        <button class="rs-button" type="button" :disabled="form.rounds.length >= 12" @click="form.rounds.splice(form.rounds.at(-1)?.name.trim() === '终面' ? form.rounds.length - 1 : form.rounds.length, 0, { name:'' })">＋ 添加轮次</button>
      </fieldset>
      <div class="rs-actions"><button class="rs-button primary" :disabled="busy">{{ edit ? '保存修改' : '创建岗位' }}</button>
        <button class="rs-button" type="button" :disabled="busy" @click="edit = undefined; error = ''">取消</button></div>
    </form>
    <section class="rs-card rs-panel" aria-label="岗位列表">
      <div class="rs-form jobs-filter">
        <RouterLink v-if="route.query.job || route.query.department" to="/jobs">查看全部岗位</RouterLink>
        <label>招聘状态<select v-model="statusFilter" aria-label="筛选岗位状态">
          <option value="">全部状态</option><option v-for="status in jobStatusOptions" :key="status.value" :value="status.value">{{ status.label }}</option>
        </select></label>
      </div>
      <table class="rs-table">
        <thead><tr><th>岗位</th><th>部门</th><th>状态</th><th>描述</th><th>面试轮次</th><th>招聘计划</th><th>关联简历</th><th v-if="!auth.isSuperAdmin">操作</th></tr></thead>
        <tbody>
          <tr v-for="job in visible" :key="job.id">
            <td>{{ job.title }}</td><td>{{ job.department_name }}</td><td><span class="rs-badge job-status" :class="job.status">{{ jobStatusLabel(job.status) }}</span></td>
            <td class="job-description">{{ job.jd || '—' }}</td>
            <td>{{ job.rounds.map(round => round.name).join(' → ') || '无需面试' }}</td>
            <td>{{ job.planned_headcount === null ? '未设置' : job.planned_headcount + ' 人' }}<br v-if="job.target_date"><small v-if="job.target_date">{{ job.target_date }}</small></td>
            <td><RouterLink v-if="!auth.isSuperAdmin" :to="{ name:'applications', query:{ job:job.id } }" :aria-label="'查看 ' + job.title + ' 的简历'">{{ job.application_count }} 份</RouterLink>
              <span v-else>{{ job.application_count }} 份</span></td>
            <td v-if="!auth.isSuperAdmin"><button v-if="job.manageable" class="rs-button" @click="choose(job)">编辑</button></td>
          </tr>
        </tbody>
      </table>
      <p v-if="!visible.length" class="rs-empty">暂无符合条件的岗位。</p>
    </section>
  </main>
</template>

<style scoped>
.jobs-page { align-content:start; }
.job-editor { display:grid; gap:14px; }
.job-editor h2 { margin:0; }
.jd-field { display:grid; gap:5px; font-size:13px; color:var(--rs-text-secondary); }
.job-editor textarea,.round-editor input { border:1px solid var(--rs-border); border-radius:7px; padding:8px 10px; background:var(--rs-bg-card); color:var(--rs-text); font:inherit; }
.job-editor textarea { resize:vertical; }
.job-editor fieldset { min-width:0; border:1px solid var(--rs-border); border-radius:8px; padding:10px 14px 13px; }
.job-editor legend { font-weight:700; padding:0 5px; }
.job-editor fieldset p { font-size:12px; margin-bottom:8px; }
.round-editor { display:flex; align-items:center; gap:7px; margin-bottom:6px; }
.round-editor span { width:20px; color:var(--rs-text-secondary); }
.round-editor input { flex:1; max-width:290px; min-width:72px; }
.job-status { display:inline-flex; align-items:center; gap:5px; font-weight:600; }
.job-status::before { content:""; width:6px; height:6px; border-radius:50%; background:currentColor; }
.job-status.paused { color:#854d0e; background:#fef3c7; }
.job-status.open { color:#166534; background:#dcfce7; }
.job-status.closed { color:#475569; background:#e2e8f0; }
.jobs-filter { margin-bottom:12px; }
.job-description { max-width:30ch; overflow-wrap:anywhere; white-space:pre-line; }
</style>
