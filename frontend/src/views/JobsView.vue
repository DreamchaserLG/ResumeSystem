<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { api } from '@/api/client'
import { label, loadJobs, type Job, type Options } from '@/api/hr'
import { useAuthStore } from '@/stores/auth'
const auth = useAuthStore()
const jobs = ref<Job[]>([])
const options = ref<Options>()
const edit = ref<number | null>(null)
const form = reactive({ title: '', jd: '', status: 'open', department_id: 0 })
const error = ref('')
const busy = ref(false)
function choose(job?: Job) {
  edit.value = job?.id ?? null
  Object.assign(form, { title: job?.title ?? '', jd: job?.jd ?? '', status: job?.status ?? 'open', department_id: job?.department_id ?? options.value?.hr_department_ids[0] ?? 0 })
}
async function load() {
  jobs.value = await loadJobs()
  if (!auth.isSuperAdmin) options.value = await api.get<Options>('/hr/options')
}
onMounted(async () => { try { await load(); choose() } catch (e) { error.value = (e as Error).message } })
async function submit() {
  busy.value = true; error.value = ''
  try {
    if (edit.value) await api.put('/hr/jobs/' + edit.value, form)
    else await api.post('/hr/jobs', form)
    choose(); await load()
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
</script>
<template>
  <main class="rs-page rs-workspace">
    <header class="rs-heading"><div><h1>岗位管理</h1><p>查看岗位与招聘状态</p></div><button v-if="!auth.isSuperAdmin" class="rs-button" @click="choose()">新建岗位</button></header>
    <p v-if="error" class="rs-error" role="alert">{{ error }}</p>
    <form v-if="!auth.isSuperAdmin" class="rs-card rs-panel rs-form" @submit.prevent="submit">
      <label class="grow">岗位名称<input v-model="form.title" required></label>
      <label>所属部门<select v-model="form.department_id" required><option v-for="d in options?.departments.filter(x => options?.hr_department_ids.includes(x.id))" :key="d.id" :value="d.id">{{ d.name }}</option></select></label>
      <label>状态<select v-model="form.status"><option value="open">招聘中</option><option value="paused">暂停</option><option value="closed">关闭</option></select></label>
      <label class="grow">职位描述<textarea v-model="form.jd" rows="2"></textarea></label>
      <button class="rs-button primary" :disabled="busy">{{ edit ? '保存修改' : '创建岗位' }}</button>
    </form>
    <section class="rs-card rs-panel">
      <table class="rs-table"><thead><tr><th>岗位</th><th>部门</th><th>状态</th><th>描述</th><th v-if="!auth.isSuperAdmin">操作</th></tr></thead><tbody>
        <tr v-for="job in jobs" :key="job.id"><td>{{ job.title }}</td><td>{{ job.department_name }}</td><td>{{ label(job.status) }}</td><td>{{ job.jd }}</td>
          <td v-if="!auth.isSuperAdmin"><button class="rs-button" @click="choose(job)">编辑</button></td></tr>
      </tbody></table><p v-if="!jobs.length" class="rs-empty">暂无岗位。</p>
    </section>
  </main>
</template>
