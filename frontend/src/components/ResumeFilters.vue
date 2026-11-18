<script lang="ts">
type Facet = 'degree' | 'school' | 'domain' | 'job' | 'followup'
type Bounds = [number | null, number | null]
export type FilterSelection = Partial<Record<Facet, string[]>> & {
  salaryRange?: Bounds
  experienceRange?: Bounds
}
</script>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElButton, ElSlider } from 'element-plus'
import { CloseOutline } from '@vicons/ionicons5'
import type { Job } from '@/api/hr'

const props = defineProps<{ selected:FilterSelection; available:string[]; jobs:Job[]; followups:string[] }>()
const emit = defineEmits<{ close:[]; apply:[filters:FilterSelection] }>()
const panel = ref<HTMLElement>()
const salaryMax = 100000
const experienceMax = 360
const salary = ref<[number, number]>([props.selected.salaryRange?.[0] ?? 0, props.selected.salaryRange?.[1] ?? salaryMax])
const experience = ref<[number, number]>([props.selected.experienceRange?.[0] ?? 0, props.selected.experienceRange?.[1] ?? experienceMax])
const facets: Facet[] = ['degree', 'school', 'domain', 'job', 'followup']
const draft = ref<Partial<Record<Facet, string[]>>>(
  Object.fromEntries(facets.map(key => [key, [...(props.selected[key] ?? [])]])),
)

type Choice = { facet:Facet; value:string; label:string }
function choices(facet:Facet, values:[string, string][]): Choice[] {
  return values.map(([value,label]) => ({facet,value,label}))
}
const degrees = computed(() => props.available.includes('degree') ? choices('degree', [
  ['high_school','高中'], ['associate','大专'], ['bachelor','本科'], ['master','硕士'], ['phd','博士'], ['other','其他学历'],
]) : [])
const schools = computed(() => props.available.includes('school') ? choices('school', [
  ['is_985','985'], ['is_211','211'], ['is_double_first_class','双一流'], ['study_abroad','留学'], ['qs_top100','QS 前 100'],
]) : [])
const groups = computed(() => [
  { key:'school', title:'学校级别', options:schools.value },
  { key:'followup', title:'当前状态', options:choices('followup', [
    ['recorded','有跟进记录'], ['unrecorded','暂无跟进记录'], ...props.followups.map((body):[string,string] => ['note:'+body,body]),
  ]) },
  { key:'domain', title:'经历方向', options:props.available.includes('domain') ? choices('domain', [
    ['technology','技术'], ['finance','金融'], ['legal','法律'], ['consumer','生活服务'],
    ['human_resources','人力资源'], ['healthcare','医疗'], ['biomed','生物医药'],
  ]) : [] },
  { key:'job', title:'招聘岗位', options:choices('job', props.jobs.map(job => [String(job.id),job.title])) },
].filter(group=>group.options.length))

function toggle({facet,value}:Choice) {
  const selected = draft.value[facet] ?? []
  if (selected.includes(value)) {
    draft.value[facet] = selected.filter(item => item !== value)
  } else if (facet === 'followup') {
    const notes = selected.filter(item => item.startsWith('note:'))
    draft.value.followup = value.startsWith('note:') ? [...notes, value].slice(-30) : [value]
  } else {
    const limit = facet === 'job' ? 100 : Infinity
    if (selected.length < limit) draft.value[facet] = [...selected, value]
  }
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
  draft.value = {}
  salary.value = [0, salaryMax]
  experience.value = [0, experienceMax]
}
function apply() {
  const filters: FilterSelection = Object.fromEntries(Object.entries(draft.value).filter(([,values]) => values.length))
  if (props.available.includes('salary')) filters.salaryRange = bounds(salary.value, salaryMax)
  if (props.available.includes('experience')) filters.experienceRange = bounds(experience.value, experienceMax)
  emit('apply', filters)
}
onMounted(() => panel.value?.focus({ preventScroll:true }))
</script>

