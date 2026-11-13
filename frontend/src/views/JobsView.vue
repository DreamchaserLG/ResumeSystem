<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { api } from '@/api/client'
import { label, loadJobs, type Job, type Options } from '@/api/hr'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const jobs = ref<Job[]>([])
const options = ref<Options>()
const edit = ref<number | null>()
const form = reactive({ title: '', jd: '', status: 'open', department_id: 0, rounds: [] as { id?: number; name: string }[] })
const editor = ref<HTMLFormElement>()
const error = ref('')
const message = ref('')
const busy = ref(false)
const department = ref<number | ''>('')
const departments = computed(() => [...new Map(jobs.value.map(j => [j.department_id,j.department_name])).entries()])
const visible = computed(() => jobs.value.filter(j => !department.value || department.value === j.department_id))
const count = computed(() => visible.value.reduce((sum, j) => sum+j.application_count,0))
async function choose(job?: Job) {
  error.value = ''; message.value = ''
  edit.value = job?.id ?? null
  Object.assign(form, {
    title: job?.title ?? '', jd: job?.jd ?? '', status: job?.status ?? 'open',
    department_id: job?.department_id ?? options.value?.hr_department_ids[0] ?? 0,
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
onMounted(async () => { try { await load() } catch (e) { error.value = (e as Error).message } })
function move(i: number, delta: number) {
  const target = i + delta
  if (target < 0 || target >= form.rounds.length) return
  const [round] = form.rounds.splice(i, 1)
  form.rounds.splice(target, 0, round)
}
async function submit() {
  busy.value = true; error.value = ''; message.value = ''
  try {
    if (edit.value) await api.put('/hr/jobs/' + edit.value, form)
    else await api.post('/hr/jobs', form)
    message.value = edit.value ? '岗位已更新。' : '岗位已创建。'
    edit.value = undefined
    await load()
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
</script>

<template>
  <main class="rs-page rs-workspace jobs-page">
    <header class="rs-heading"><div><h1>岗位管理</h1><p>配置面试轮次，查看每个岗位关联的简历。</p></div>
      <button v-if="!auth.isSuperAdmin" class="rs-button primary" @click="choose()">＋ 新建岗位</button></header>
    <p v-if="error" class="rs-error" role="alert">{{ error }}</p>
    <p v-if="message" class="rs-message" role="status">{{ message }}</p>
    <form v-if="!auth.isSuperAdmin && edit !== undefined" ref="editor" class="rs-card rs-panel job-editor" @submit.prevent="submit">
      <h2>{{ edit ? '编辑岗位' : '新建岗位' }}</h2>
      <div class="rs-form">
        <label class="grow">岗位名称<input v-model="form.title" maxlength="120" required></label>
        <label>所属部门<select v-model="form.department_id" :disabled="edit !== null" required><option v-for="d in options?.departments.filter(x => options?.hr_department_ids.includes(x.id))" :key="d.id" :value="d.id">{{ d.name }}</option></select></label>
        <label>招聘状态<select v-model="form.status"><option value="open">招聘中</option><option value="paused">暂停</option><option value="closed">关闭</option></select></label>
      </div>
      <label class="jd-field">职位描述<textarea v-model="form.jd" maxlength="4000" rows="2"></textarea></label>
      <fieldset>
        <legend>面试轮次</legend>
        <p class="rs-muted">按下方顺序展示。可以添加、改名或调整顺序，也可以设为无需面试。</p>
        <div v-for="(round,i) in form.rounds" :key="i" class="round-editor">
          <span>{{ i + 1 }}</span>
          <input v-model="round.name" :aria-label="'第 ' + (i+1) + ' 轮名称'" placeholder="例如：技术面" maxlength="40" required>
          <button class="rs-button" type="button" :aria-label="'上移第 ' + (i+1) + ' 轮'" :disabled="i===0" @click="move(i,-1)">↑</button>
          <button class="rs-button" type="button" :aria-label="'下移第 ' + (i+1) + ' 轮'" :disabled="i===form.rounds.length-1" @click="move(i,1)">↓</button>
          <button class="rs-button" type="button" @click="form.rounds.splice(i,1)">移除</button>
        </div>
        <button class="rs-button" type="button" :disabled="form.rounds.length >= 12" @click="form.rounds.push({ name:'' })">＋ 添加轮次</button>
      </fieldset>
      <div class="rs-actions"><button class="rs-button primary" :disabled="busy">{{ edit ? '保存修改' : '创建岗位' }}</button>
        <button class="rs-button" type="button" :disabled="busy" @click="edit = undefined; error = ''">取消</button></div>
    </form>
    <section class="rs-card rs-panel job-list">
      <div class="rs-actions job-summary"><label>部门 <select v-model="department" aria-label="筛选部门"><option value="">全部部门</option><option v-for="[id,name] in departments" :key="id" :value="id">{{ name }}</option></select></label>
        <span>{{ visible.length }} 个岗位 · {{ count }} 份关联简历</span></div>
      <table class="rs-table"><thead><tr><th>岗位 / 部门</th><th>招聘状态</th><th>面试流程</th><th>关联简历</th><th v-if="!auth.isSuperAdmin">操作</th></tr></thead><tbody>
        <tr v-for="job in visible" :key="job.id"><td><strong>{{ job.title }}</strong><small>{{ job.department_name }}</small></td>
          <td><span class="rs-badge">{{ label(job.status) }}</span></td>
          <td><span class="job-rounds">{{ job.rounds.map(r => r.name).join(' → ') || '无需面试' }}</span></td>
          <td><RouterLink v-if="!auth.isSuperAdmin" class="inline" :to="{ name:'applications', query:{ job:job.id } }">{{ job.application_count }} 份</RouterLink><span v-else>{{ job.application_count }} 份</span></td>
          <td v-if="!auth.isSuperAdmin"><div class="rs-actions"><RouterLink :to="{ name:'applications', query:{ job:job.id } }">查看简历</RouterLink><button v-if="job.manageable" class="rs-button" @click="choose(job)">编辑</button></div></td></tr>
      </tbody></table><p v-if="!visible.length" class="rs-empty">暂无岗位。</p>
    </section>
  </main>
</template>

<style scoped>
.jobs-page { padding-bottom:24px; }
.job-editor { display:grid; gap:14px; }
.job-editor h2 { margin:0; }
.jd-field { display:grid; gap:5px; font-size:13px; }
.job-editor textarea,.job-editor input,.job-summary select { border:1px solid #dce4f2; border-radius:7px; padding:8px 10px; background:#fff; color:#253a60; }
.job-editor fieldset { border:1px solid #e1e8f4; border-radius:10px; padding:10px 14px 13px; }
.job-editor legend { font-weight:700; padding:0 5px; }
.job-editor fieldset p { font-size:12px; margin-bottom:8px; }
.round-editor { display:flex; align-items:center; gap:7px; margin-bottom:6px; }
.round-editor span { width:20px; color:#526585; }
.round-editor input { flex:1; max-width:290px; min-width:80px; }
.job-summary { margin-bottom:12px; justify-content:space-between; color:#526585; }
.job-list { overflow:auto; }
.job-list small { display:block; margin-top:4px; color:#69778d; }
.job-rounds { color:#475a79; }
</style>
