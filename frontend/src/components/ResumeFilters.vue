<script lang="ts">
type Facet = 'degree' | 'school' | 'domain' | 'capability'
type Bounds = [number | null, number | null]
export type FilterSelection = Partial<Record<Facet, string[]>> & {
  department?: number
  jobs?: string[]
  salaryRange?: Bounds
  experienceRange?: Bounds
}
export function filterSize(filters:FilterSelection) {
  return (filters.jobs?.length ?? 0) + Number(Boolean(filters.department))
    + Number(Boolean(filters.salaryRange)) + Number(Boolean(filters.experienceRange))
    + (filters.degree?.length ?? 0) + (filters.school?.length ?? 0)
    + (filters.domain?.length ?? 0) + (filters.capability?.length ?? 0)
}

export function filterSearchBody(filters:FilterSelection) {
  const {department,jobs,salaryRange,experienceRange,...facets}=filters
  return {
    department_id:department, job_ids:jobs?.map(Number) ?? [], facets,
    salary_min:salaryRange?.[0],salary_max:salaryRange?.[1],
    experience_min_months:experienceRange?.[0],experience_max_months:experienceRange?.[1],
  }
}
</script>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElButton, ElInput, ElSlider } from 'element-plus'
import { ChevronBackOutline } from '@vicons/ionicons5'
import { api } from '@/api/client'
import type { CapabilityMatch, CapabilityTag, Job } from '@/api/hr'

const props = defineProps<{ title:string; selected:FilterSelection; available:string[]; departments:{id:number;name:string}[];
  jobs:Job[]; requiresDepartment:boolean }>()
const emit = defineEmits<{ close:[]; apply:[filters:FilterSelection] }>()
const panel = ref<HTMLElement>()
const salaryMax = 100000
const experienceMax = 360
const salary = ref<[number, number]>([props.selected.salaryRange?.[0] ?? 0, props.selected.salaryRange?.[1] ?? salaryMax])
const experience = ref<[number, number]>([props.selected.experienceRange?.[0] ?? 0, props.selected.experienceRange?.[1] ?? experienceMax])
const facets: Facet[] = ['degree', 'school', 'domain', 'capability']
const draft = ref<Partial<Record<Facet, string[]>>>(
  Object.fromEntries(facets.map(key => [key, [...(props.selected[key] ?? [])]])),
)

type Choice = { facet:Facet; value:string; label:string }
type FilterGroup = { key:Facet; title:string; options:Choice[] }
function choices(facet:Facet, values:[string, string][]): Choice[] {
  return values.map(([value,label]) => ({facet,value,label}))
}
const degrees = computed(() => props.available.includes('degree') ? choices('degree', [
  ['high_school','高中'], ['associate','大专'], ['bachelor','本科'], ['master','硕士'], ['phd','博士'], ['other','其他学历'],
]) : [])
const schools = computed(() => props.available.includes('school') ? choices('school', [
  ['is_985','985'], ['is_211','211'], ['is_double_first_class','双一流'], ['study_abroad','留学'], ['qs_top100','QS 前 100'],
]) : [])
const groups = computed(() => ([
  { key:'school', title:'学校级别', options:schools.value },
  { key:'domain', title:'经历方向', options:props.available.includes('domain') ? choices('domain', [
    ['technology','技术'], ['finance','金融'], ['legal','法律'], ['consumer','生活服务'],
    ['human_resources','人力资源'], ['healthcare','医疗'], ['biomed','生物医药'],
  ]) : [] },
] satisfies FilterGroup[]).filter(group=>group.options.length))
const capabilityEnabled = computed(() => props.available.includes('capability'))
const tagQuery = ref('')
const tagLabels = ref<Record<string, string>>({})
const tagMatches = ref<Record<string, number | null>>({})
const tagsLoading = ref(false)
const tagsUnmatched = ref(false)
const dictionaryMatch = ref(false)
const tagError = ref('')
const tagThreshold = ref(.5)
let tagRequest = 0
let tagTimer: ReturnType<typeof setTimeout> | undefined
let tagController: AbortController | undefined

function rememberTags(items: CapabilityTag[]) {
  for (const item of items) tagLabels.value[item.code] = item.name
}

watch(capabilityEnabled, async enabled => {
  if (!enabled || !draft.value.capability?.length) return
  try {
    rememberTags((await api.get<{items:CapabilityTag[]}>('/hr/capability-tags?full=true')).items)
  } catch (cause) {
    if (!tagQuery.value.trim()) tagError.value = (cause as Error).message
  }
}, { immediate:true })

function resetTagRequest() {
  clearTimeout(tagTimer)
  tagController?.abort()
  return ++tagRequest
}