<template>
  <section id="candidate-filter-panel" ref="panel" class="local-filters" aria-labelledby="candidate-filter-title" tabindex="-1"
    @keydown.esc.stop="emit('close')">
    <header class="filter-title"><h2 id="candidate-filter-title">筛选</h2>
      <button type="button" aria-label="关闭筛选" @click="emit('close')"><CloseOutline aria-hidden="true"/></button>
    </header>
    <div class="filter-options">
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
          <button type="button" :aria-pressed="!draft[group.options[0].facet]?.length"
            @click="draft[group.options[0].facet]=[]">全部</button></header>
        <p v-if="group.key==='followup'" class="group-hint">按最新跟进备注筛选</p>
        <div class="filter-choices" :class="{ 'wide-choices':group.key==='job' || group.key==='followup' }">
          <button v-for="choice in group.options" :key="choice.value" type="button" class="facet-choice"
            :aria-pressed="draft[choice.facet]?.includes(choice.value) ?? false" :title="choice.label"
            @click="toggle(choice)">{{ choice.label }}</button>
        </div>
      </section>
    </div>
    <footer class="local-filter-actions">
      <ElButton @click="clear">清除</ElButton>
      <ElButton type="primary" @click="apply">确定</ElButton>
    </footer>
  </section>
</template>

<style scoped>
.local-filters {grid-row:1 / -1;display:grid;grid-template-rows:auto minmax(0,1fr) auto;min-width:0;min-height:0;background:#fff}
.local-filters:focus {outline:0}
.filter-title,.group-heading,.range-ends {display:flex;align-items:center;justify-content:space-between;gap:8px}
.filter-title {padding:13px 16px;border-bottom:1px solid var(--hire-line)}
.filter-title h2 {font-size:15px}
.filter-title button {width:30px;height:30px;padding:6px;border:none;border-radius:6px;background:#f2f6f7;cursor:pointer;color:var(--hire-ink)}
.filter-options {overflow:auto;overscroll-behavior:contain;padding:0 16px;scrollbar-gutter:stable}
.filter-group {min-width:0;padding:17px 0 19px;border-bottom:1px solid var(--hire-line)}
.filter-group:last-child {border-bottom:0}
.group-heading {margin-bottom:10px}
.group-heading h3 {font-size:13px}
.group-heading button {border:1px solid #e1e8eb;border-radius:5px;padding:3px 7px;background:#fff;font-size:11px;cursor:pointer;color:var(--hire-muted)}
.group-heading button[aria-pressed=true] {border-color:#badbd4;background:#edf8f5;color:var(--hire-accent-dark)}
.group-hint {color:var(--hire-muted);margin:-4px 0 9px;font-size:11px}
.filter-choices {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px}
.wide-choices {grid-template-columns:repeat(2,minmax(0,1fr))}
.facet-choice {cursor:pointer;min-height:32px;min-width:0;padding:4px 5px;border:1px solid #e6ebee;border-radius:6px;
  color:#344654;background:#f7f9fa;font-size:12px;text-align:center;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.facet-choice:hover {border-color:#91c6bf;background:#f1f8f6}
.facet-choice[aria-pressed=true] {border-color:#8bbfb6;background:var(--hire-soft);color:var(--hire-accent-dark);font-weight:600}
.filter-range output {color:var(--hire-accent-dark);font-weight:600;font-size:12px;font-variant-numeric:tabular-nums}
.range-ends {color:var(--hire-muted);font-size:11px;margin-top:1px}
.filter-range :deep(.el-slider) {padding-inline:9px;height:36px;--el-slider-button-size:15px;--el-slider-height:4px}
.filter-range :deep(.el-slider__button-wrapper) {touch-action:none}
.local-filter-actions {display:grid;grid-template-columns:1fr 2fr;gap:9px;padding:13px 15px;border-top:1px solid var(--hire-line)}
.local-filter-actions .el-button {margin:0}
</style>
