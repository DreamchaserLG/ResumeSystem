<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { api } from '@/api/client'
import { loadJobs, type Department, type Job } from '@/api/hr'
interface Settings {
  departments: Department[]
  users: { id: number; display_name: string; email: string; super_admin: boolean; departments: number[] }[]
  fields: { field_key: string; display_name: string; purposes: string[] }[]
  integrations: { id: number; name: string; status: string; allowed_fields: string[]; job_ids: number[] }[]
  school_tags: { tag_code: string }[]
  school_filters: { job_id: number; tag_code: string; reason: string }[]
}
const data = ref<Settings>()
const jobs = ref<Job[]>([])
const error = ref(''), message = ref('')
const busy = ref(false)
const dept = ref('')
const user = reactive({ email: '', password: '', display_name: '' })
const grant = reactive({ user_id: 0, department_id: 0 })
const original = reactive({ user_id: 0, application_id: 0, minutes: 30, reason: '' })
const policy = reactive({ field_key: '', purpose: 'preview', allowed: true, reason: '' })
const field = reactive({ field_key: '', display_name: '', alias: '', value_type: 'text' })
const school = reactive({ job_id: 0, tag_code: '', enabled: true, reason: '' })
const client = reactive({ id: 0, name: '', job_ids: [] as number[], allowed_fields: [] as string[], status: 'active' })
async function load() { [data.value, jobs.value] = await Promise.all([api.get<Settings>('/admin/settings'), loadJobs()]) }
onMounted(async () => { try { await load() } catch (e) { error.value = (e as Error).message } })
async function save(task: () => Promise<unknown>) {
  busy.value = true; error.value = ''; message.value = ''
  try { await task(); message.value = '已保存。'; await load() } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
function pick(row?: Settings['integrations'][number]) {
  Object.assign(client, row ? { ...row, job_ids: [...row.job_ids], allowed_fields: [...row.allowed_fields] } : { id: 0, name: '', job_ids: [], allowed_fields: [], status: 'active' })
}
async function saveClient() {
  const { id, ...payload } = client
  await save(() => id ? api.put('/admin/integrations/' + id, payload) : api.post('/admin/integrations', payload))
}
</script>
<template>
  <main class="rs-page rs-workspace">
    <header class="rs-heading"><div><h1>系统设置</h1><p>管理部门、访问权限与对外接口</p></div></header>
    <p v-if="error" class="rs-error" role="alert">{{ error }}</p><p v-if="message" class="rs-message" role="status">{{ message }}</p>
    <div v-if="data" class="rs-two-col">
      <section class="rs-card rs-panel">
        <h2>部门与账号</h2>
        <form class="rs-form" @submit.prevent="save(async () => { await api.post('/admin/departments', { name: dept }); dept = '' })">
          <label class="grow">部门名称<input v-model="dept" required></label><button class="rs-button" :disabled="busy">新建部门</button>
        </form>
        <form class="rs-form" @submit.prevent="save(async () => { await api.post('/admin/users', user); user.password = '' })">
          <label>姓名<input v-model="user.display_name" required></label><label>邮箱<input v-model="user.email" type="email" required></label>
          <label>初始密码<input v-model="user.password" type="password" minlength="8" required></label><button class="rs-button" :disabled="busy">新建账号</button>
        </form>
        <table class="rs-table"><thead><tr><th>账号</th><th>角色或授权部门</th></tr></thead><tbody><tr v-for="u in data.users" :key="u.id"><td>{{ u.display_name }}<br><small>{{ u.email }}</small></td><td>{{ u.super_admin ? '超级管理员' : data.departments.filter(d => u.departments.includes(d.id)).map(d => d.name).join('、') || '待授权' }}</td></tr></tbody></table>
      </section>
      <section class="rs-card rs-panel">
        <h2>访问授权</h2>
        <form class="rs-form" @submit.prevent="save(() => api.post('/admin/grant-dept-hr', grant))">
          <label>账号<select v-model="grant.user_id"><option :value="0">选择账号</option><option v-for="u in data.users.filter(x => !x.super_admin)" :key="u.id" :value="u.id">{{ u.display_name }}</option></select></label>
          <label>部门<select v-model="grant.department_id"><option :value="0">选择部门</option><option v-for="d in data.departments" :key="d.id" :value="d.id">{{ d.name }}</option></select></label>
          <button class="rs-button primary" :disabled="busy || !grant.user_id || !grant.department_id">授予 HR 权限</button>
          <button type="button" class="rs-button" :disabled="busy || !grant.user_id || !grant.department_id" @click="save(() => api.post('/admin/revoke-dept-hr', grant))">撤销</button>
        </form>
        <form class="rs-form" @submit.prevent="save(() => api.post('/admin/original-grants', original))">
          <label>原件申请编号<input v-model.number="original.application_id" type="number" min="1" required></label>
          <label>授权账号<select v-model="original.user_id" required><option :value="0">选择账号</option><option v-for="u in data.users" :key="u.id" :value="u.id">{{ u.display_name }}</option></select></label>
          <label>有效分钟<input v-model.number="original.minutes" type="number" min="1" max="120" required></label>
          <label class="grow">授权理由<input v-model="original.reason" required></label><button class="rs-button" :disabled="busy">授权一次下载</button>
        </form>
      </section>
      <section class="rs-card rs-panel">
        <h2>字段与院校标签</h2>
        <form class="rs-form" @submit.prevent="save(() => api.post('/admin/fields', field))">
          <label>扩展字段编号<input v-model="field.field_key" placeholder="x_.community" required></label>
          <label>显示名称<input v-model="field.display_name" required></label>
          <label>简历标签别名<input v-model="field.alias" required></label>
          <label>类型<select v-model="field.value_type"><option value="text">文本</option><option value="number">数字</option><option value="date">日期</option><option value="period">区间</option><option value="boolean">布尔</option><option value="unknown">待确认</option></select></label>
          <button class="rs-button" :disabled="busy">登记扩展字段</button>
        </form>
        <form class="rs-form" @submit.prevent="save(() => api.post('/admin/field-policies', policy))">
          <label>字段<select v-model="policy.field_key" required><option value="">选择字段</option><option v-for="f in data.fields" :key="f.field_key" :value="f.field_key">{{ f.display_name }} · {{ f.purposes.join(', ') }}</option></select></label>
          <label>用途<select v-model="policy.purpose"><option value="preview">预览</option><option value="filter">筛选</option><option value="search">关键词检索</option><option value="export">外部读取</option><option value="sort">排序</option></select></label>
          <label>允许<input v-model="policy.allowed" type="checkbox"></label><label class="grow">变更理由<input v-model="policy.reason" required></label>
          <button class="rs-button" :disabled="busy">保存字段策略</button>
        </form>
        <form class="rs-form" @submit.prevent="save(() => api.post('/admin/school-filters', school))">
          <label>岗位<select v-model="school.job_id"><option :value="0">选择岗位</option><option v-for="job in jobs" :key="job.id" :value="job.id">{{ job.title }}</option></select></label>
          <label>院校标签<select v-model="school.tag_code" required><option value="">选择标签</option><option v-for="t in data.school_tags" :key="t.tag_code">{{ t.tag_code }}</option></select></label>
          <label>允许筛选<input v-model="school.enabled" type="checkbox"></label><label class="grow">审批理由<input v-model="school.reason" required></label>
          <button class="rs-button" :disabled="busy">保存岗位策略</button>
        </form>
      </section>
      <section class="rs-card rs-panel">
        <h2>集成账号</h2>
        <div class="rs-actions"><button class="rs-button" @click="pick()">新建</button><button v-for="c in data.integrations" :key="c.id" class="rs-button" @click="pick(c)">{{ c.name }}</button></div>
        <form class="rs-form" @submit.prevent="saveClient">
          <label class="grow">名称<input v-model="client.name" required></label>
          <label>状态<select v-model="client.status"><option value="active">启用</option><option value="disabled">停用</option></select></label>
          <label>岗位白名单<select v-model="client.job_ids" multiple size="4"><option v-for="j in jobs" :key="j.id" :value="j.id">{{ j.title }}</option></select></label>
          <label>字段白名单<select v-model="client.allowed_fields" multiple size="4"><option v-for="f in data.fields.filter(x => x.purposes.includes('export'))" :key="f.field_key" :value="f.field_key">{{ f.display_name }}</option></select></label>
          <button class="rs-button" :disabled="busy">保存集成账号</button>
        </form>
        <p class="rs-empty">密钥由服务端命令签发。账号编号 {{ client.id || '保存后生成' }}。</p>
      </section>
    </div>
    <button class="rs-button" :disabled="busy" @click="save(() => api.post('/admin/cleanup-files'))">重试待清理文件</button>
  </main>
</template>
