<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api/client'
import { label, loadJobs, locationText, type Job, type Options, type SearchResult } from '@/api/hr'

const route = useRoute(), router = useRouter()
const jobId = computed(() => {
  const id = typeof route.query.job === 'string' ? Number(route.query.job) : 0
  return Number.isSafeInteger(id) && id > 0 ? id : 0
})
const filters = reactive({ keywords: '', section: 'work', degree: '', skill: '', company: '', city: '', min_months: '', school_tag: '', same_experience: true })
const states = { match: '满足', review: '待核实', reject: '不满足', no_text: '关键词未命中' }
const state = ref<keyof typeof states>('match')
const options = ref<Options>()
const jobs = ref<Job[]>([])
const currentJob = computed(() => jobs.value.find(job => job.id === jobId.value))
const schoolTags = computed(() => options.value?.school_tags.filter(tag => tag.job_id === currentJob.value?.id) ?? [])
function clearJob() {
  filters.school_tag = ''
  results.value = undefined
  void router.replace({ query: {} })
}
const results = ref<SearchResult>()
const offset = ref(0)
const busy = ref(false)
const error = ref('')
async function run(start = 0) {
  busy.value = true; error.value = ''
  try {
    results.value = await api.post<SearchResult>('/hr/search', {
      job_id: currentJob.value?.id || null, keywords: filters.keywords.split(/[，,、]+/).map(s => s.trim()).filter(Boolean),
      section: filters.section || null, degree: filters.degree || null, skill: filters.skill.trim() || null,
      company: filters.company.trim() || null, city: filters.city.trim() || null, school_tag: schoolTags.value.some(tag => tag.tag_code === filters.school_tag) ? filters.school_tag : null,
      min_months: filters.min_months ? Number(filters.min_months) : null, same_experience: filters.same_experience,
      status: state.value, limit: 20, offset: start,
    })
    offset.value = start
  } catch (e) { error.value = (e as Error).message; results.value = undefined }
  finally { busy.value = false }
}
onMounted(async () => {
  try { [jobs.value, options.value] = await Promise.all([loadJobs(), api.get<Options>('/hr/options')]) }
  catch (e) { error.value = (e as Error).message }
})
</script>
<template>
  <main class="rs-page rs-workspace">
    <header class="rs-heading"><div><h1>候选人检索</h1><p>按条件和经历关键词查找本部门候选人</p></div></header>
    <p v-if="error" class="rs-error" role="alert">{{ error }}</p>
    <form class="rs-card rs-panel rs-form" @submit.prevent="run()">
      <div v-if="currentJob" class="rs-actions"><span class="rs-muted">当前岗位：{{ currentJob.title }}</span>
        <button type="button" class="rs-button" @click="clearJob">检索本部门简历</button></div>
      <label class="grow">关键词组<input v-model="filters.keywords" placeholder="例如 高并发，订单"></label>
      <label>经历范围<select v-model="filters.section"><option value="">不限</option><option value="work">工作经历</option><option value="internship">实习</option><option value="project">项目</option><option value="education">教育</option><option value="summary">个人简介</option></select></label>
      <label>最低学历<select v-model="filters.degree"><option value="">不限</option><option v-for="d in ['high_school','associate','bachelor','master','phd']" :key="d" :value="d">{{ label(d) }}</option></select></label>
      <label>技能<input v-model="filters.skill" list="rs-skills" placeholder="例如 Go"></label>
      <datalist id="rs-skills"><option v-for="skill in options?.skills" :key="skill" :value="skill"></option></datalist>
      <label>工作月数<input v-model="filters.min_months" type="number" min="1" max="720" placeholder="例如 36"></label>
      <label>公司<input v-model="filters.company"></label><label>城市<input v-model="filters.city"></label>
      <label v-if="schoolTags.length">院校标签<select v-model="filters.school_tag"><option value="">不限</option><option v-for="tag in schoolTags" :key="tag.tag_code" :value="tag.tag_code">{{ tag.tag_code }}</option></select></label>
      <label>技能、公司与关键词同经历<input v-model="filters.same_experience" type="checkbox"></label>
      <button class="rs-button primary" :disabled="busy">{{ busy ? '查询中' : '检索' }}</button>
    </form>
    <section v-if="results" class="rs-card rs-panel">
      <div class="rs-actions"><button v-for="(title, key) in states" :key="key" class="rs-button" :class="{ primary: state === key }" @click="state = key; run()">{{ title }}</button>
        <span class="rs-muted">{{ results.total }} 条结果</span></div>
      <table class="rs-table"><thead><tr><th>候选人</th><th>岗位</th><th>匹配证据</th></tr></thead><tbody>
        <tr v-for="row in results.items" :key="row.id"><td><RouterLink :to="'/applications/' + row.id">{{ row.name || '申请 #' + row.id }}</RouterLink></td><td>{{ row.job_title }}</td>
          <td class="rs-evidence"><RouterLink v-for="e in row.evidence" :key="e.span_no + ':' + e.field_key" :to="'/applications/' + row.id + '#span-' + e.span_no">
            {{ locationText(e.locator) }} · {{ e.text }}
          </RouterLink></td></tr>
      </tbody></table>
      <p v-if="!results.items.length" class="rs-empty">此分类暂无候选人。</p>
      <div class="rs-pagination"><button class="rs-button" :disabled="!offset" @click="run(offset - 20)">上一页</button><button class="rs-button" :disabled="offset + 20 >= results.total" @click="run(offset + 20)">下一页</button></div>
    </section>
  </main>
</template>
