<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { BriefcaseOutline } from '@vicons/ionicons5'
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
  <main class="recruit-workspace rs-workspace jobs-page">
    <header class="workspace-heading">
      <div><span class="workspace-kicker">招聘工作台 / 岗位</span><h1>岗位管理</h1><p>配置面试轮次，查看每个岗位关联的简历。</p></div>
      <div class="heading-actions"><button v-if="!auth.isSuperAdmin" class="rs-button primary" @click="choose()">＋ 新建岗位</button></div>
    </header>
    <p v-if="error" class="workspace-notice danger" role="alert">{{ error }}</p>
    <p v-if="message" class="workspace-notice" role="status">{{ message }}</p>
    <form v-if="!auth.isSuperAdmin && edit !== undefined" ref="editor" class="workspace-surface rs-panel job-editor" @submit.prevent="submit">
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
    <section class="job-list" aria-label="岗位列表">
      <div class="workspace-surface job-summary">
        <label>部门 <select v-model="department" aria-label="筛选部门"><option value="">全部部门</option><option v-for="[id,name] in departments" :key="id" :value="id">{{ name }}</option></select></label>
        <span>{{ visible.length }} 个岗位 · {{ count }} 份关联简历</span>
      </div>
      <article v-for="job in visible" :key="job.id" class="workspace-surface job-row">
        <div class="job-mark" aria-hidden="true"><BriefcaseOutline/></div>
        <div class="job-content">
          <div class="job-title"><h2>{{ job.title }}</h2><span class="workspace-chip" :class="job.status">{{ label(job.status) }}</span></div>
          <p>{{ job.department_name }}</p>
          <div class="job-rounds" aria-label="面试轮次">
            <span v-for="round in job.rounds" :key="round.id" class="workspace-chip interview">{{ round.name }}</span>
            <span v-if="!job.rounds.length" class="workspace-chip">无需面试</span>
          </div>
        </div>
        <div class="job-actions">
          <RouterLink v-if="!auth.isSuperAdmin" class="job-count" :to="{ name:'applications', query:{ job:job.id } }" :aria-label="'查看 ' + job.title + ' 的简历'">
            <strong>{{ job.application_count }}</strong><span>份简历 ›</span>
          </RouterLink>
          <div v-else class="job-count"><strong>{{ job.application_count }}</strong><span>份简历</span></div>
          <button v-if="!auth.isSuperAdmin && job.manageable" class="rs-button" @click="choose(job)">编辑岗位</button>
        </div>
      </article>
      <p v-if="!visible.length" class="workspace-surface job-empty">暂无岗位。</p>
    </section>
  </main>
</template>

<style scoped>
.jobs-page { gap:12px; align-content:start; }
.job-editor { display:grid; gap:14px; }
.job-editor h2 { margin:0; }
.jd-field { display:grid; gap:5px; font-size:13px; color:var(--rs-text-secondary); }
.job-editor textarea,.job-editor input,.job-summary select { border:1px solid #d9e2f1; border-radius:8px; padding:8px 10px; background:#fff; color:#263a5c; }
.job-editor textarea { resize:vertical; }
.job-editor fieldset { min-width:0; border:1px solid #e0e7f2; border-radius:10px; padding:10px 14px 13px; }
.job-editor legend { font-weight:700; padding:0 5px; }
.job-editor fieldset p { font-size:12px; margin-bottom:8px; }
.round-editor { display:flex; align-items:center; gap:7px; margin-bottom:6px; }
.round-editor span { width:20px; color:#526585; }
.round-editor input { flex:1; max-width:290px; min-width:72px; }
.job-list { display:grid; gap:8px; min-width:0; }
.job-summary { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px; padding:9px 11px; color:#526585; font-size:13px; }
.job-summary label { display:flex; align-items:center; gap:8px; }
.job-row { display:grid; grid-template-columns:54px minmax(0,1fr) auto; align-items:center; gap:15px; padding:13px 15px; min-height:108px; border-radius:16px; box-shadow:0 2px 9px #1a345506; }
.job-mark { width:54px; height:54px; display:grid; place-items:center; border-radius:15px; background:linear-gradient(145deg,#e6eeff,#d8e5fa); color:#36568e; }
.job-mark svg { width:24px; height:24px; }
.job-title,.job-rounds,.job-actions { display:flex; align-items:center; flex-wrap:wrap; gap:7px; }
.job-title h2 { margin:0; font-size:17px; line-height:1.35; overflow-wrap:anywhere; }
.job-content p { margin:5px 0 6px; font-size:13px; color:#596982; }
.job-actions { justify-content:flex-end; gap:12px; }
.job-count { display:flex; align-items:center; gap:6px; color:#3858a1; font-size:13px; text-decoration:none; white-space:nowrap; }
.job-count strong { font-size:20px; }
a.job-count:hover { text-decoration:underline; }
.job-empty { padding:24px; text-align:center; color:#546884; }
@media (max-width: 680px) {
  .job-row { grid-template-columns:36px minmax(0,1fr); gap:9px; }
  .job-mark { width:36px; height:36px; border-radius:10px; }
  .job-actions { grid-column:2; justify-content:flex-start; }
}
</style>