function scheduleTagMatch(immediate = false) {
  const request = resetTagRequest()
  const query = tagQuery.value.trim()
  tagsLoading.value = Boolean(query)
  tagsUnmatched.value = false
  dictionaryMatch.value = false
  tagError.value = ''
  if (!query) {
    tagMatches.value = {}
    draft.value.capability = []
  } else if (immediate) {
    void matchTags(query, request)
  } else {
    tagTimer = setTimeout(() => void matchTags(query, request), 400)
  }
}

async function matchTags(query: string, request: number) {
  const controller = tagController = new AbortController()
  try {
    const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(9000)])
    const { items, threshold, mode } = await api.post<{items:CapabilityMatch[]; threshold:number; mode?:'dictionary'}>(
      '/hr/capability-tags/match', {query}, signal)
    if (request !== tagRequest) return
    rememberTags(items)
    tagThreshold.value = threshold
    dictionaryMatch.value = mode === 'dictionary'
    tagMatches.value = Object.fromEntries(items.map(item => [item.code, item.similarity]))
    draft.value.capability = items.map(item => item.code)
    tagsUnmatched.value = !items.length
  } catch (cause) {
    if (request === tagRequest) tagError.value = (cause as Error).name === 'TimeoutError'
      ? '本次匹配超时，按回车重试' : (cause as Error).message
  } finally {
    if (request === tagRequest) tagsLoading.value = false
  }
}
watch(tagQuery, () => scheduleTagMatch(), { flush:'sync' })
onBeforeUnmount(resetTagRequest)

function clearCapabilities() {
  if (tagQuery.value) tagQuery.value = ''
  else scheduleTagMatch()
}

function capabilityLabel(code: string) {
  return tagLabels.value[code] || '已选能力'
}

const percentage = (similarity: number) => `${(similarity * 100).toFixed(1).replace(/\.0$/, '')}%`

const draftDepartment = ref(props.selected.department)
const draftJobs = ref([...(props.selected.jobs ?? [])])
const jobChoices = computed(()=>props.jobs.filter(job=>!props.requiresDepartment || job.department_id===draftDepartment.value))
function selectDepartment(department?:number) {
  draftDepartment.value=department
  draftJobs.value=[]
}
function toggleJob(id:number) {
  const value=String(id)
  if(draftJobs.value.includes(value)) draftJobs.value=draftJobs.value.filter(item=>item!==value)
  else if(draftJobs.value.length<100) draftJobs.value.push(value)
}
function toggle({facet,value}:Choice) {
  const selected = draft.value[facet] ?? []
  draft.value[facet] = selected.includes(value) ? selected.filter(item=>item!==value) : [...selected,value]
}

function bounds([min,max]:[number,number], ceiling:number): Bounds | undefined {
  if (min === 0 && max === ceiling) return undefined
  return [min === 0 ? null : min, max === ceiling ? null : max]
}
function salaryText(value:number) {
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits:1 }).format(value / 1000) + 'K'
}
function experienceText(value:number) {
  const years = Math.floor(value / 12)
  const months = value % 12
  return months ? (years ? years + ' 年 ' : '') + months + ' 个月' : years + ' 年'
}
function rangeText(range:[number,number], ceiling:number, format:(n:number)=>string) {
  const [min,max] = range
  if (min === 0 && max === ceiling) return '不限'
  if (min === max && max === ceiling) return format(min) + '及以上'
  if (!min) return format(max) + '及以下'
  if (max === ceiling) return format(min) + '及以上'
  if (min === max) return format(min)
  return format(min) + ' – ' + format(max)
}
function clear() {
  clearCapabilities()
  draft.value = {}
  selectDepartment()
  salary.value = [0, salaryMax]
  experience.value = [0, experienceMax]
}
function apply() {
  const filters: FilterSelection = Object.fromEntries(Object.entries(draft.value).filter(([,values]) => values.length))
  if (props.available.includes('salary')) filters.salaryRange = bounds(salary.value, salaryMax)
  if (props.available.includes('experience')) filters.experienceRange = bounds(experience.value, experienceMax)
  if (props.departments.length && draftDepartment.value) filters.department=draftDepartment.value
  const jobs=jobChoices.value.map(job=>String(job.id))
  const selectedJobs=draftJobs.value.filter(job=>jobs.includes(job))
  if(selectedJobs.length) filters.jobs=selectedJobs
  emit('apply', filters)
}
onMounted(() => panel.value?.focus({ preventScroll:true }))
</script>

<template>
  <section ref="panel" class="local-filters" role="dialog" :aria-label="title" tabindex="-1" @keydown.esc.stop="emit('close')">
    <header class="filter-title"><button type="button" aria-label="关闭筛选" @click="emit('close')"><ChevronBackOutline aria-hidden="true"/></button>
      <h2>{{ title }}</h2>
    </header>
    <div class="filter-options">
      <section v-if="departments.length" class="filter-group" aria-labelledby="filter-department">
        <header class="group-heading"><h3 id="filter-department">部门</h3>
          <button type="button" :aria-pressed="!draftDepartment" @click="selectDepartment()">全部</button></header>
        <div class="filter-choices wide-choices">
          <button v-for="dept in departments" :key="dept.id" type="button" class="facet-choice"
            :aria-pressed="draftDepartment===dept.id" @click="selectDepartment(draftDepartment===dept.id?undefined:dept.id)">{{ dept.name }}</button>
        </div>
      </section>
      <section class="filter-group" aria-labelledby="filter-job">
        <header class="group-heading"><h3 id="filter-job">岗位</h3>
          <button type="button" :aria-pressed="!draftJobs.length" @click="draftJobs=[]">全部</button></header>
        <p v-if="requiresDepartment && !draftDepartment" class="hire-muted">选择部门后可筛选岗位。</p>
        <p v-else-if="!jobChoices.length" class="hire-muted">暂无可选岗位。</p>
        <div v-else class="filter-choices wide-choices">
          <button v-for="job in jobChoices" :key="job.id" type="button" class="facet-choice" :title="job.title"
            :aria-pressed="draftJobs.includes(String(job.id))" @click="toggleJob(job.id)">{{ job.title }}</button>
        </div>
      </section>
      <section v-if="degrees.length" class="filter-group" aria-labelledby="filter-degree">
        <header class="group-heading"><h3 id="filter-degree">学历要求</h3>
          <button type="button" :aria-pressed="!draft.degree?.length" @click="draft.degree=[]">全部</button></header>
        <div class="filter-choices">
          <button v-for="choice in degrees" :key="choice.value" type="button" class="facet-choice"
            :aria-pressed="draft.degree?.includes(choice.value) ?? false" @click="toggle(choice)">{{ choice.label }}</button>
        </div>
      </section>
      <section v-if="available.includes('experience')" class="filter-group filter-range" aria-labelledby="filter-experience">
        <header class="group-heading"><h3 id="filter-experience">工作经验</h3>
          <button type="button" :aria-pressed="!bounds(experience,experienceMax)" @click="experience=[0,experienceMax]">全部</button></header>
        <output>{{ rangeText(experience,experienceMax,experienceText) }}</output>
        <ElSlider v-model="experience" range :max="experienceMax" :step="6" :show-tooltip="false" aria-label="工作经验（月）"
          range-start-label="工作经验下限（月）" range-end-label="工作经验上限（月）"/>
        <div class="range-ends"><span>0 年</span><span>30 年以上</span></div>
      </section>
      <section v-if="available.includes('salary')" class="filter-group filter-range" aria-labelledby="filter-salary">
        <header class="group-heading"><h3 id="filter-salary">期望月薪</h3>
          <button type="button" :aria-pressed="!bounds(salary,salaryMax)" @click="salary=[0,salaryMax]">全部</button></header>
        <output>{{ rangeText(salary,salaryMax,salaryText) }}</output>
        <ElSlider v-model="salary" range :max="salaryMax" :step="500" :show-tooltip="false" aria-label="期望月薪（元）"
          range-start-label="期望月薪下限（元）" range-end-label="期望月薪上限（元）"/>
        <div class="range-ends"><span>0 元</span><span>100K 以上</span></div>
      </section>
      <section v-for="group in groups" :key="group.key" class="filter-group" :aria-labelledby="'filter-'+group.key">
        <header class="group-heading"><h3 :id="'filter-'+group.key">{{ group.title }}</h3>
          <button type="button" :aria-pressed="!draft[group.key]?.length"
            @click="draft[group.key]=[]">全部</button></header>
        <div class="filter-choices">
          <button v-for="choice in group.options" :key="choice.value" type="button" class="facet-choice"
            :aria-pressed="draft[choice.facet]?.includes(choice.value) ?? false" :title="choice.label"
            @click="toggle(choice)">{{ choice.label }}</button>
        </div>
      </section>
      <section v-if="capabilityEnabled" class="filter-group" aria-labelledby="filter-capability">
        <header class="group-heading"><h3 id="filter-capability">能力标签</h3>
          <button type="button" :aria-pressed="!draft.capability?.length && !tagQuery" @click="clearCapabilities">全部</button>
        </header>
        <ElInput v-model="tagQuery" maxlength="120" aria-label="输入能力自动匹配标签" placeholder="输入能力，如绩效管理或数据库" clearable
          @keyup.enter="tagQuery.trim() && scheduleTagMatch(true)"/>
        <p v-if="tagsLoading" class="capability-hint" role="status">正在匹配能力标签…</p>
        <p v-else-if="tagError" class="capability-error" role="alert">{{ tagError }}</p>
        <p v-else-if="dictionaryMatch" class="capability-hint" role="status">
          {{ tagsUnmatched ? '标签词库没有匹配项，可以换个关键词或按回车重试' : '已按标签词库匹配，可直接筛选' }}
        </p>
        <p v-else-if="tagsUnmatched" class="capability-hint" role="status">没有相似度达到 {{ percentage(tagThreshold) }} 的标签</p>
        <div v-if="draft.capability?.length" class="capability-tags">
          <button v-for="code in draft.capability" :key="code" type="button" class="facet-choice"
            :aria-label="`移除${capabilityLabel(code)}标签`" :title="capabilityLabel(code)" aria-pressed="true"
            @click="draft.capability=draft.capability?.filter(tag=>tag!==code)">
            {{ capabilityLabel(code) }}<span v-if="typeof tagMatches[code] === 'number'"> {{ percentage(tagMatches[code]!) }}</span> ×
          </button>
        </div>
      </section>
    </div>
    <footer class="local-filter-actions">
      <ElButton @click="clear">清除</ElButton>
      <ElButton type="primary" :disabled="tagsLoading" @click="apply">确定</ElButton>
    </footer>
  </section>
</template>

<style scoped>
.local-filters {grid-row:1 / -1;display:grid;grid-template-rows:auto minmax(0,1fr) auto;min-width:0;min-height:0;background:linear-gradient(180deg,#fff,#fcfefe)}
.local-filters:focus {outline:0}
.filter-title,.group-heading,.range-ends {display:flex;align-items:center;justify-content:space-between;gap:8px}
.filter-title {justify-content:flex-start;padding:14px 16px;border-bottom:1px solid var(--hire-line);background:#fbfdfd}
.filter-title h2 {font-size:15px}
.filter-title button {width:30px;height:30px;padding:6px;border:none;border-radius:7px;background:var(--rs-primary-soft);cursor:pointer;color:var(--hire-accent-dark);transition:transform var(--rs-motion-fast),background-color var(--rs-motion-fast)}
.filter-title button:hover {background:#d7efea;transform:scale(1.04)}
.filter-options {overflow:auto;overscroll-behavior:contain;padding:0 16px;scrollbar-gutter:stable}
.filter-group {min-width:0;padding:17px 0 19px;border-bottom:1px solid var(--hire-line)}
.filter-group:last-child {border-bottom:0}
.group-heading {margin-bottom:10px}
.group-heading h3 {font-size:13px}
.group-heading button:not(.el-button) {border:1px solid #e1e8eb;border-radius:5px;padding:3px 7px;background:#fff;font-size:11px;cursor:pointer;color:var(--hire-muted)}
.group-heading button[aria-pressed=true] {border-color:#badbd4;background:#edf8f5;color:var(--hire-accent-dark)}
.capability-tags {display:flex;flex-wrap:wrap;gap:6px;margin-top:9px}
.capability-tags .facet-choice {min-height:28px;padding-inline:7px}
.capability-error {color:#9e362d;font-size:12px;margin-top:8px}
.capability-hint {color:var(--hire-muted);font-size:12px;margin-top:8px}
.filter-choices {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px}
.wide-choices {grid-template-columns:repeat(2,minmax(0,1fr))}
.facet-choice {cursor:pointer;min-height:33px;min-width:0;padding:4px 5px;border:1px solid #e2eaec;border-radius:7px;
  color:#344f5a;background:#f7fafb;font-size:12px;text-align:center;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;transition:transform var(--rs-motion-fast),border-color var(--rs-motion-fast),background-color var(--rs-motion-fast)}
.facet-choice:hover {border-color:#91c6bf;background:#f1f8f6;transform:translateY(-1px)}
.facet-choice[aria-pressed=true] {border-color:#8bbfb6;background:var(--hire-soft);color:var(--hire-accent-dark);font-weight:600}
.filter-range output {color:var(--hire-accent-dark);font-weight:600;font-size:12px;font-variant-numeric:tabular-nums}
.range-ends {color:var(--hire-muted);font-size:11px;margin-top:1px}
.filter-range :deep(.el-slider) {padding-inline:9px;height:36px;--el-slider-button-size:15px;--el-slider-height:4px}
.filter-range :deep(.el-slider__button-wrapper) {touch-action:none}
.local-filter-actions {display:grid;grid-template-columns:1fr 2fr;gap:9px;padding:13px 15px;border-top:1px solid var(--hire-line);background:#fbfdfd}
.local-filter-actions .el-button {margin:0}
</style>
