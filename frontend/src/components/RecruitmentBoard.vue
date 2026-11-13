<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQueryRaw } from 'vue-router'
import {
  ArrowForwardOutline, BriefcaseOutline, CalendarOutline, CheckmarkCircleOutline, ContractOutline,
  DocumentsOutline, ExpandOutline, PeopleOutline, RefreshOutline, TimeOutline, TrendingUpOutline,
} from '@vicons/ionicons5'
import StatLink from './StatLink.vue'
import { fetchStatistics, type RecruitmentJob, type Statistics, type TrendPoint } from '@/api/statistics'
import { jobStatusLabel } from '@/api/hr'
import { useAuthStore } from '@/stores/auth'

const props = defineProps<{ detailed?: boolean }>()
const route = useRoute(), router = useRouter(), auth = useAuthStore()
const data = ref<Statistics>()
const pending = ref(true)
const error = ref('')
const board = ref<HTMLElement>()
const fullscreen = ref(false)
const draft = ref({start:'',end:''})
let request = 0
const read = (key: string) => typeof route.query[key] === 'string' ? route.query[key] as string : ''
const admin = computed(() => data.value?.scope === 'super_admin' || auth.isSuperAdmin)
const view = computed(() => {
  const requested = read('view')
  return ['trend','jobs','stages',admin.value ? 'accounts' : 'work'].includes(requested) ? requested : 'overview'
})
const department = computed(() => read('department'))
const job = computed(() => read('job'))
const range = computed(() => read('start') && read('end') ? 'custom' : ['7','30','90'].includes(read('days')) ? read('days') : '30')
const unit = computed(() => read('unit'))
const chosenDepartment = computed(() => data.value?.departments.find(d => String(d.id) === department.value)?.name)
const chosenJob = computed(() => data.value?.job_options.find(j => String(j.id) === job.value)?.title)
const scopeText = computed(() => chosenJob.value || chosenDepartment.value || (admin.value ? '全公司' : '已授权部门'))
const digits = new Intl.NumberFormat('zh-CN')
const number = (v: number | null | undefined) => v == null ? '—' : digits.format(v)
const percent = (part:number, total:number) => total ? Math.round(part/total*100) : 0
const ranges = [{ id:'7', label:'近 7 天' }, { id:'30', label:'近 30 天' }, { id:'90', label:'近 90 天' }]
const views = computed(() => [
  { id:'overview', label:'招聘总览' }, { id:'trend', label:'时间趋势' },
  { id:'jobs', label:'岗位进度' }, { id:'stages', label:'阶段分布' },
  ...(admin.value ? [{ id:'accounts', label:'管理员覆盖' }] : [{ id:'work', label:'待办统计' }]),
])
const stageRows = computed(() => data.value ? [
  { id:'review', label:'待审核', value:data.value.totals.review, color:'slate' },
  { id:'progress', label:'推进中', value:data.value.totals.progress, color:'blue' },
  { id:'hired', label:'录取', value:data.value.totals.hired_applications, color:'teal' },
  { id:'rejected', label:'淘汰', value:data.value.totals.rejected, color:'rose' },
] : [])
const cards = computed(() => {
  const t = data.value?.totals
  if (!t) return []
  const all = [
    { key:'open', label:'开放岗位', value:t.open_jobs, unit:'个', note:`未开始 ${t.paused_jobs} · 已结束 ${t.closed_jobs}`, icon:BriefcaseOutline, tone:'blue', tab:'jobs', metric:'open' },
    { key:'plan', label:'开放岗位计划', value:t.open_jobs && t.unplanned===t.open_jobs ? null : t.planned, unit:'人', note:`${t.unplanned} 个岗位未设置计划`, icon:PeopleOutline, tone:'blue', tab:'jobs', metric:'plan' },
    { key:'hired', label:'本期录取', value:t.period_hired, unit:'人', note:`当前有效录取 ${t.hired} 人`, icon:CheckmarkCircleOutline, tone:'teal', tab:'trend', metric:'hired' },
    { key:'gap', label:'已知招聘缺口', value:t.open_jobs && t.unplanned===t.open_jobs ? null : t.gap, unit:'人', note:`${t.unfilled_jobs} 个岗位未招满 · ${t.overdue} 个超期`, icon:TrendingUpOutline, tone:'amber', tab:'jobs', metric:t.open_jobs && t.unplanned===t.open_jobs ? 'unplanned' : 'gap' },
  ]
  return admin.value ? [...all,
    { key:'files', label:'在库简历', value:t.resumes, unit:'份', note:`${t.people} 位候选人 · ${t.applications} 份申请`, icon:DocumentsOutline, tone:'slate', tab:'stages', metric:'files' },
    { key:'admins', label:'部门管理员', value:data.value?.accounts?.admins, unit:'人', note:`${data.value?.accounts?.uncovered ?? 0} 个部门未覆盖`, icon:PeopleOutline, tone:'slate', tab:'accounts', metric:'admins' },
  ] : [...all,
    { key:'review', label:'待审核申请', value:t.review, unit:'份', note:`另有 ${t.verify} 份申请待核对`, icon:DocumentsOutline, tone:'slate', tab:'work', metric:'review' },
    { key:'progress', label:'推进中申请', value:t.progress, unit:'份', note:`本期新增 ${t.new_applications} 份申请`, icon:TimeOutline, tone:'blue', tab:'stages', metric:'progress' },
  ]
})
const distribution = computed(() => {
  const byDepartment = !department.value && !job.value
  const rows = byDepartment ? (data.value?.department_rows ?? []).map(d => ({
    id:d.id,title:d.name,department:d.id,job:undefined,hired:d.period_hired,applications:d.new_applications,
  })) : (data.value?.jobs ?? []).map(j => ({
    id:j.id,title:j.title,department:j.department_id,job:j.id,hired:j.period_hired,applications:j.new_applications,
  }))
  return rows.filter(r => r.applications || r.hired).sort((a,b) => b.hired-a.hired || b.applications-a.applications)
})
const jobFilter = computed(() => read('metric'))
const gapRows = computed(() => {
  const rows = data.value?.jobs ?? []
  if (view.value === 'jobs') {
    if (jobFilter.value === 'gap') return rows.filter(r => r.gap && r.gap > 0)
    if (jobFilter.value === 'open' || jobFilter.value === 'plan') return rows.filter(r => r.status === 'open')
    if (jobFilter.value === 'unplanned') return rows.filter(r => r.status === 'open' && r.planned_headcount === null)
    return rows
  }
  return rows.filter(r => r.status === 'open' && (r.gap === null || r.gap > 0)).slice(0, 5)
})
const peak = computed(() => Math.max(1, ...(data.value?.trend ?? []).flatMap(x => [x.applications,x.hired])))
const chartMax = computed(() => Math.ceil(peak.value / 4) * 4)
const chartTicks = computed(() => Array.from({length:5}, (_,i) => chartMax.value * (4-i)/4))
const shortDay = (date:string) => date.slice(5).replace('-', '/')
const periodText = (p:TrendPoint) => p.start_date === p.end_date ? p.start_date : p.start_date + ' 至 ' + p.end_date
function link(viewName: string, extra: LocationQueryRaw = {}) {
  const { view: _view, metric: _metric, ...context } = route.query
  return { name:'statistics', query:{ ...context, view:viewName, ...extra } }
}
function jobLink(row:RecruitmentJob) {
  return props.detailed ? (admin.value ? {name:'jobs',query:{job:row.id}} : {name:'applications',query:{job:row.id}})
    : link('jobs',{department:row.department_id,job:row.id})
}
function appLink(extra: LocationQueryRaw = {}) {
  return { name:'applications',query:{ ...(department.value ? {department:department.value} : {}),
    ...(job.value ? {job:job.value} : {}),...extra } }
}
function stageLink(id:string) {
  return !props.detailed || view.value !== 'stages' || admin.value ? link('stages',{metric:id}) : appLink({stage:id})
}
function go(query: LocationQueryRaw) { return router.replace({ query:{...route.query,...query} }) }
function chooseDepartment(e:Event) { void go({department:(e.target as HTMLSelectElement).value || undefined,job:undefined}) }
function chooseJob(e:Event) { void go({job:(e.target as HTMLSelectElement).value || undefined}) }
function chooseRange(days:string) { void go({days,start:undefined,end:undefined,unit:undefined}) }
function custom() { if (draft.value.start && draft.value.end) void go({start:draft.value.start,end:draft.value.end,days:undefined,unit:undefined}) }
function chartLink(p:TrendPoint) {
  return link('trend',{start:p.start_date,end:p.end_date,days:undefined,unit:'day'})
}
function setFullscreen() {
  if (document.fullscreenElement === board.value) void document.exitFullscreen().catch(() => { error.value='无法退出全屏，请按 Esc。' })
  else if (board.value?.requestFullscreen) void board.value.requestFullscreen().catch(() => { error.value='当前窗口无法进入全屏。' })
}
function onFullscreen() { fullscreen.value = document.fullscreenElement === board.value }
async function load() {
  const current = ++request
  pending.value = true; error.value=''
  const params = new URLSearchParams()
  for (const [key,value] of [['department_id',department.value],['job_id',job.value],['days',range.value==='custom'?'':range.value],
    ['start',read('start')],['end',read('end')],['granularity',unit.value]]) if (value) params.set(key,value)
  try {
    const result = await fetchStatistics(params)
    if (current === request) {
      data.value = result
      draft.value = {start:result.period.start,end:result.period.end}
    }
  } catch(e) { if (current === request) { data.value = undefined; error.value = (e as Error).message } }
  finally { if (current === request) pending.value=false }
}
watch(() => [department.value,job.value,range.value,read('start'),read('end'),unit.value], load, { immediate:true })
onMounted(() => document.addEventListener('fullscreenchange', onFullscreen))
onBeforeUnmount(() => { request++; document.removeEventListener('fullscreenchange',onFullscreen) })
const work = computed(() => data.value ? [
  { label:'待审核', value:data.value.totals.review, to:appLink({stage:'review'}), note:'查看尚未审核的申请' },
  { label:'待复核', value:data.value.totals.verify, to:{name:'review',query:{department:department.value || undefined,job:job.value || undefined}}, note:'核对存在疑问的字段' },
  { label:'解析失败', value:data.value.totals.failed, to:appLink({parse:'failed'}), note:'查看解析失败的申请' },
  { label:'7 天未推进', value:data.value.totals.stalled, to:link('work',{metric:'stalled'}), note:'待审核或推进中，连续 7 天未变更招聘阶段' },
] : [])
const shortcuts = computed(() => admin.value ? [
  {label:'岗位管理',note:'查看所有部门的岗位',to:'/jobs'},
  {label:'系统设置',note:'管理部门、管理员与权限',to:'/admin'},
  {label:'审计日志',note:'追溯系统操作记录',to:'/audit'},
] : [
  {label:'简历管理',note:'导入简历、跟进候选人',to:'/applications'},
  {label:'岗位管理',note:'维护招聘计划与面试轮次',to:'/jobs'},
  {label:'复核队列',note:'核对低置信或冲突字段',to:'/review'},
])
</script>

<template>
  <main ref="board" class="recruitment-board" :class="{ 'report-view':detailed }" :aria-busy="pending">
    <div class="board-inner">
      <header class="board-heading">
        <div>
          <div class="board-context"><span class="context-dot"></span>{{ admin ? '公司总览' : '部门工作台' }}<span class="scope-label">{{ scopeText }}</span></div>
          <h1>{{ detailed ? '统计管理' : '招聘运营看板' }}</h1>
          <p>{{ admin ? '查看公司招聘进度、简历规模与管理员覆盖。' : '跟进岗位招聘进度、面试阶段与待处理申请。' }}</p>
        </div>
        <div class="heading-tools">
          <RouterLink v-if="!detailed" :to="link('overview')" class="rs-button primary">统计管理 <ArrowForwardOutline aria-hidden="true"/></RouterLink>
          <RouterLink v-else :to="{name:'dashboard',query:{department:department || undefined,job:job || undefined}}" class="rs-button">返回工作台</RouterLink>
          <button class="rs-button icon-button" type="button" aria-label="刷新统计" title="刷新统计" :disabled="pending" @click="load"><RefreshOutline aria-hidden="true"/></button>
          <button class="rs-button icon-button" type="button" :aria-label="fullscreen ? '退出全屏' : '全屏看板'" :title="fullscreen ? '退出全屏' : '全屏看板'" @click="setFullscreen">
            <ContractOutline v-if="fullscreen" aria-hidden="true"/><ExpandOutline v-else aria-hidden="true"/>
          </button>
        </div>
      </header>

      <div class="board-filters" aria-label="统计筛选">
        <div class="date-ranges">
          <CalendarOutline aria-hidden="true"/>
          <button v-for="r in ranges" :key="r.id" type="button" :aria-pressed="range===r.id" @click="chooseRange(r.id)">{{ r.label }}</button>
        </div>
        <div v-if="data" class="scope-filters">
          <label>部门<select :value="department" @change="chooseDepartment">
            <option value="">{{ admin ? '全部部门' : '全部授权部门' }}</option><option v-for="d in data.departments" :key="d.id" :value="d.id">{{ d.name }}</option>
          </select></label>
          <label>岗位<select :value="job" @change="chooseJob">
            <option value="">全部岗位</option><option v-for="j in data.job_options" :key="j.id" :value="j.id">{{ j.title }}</option>
          </select></label>
        </div>
      </div>
      <form v-if="detailed" class="custom-period" @submit.prevent="custom">
        <label>开始日期<input v-model="draft.start" type="date" :max="draft.end || undefined" required></label>
        <label>结束日期<input v-model="draft.end" type="date" :min="draft.start || undefined" required></label>
        <button type="submit" class="rs-button">查看日期范围</button>
        <label>趋势粒度<select :value="unit" @change="go({unit:($event.target as HTMLSelectElement).value || undefined})">
          <option value="">自动</option><option value="day">按日</option><option value="week">按周</option><option value="month">按月</option>
        </select></label>
      </form>
      <p v-if="error" class="rs-error" role="alert">{{ error }} <button class="rs-button" @click="go({department:undefined,job:undefined,start:undefined,end:undefined,unit:undefined,days:undefined})">重置筛选</button></p>
      <div v-if="!data && pending" class="board-loading" role="status">正在汇总招聘数据…</div>
      <template v-if="data">
        <div class="summary-grid" aria-label="关键指标">
          <StatLink v-for="item in cards" :key="item.key" :to="link(item.tab,{metric:item.metric})" class="summary-item" :class="item.tone"
            :name="item.label + ' ' + number(item.value) + ' ' + item.unit + '，查看明细'"
            :preview="item.label + '：' + number(item.value) + ' ' + item.unit + '。' + item.note">
            <div class="summary-label"><component :is="item.icon" aria-hidden="true"/>{{ item.label }}</div>
            <div class="summary-value">{{ number(item.value) }} <small>{{ item.unit }}</small></div>
            <div class="summary-note">{{ item.note }}</div>
          </StatLink>
        </div>

        <nav v-if="detailed" class="report-tabs" aria-label="统计分类">
          <RouterLink v-for="tab in views" :key="tab.id" :to="link(tab.id)" :aria-current="view === tab.id ? 'page' : undefined">{{ tab.label }}</RouterLink>
        </nav>
        <div class="board-grid">
          <section v-if="!detailed || ['overview','trend'].includes(view)" class="board-panel trend-panel">
            <header class="panel-heading"><div><h2>申请与录取趋势</h2><p>{{ data.period.start }} 至 {{ data.period.end }} · 北京时间</p></div>
              <RouterLink v-if="!detailed || view==='overview'" :to="link('trend')">查看趋势数据</RouterLink></header>
            <div class="trend-legend"><span class="legend-item"><i></i>新增申请 {{ number(data.totals.new_applications) }} 份</span>
              <span class="legend-item hired"><i></i>本期录取 {{ number(data.totals.period_hired) }} 人</span></div>
            <div class="chart-scroll" aria-label="申请与录取柱状图" tabindex="0">
              <div class="trend-chart" :style="{ minWidth: Math.max(270,data.trend.length*34+42)+'px' }">
                <div class="axis-labels" aria-hidden="true"><span v-for="tick in chartTicks" :key="tick">{{ tick }}</span></div>
                <div class="chart-grid" aria-hidden="true"><i v-for="tick in chartTicks" :key="tick"></i></div>
                <div class="columns" :style="{gridTemplateColumns:'repeat('+data.trend.length+',minmax(0,1fr))'}">
                  <StatLink v-for="point in data.trend" :key="point.bucket" class="column-target" :to="chartLink(point)"
                    :preview="periodText(point)+'：新增申请 '+point.applications+' 份，录取 '+point.hired+' 人。'">
                    <span class="bars" aria-hidden="true">
                      <span class="bar intake" :style="{height:point.applications/chartMax*100+'%'}"></span>
                      <span class="bar accepted" :style="{height:point.hired/chartMax*100+'%'}"></span>
                    </span>
                    <span class="bucket-label">{{ shortDay(point.start_date) }}</span>
                  </StatLink>
                </div>
              </div>
            </div>
            <div v-if="detailed && view==='trend' && distribution.length" class="distribution">
              <h3>{{ department || job ? '按岗位分布' : '按部门分布' }}</h3>
              <StatLink v-for="row in distribution" :key="row.id" class="department-line"
                :to="link('trend',{department:row.department,job:row.job})"
                :preview="row.title+'：新增申请 '+row.applications+' 份，首次录取 '+row.hired+' 人。'">
                <span>{{ row.title }}</span><span class="department-track" aria-hidden="true"><i :style="{width:percent(row.hired,data.totals.period_hired)+'%'}"></i></span>
                <small>录取 {{ row.hired }} 人 · 申请 {{ row.applications }} 份</small>
              </StatLink>
            </div>
            <p v-if="!data.totals.new_applications && !data.totals.period_hired" class="subtle-text">所选日期范围内暂无新增申请或录取记录。</p>
            <div v-if="detailed && view==='trend'" class="board-table-wrap">
              <table class="board-table"><thead><tr><th>日期范围</th><th>新增申请</th><th>首次录取</th></tr></thead>
                <tbody><tr v-for="p in data.trend" :key="p.bucket"><td><StatLink :to="chartLink(p)" :preview="periodText(p)+' · '+p.applications+' 份申请 · '+p.hired+' 人录取'">{{ periodText(p) }}</StatLink></td><td>{{ number(p.applications) }}</td><td>{{ number(p.hired) }}</td></tr></tbody>
              </table>
            </div>
          </section>

          <section v-if="!detailed || ['overview','stages'].includes(view)" class="board-panel stage-panel">
            <header class="panel-heading"><div><h2>当前招聘阶段</h2><p>按申请计数 · 截至当前</p></div><RouterLink v-if="!detailed" :to="link('stages')">查看分布</RouterLink></header>
            <div class="stage-rail" aria-hidden="true">
              <span v-for="s in stageRows" :key="s.id" :class="s.color" :style="{flexGrow:s.value, flexBasis:'0px'}"></span>
            </div>
            <div class="stage-list">
              <StatLink v-for="s in stageRows" :key="s.id" :class="['stage-line',{ highlighted:read('metric')===s.id }]" :to="stageLink(s.id)"
                :preview="s.label+'：'+s.value+' 份申请，占 '+percent(s.value,data.totals.applications)+'%'">
                <span><i :class="s.color"></i>{{ s.label }}</span><strong>{{ number(s.value) }}</strong><small>{{ percent(s.value,data.totals.applications) }}%</small>
              </StatLink>
            </div>
            <p v-if="!job" class="subtle-text">选择具体岗位后，可按该岗位的面试轮次查看。</p>
            <div v-if="job && data.rounds.length" class="round-list" aria-label="岗位面试轮次">
              <StatLink v-for="round in data.rounds" :key="round.id" :class="['compact-link',{ highlighted:read('metric')==='interview:'+round.id }]"
                :to="admin ? link('stages',{metric:'interview:'+round.id}) : appLink({stage:'interview:'+round.id})" :preview="round.name+'：'+round.total+' 份申请'">
                {{ round.name }}<strong>{{ round.total }}</strong>
              </StatLink>
            </div>
            <div v-if="detailed && view==='stages'" class="stage-list files-list" aria-label="简历规模">
              <StatLink class="stage-line" :to="admin ? link('jobs') : appLink()" :preview="'不同原始文件数：'+data.totals.resumes"><span>简历文件</span><strong>{{ number(data.totals.resumes) }}</strong></StatLink>
              <StatLink class="stage-line" :to="admin ? link('jobs') : appLink()" :preview="'按已识别候选人去重：'+data.totals.people"><span>候选人数</span><strong>{{ number(data.totals.people) }}</strong></StatLink>
            </div>
          </section>

          <section v-if="!detailed || ['overview','jobs'].includes(view)" class="board-panel gaps-panel">
            <header class="panel-heading"><div><h2>{{ detailed && view==='jobs' ? '岗位招聘进度' : '岗位缺口' }}</h2><p>开放岗位按有效录取计算，未设置计划单列。</p></div>
              <RouterLink v-if="!detailed || view==='overview'" :to="link('jobs')">查看全部岗位</RouterLink></header>
            <div class="board-table-wrap">
              <table class="board-table"><thead><tr><th>岗位</th><th>计划</th><th>录取</th><th>还缺</th><th v-if="detailed">完成日期</th><th v-if="detailed && !admin">管理</th></tr></thead>
                <tbody><tr v-for="row in gapRows" :key="row.id">
                  <td><StatLink :to="jobLink(row)" :preview="row.title+'：计划 '+(row.planned_headcount ?? '未设置')+'，录取 '+row.hired+'，缺口 '+(row.gap ?? '—')">{{ row.title }}</StatLink><small>{{ row.department_name }}<template v-if="detailed"> · {{ jobStatusLabel(row.status) }}</template></small></td>
                  <td><StatLink :to="jobLink(row)" :preview="row.title+' 的计划招聘人数'">{{ row.planned_headcount === null ? '未设置' : number(row.planned_headcount) }}</StatLink></td>
                  <td><StatLink :to="admin ? link('stages',{department:row.department_id,job:row.id,metric:'hired'}) : {name:'applications',query:{job:row.id,stage:'hired'}}"
                    :preview="row.title+'：当前有效录取 '+row.hired+' 人。'">{{ number(row.hired) }}</StatLink></td>
                  <td><StatLink :to="jobLink(row)" :class="{ 'gap-number':(row.gap || 0)>0 }"
                    :preview="row.title+'：剩余缺口 '+(row.gap ?? '未设置或岗位未开放')">{{ number(row.gap) }}</StatLink></td>
                  <td v-if="detailed">{{ row.target_date || '—' }} <span v-if="row.overdue" class="gap-number">超期</span></td>
                  <td v-if="detailed && !admin"><StatLink :to="{name:'jobs',query:{job:row.id,edit:row.id}}" :preview="'维护 '+row.title+' 的招聘计划'">编辑计划</StatLink></td>
                </tr></tbody>
              </table>
              <p v-if="!gapRows.length" class="board-empty">{{ jobFilter==='gap' || !detailed ? '当前没有已知缺口岗位。' : '此范围暂无岗位。' }}</p>
            </div>
          </section>

          <section v-if="admin && (!detailed || ['overview','accounts'].includes(view))" class="board-panel coverage-panel">
            <header class="panel-heading"><div><h2>{{ detailed && view==='accounts' ? '管理员覆盖' : '各部门招聘进度' }}</h2><p>{{ detailed && view==='accounts' ? '仅统计启用的部门管理员。' : '开放岗位的已完成计划与缺口。' }}</p></div>
              <RouterLink v-if="!detailed || view==='overview'" :to="link('accounts')">管理员覆盖</RouterLink>
              <RouterLink v-else :to="{path:'/admin',hash:'#accounts'}">管理账号</RouterLink></header>
            <div v-if="data.accounts && detailed && view==='accounts'" class="coverage-note">启用超管 {{ number(data.accounts.super_admins) }} 人 · 停用账号 {{ number(data.accounts.disabled) }} 个 · 未覆盖部门 {{ number(data.accounts.uncovered) }} 个</div>
            <div class="department-list">
              <StatLink v-for="d in data.department_rows" :key="d.id" class="department-line"
                :to="detailed && view==='accounts' ? {path:'/admin',query:{department:d.id},hash:'#accounts'} : link('jobs',{department:d.id,job:undefined})"
                :preview="d.name+'：开放 '+d.open_jobs+' 岗；计划 '+d.planned+'，完成 '+d.filled+'，缺口 '+d.gap+'；未设计划 '+d.unplanned+' 岗；管理员 '+d.admins+' 人。'">
                <span>{{ d.name }}</span>
                <template v-if="detailed && view==='accounts'"><strong>{{ d.admins }} 人</strong><small>{{ d.status==='disabled' ? '已停用' : d.admins ? '已覆盖' : '未覆盖' }}</small></template>
                <template v-else>
                  <span class="department-track" aria-hidden="true"><i :style="{width:percent(d.filled,d.planned)+'%'}"></i></span>
                  <small>{{ d.planned ? d.filled+' / '+d.planned : d.unplanned ? '未设计划' : '—' }}</small>
                </template>
              </StatLink>
              <p v-if="!data.department_rows.length" class="board-empty">此范围暂无部门。</p>
            </div>
          </section>

          <section v-if="!admin && (!detailed || ['overview','work'].includes(view))" class="board-panel work-panel">
            <header class="panel-heading"><div><h2>招聘待办</h2><p>点击查看待处理的申请</p></div><RouterLink v-if="!detailed" :to="link('work')">查看待办统计</RouterLink></header>
            <div class="stage-list"><StatLink v-for="item in work" :key="item.label" :to="item.to" class="stage-line" :preview="item.label+'：'+item.value+' 份。'+item.note">
              <span>{{ item.label }}</span><strong>{{ number(item.value) }}</strong><small>份</small>
            </StatLink></div>
            <div v-if="detailed && view==='work' && read('metric')==='stalled'" class="stage-list files-list" aria-label="未推进申请按岗位分布">
              <StatLink v-for="row in data.jobs.filter(j=>j.stalled)" :key="row.id" class="compact-link"
                :to="{name:'applications',query:{job:row.id,stalled:'1'}}" :preview="row.title+'：'+row.stalled+' 份连续 7 天未推进的申请'">
                {{ row.title }} <strong>{{ row.stalled }} 份</strong>
              </StatLink>
              <p v-if="!data.totals.stalled" class="subtle-text">当前没有连续 7 天未推进的申请。</p>
            </div>
          </section>
        </div>
        <section v-if="!detailed || view==='overview'" class="shortcut-panel" aria-label="快捷入口">
          <h2>快捷入口</h2>
          <StatLink v-for="item in shortcuts" :key="item.to" :to="item.to" class="shortcut" :preview="item.note">
            <span><strong>{{ item.label }}</strong><small>{{ item.note }}</small></span><ArrowForwardOutline aria-hidden="true"/>
          </StatLink>
        </section>
        <p class="board-footnote">时间筛选仅影响期间趋势和本期录取。趋势按首次录取记录计，历史撤回不改变首次录取时间；岗位缺口按当前有效录取计算。</p>
      </template>
    </div>
  </main>
</template>

<style scoped>
.recruitment-board {
  --board-navy:#203552; --board-muted:#516680; --board-line:#dbe4ef; --board-paper:#fff;
  --board-wash:#f3f6fb; --board-blue:#5379bb; --board-teal:#138674; --board-amber:#a46121; --board-rose:#b35d6b;
  min-width:0; height:100%; overflow:auto; color:var(--board-navy); background:var(--board-wash);
}
.board-inner { max-width:1696px; margin:auto; padding:clamp(16px,2.2vw,32px); display:grid; gap:16px; }
.board-heading { display:flex; align-items:flex-end; justify-content:space-between; gap:16px; }
.board-heading h1 { font-size:clamp(25px,3vw,39px); letter-spacing:-.035em; line-height:1.22; margin:4px 0 6px; }
.board-heading p,.panel-heading p,.subtle-text,.board-footnote { color:var(--board-muted); font-size:13px; }
.board-context { display:flex; gap:8px; flex-wrap:wrap; align-items:center; font-size:12px; font-weight:650; }
.context-dot { width:7px; height:7px; border-radius:50%; background:var(--board-teal); }
.scope-label { border-left:1px solid #bac8da; padding-left:8px; color:var(--board-muted); }
.heading-tools,.board-filters,.scope-filters,.date-ranges,.custom-period { display:flex; align-items:center; flex-wrap:wrap; gap:9px; }
.heading-tools { justify-content:flex-end; }
.heading-tools .rs-button { display:inline-flex; align-items:center; gap:6px; text-decoration:none; min-height:44px; }
.heading-tools svg,.date-ranges svg,.summary-label svg,:deep(.shortcut svg) { width:17px; height:17px; flex:none; }
.heading-tools .icon-button { width:44px; justify-content:center; padding:0; }
.board-filters { justify-content:space-between; padding:7px 10px; border:1px solid var(--board-line); background:#e9eff7; border-radius:9px; }
.date-ranges { gap:4px; }
.date-ranges svg { margin:0 5px; color:var(--board-muted); }
.date-ranges button { min-height:38px; padding:0 10px; border:1px solid transparent; border-radius:5px; background:transparent; color:var(--board-navy); cursor:pointer; }
.date-ranges button[aria-pressed=true] { background:#fff; border-color:#c4d2e6; font-weight:700; }
.scope-filters label,.custom-period label { display:flex; align-items:center; gap:6px; font-size:13px; color:var(--board-muted); }
.scope-filters select,.custom-period select,.custom-period input { min-height:36px; max-width:220px; padding:4px 8px; border:1px solid #c8d5e4; border-radius:6px; background:#fff; color:var(--board-navy); }
.custom-period { padding:4px 0; }
.summary-grid { display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); border:1px solid var(--board-line); border-radius:11px; background:var(--board-paper); }
:deep(.summary-item) { position:relative; min-width:0; padding:13px clamp(9px,1.2vw,19px) 12px; text-decoration:none; color:var(--board-navy); border-right:1px solid var(--board-line); }
:deep(.summary-item:last-child) { border-right:0; }
:deep(.summary-item::before) { content:''; position:absolute; top:-1px; left:13px; width:25px; height:3px; border-radius:3px; background:var(--metric-color,var(--board-blue)); }
:deep(.summary-item.amber) { --metric-color:var(--board-amber); }
:deep(.summary-item.teal) { --metric-color:var(--board-teal); }
:deep(.summary-item.slate) { --metric-color:#7389a6; }
.summary-label { display:flex; align-items:center; gap:5px; font-size:12px; font-weight:700; }
.summary-label svg { color:var(--metric-color,var(--board-blue)); }
.summary-value { font-size:clamp(22px,2.55vw,36px); font-weight:750; font-variant-numeric:tabular-nums; line-height:1.35; }
.summary-value small { font-size:12px; font-weight:550; color:var(--board-muted); }
.summary-note { font-size:12px; color:var(--board-muted); line-height:1.45; }
.board-grid { display:grid; grid-template-columns:minmax(0,1.68fr) minmax(0,1fr); align-items:start; gap:16px; }
.board-panel { padding:15px 16px; background:var(--board-paper); border:1px solid var(--board-line); border-radius:11px; min-width:0; }
.panel-heading { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:10px; }
.panel-heading h2,.shortcut-panel h2 { font-size:17px; line-height:1.3; margin:0 0 4px; }
.panel-heading a,.board-table :deep(a) { color:#315989; font-size:13px; text-underline-offset:3px; }
.panel-heading a { white-space:nowrap; }
.trend-legend { display:flex; gap:14px; flex-wrap:wrap; font-size:12px; color:var(--board-muted); }
.legend-item { display:inline-flex; align-items:center; gap:6px; }
.legend-item i,:deep(.stage-line i) { width:9px; height:9px; border-radius:2px; background:var(--board-blue); }
.legend-item.hired i { background:var(--board-teal); border-radius:50%; }
.chart-scroll { overflow:auto; margin-top:8px; }
.trend-chart { height:177px; position:relative; display:grid; grid-template-columns:34px 1fr; }
.axis-labels,.chart-grid { height:calc(100% - 23px); display:flex; justify-content:space-between; flex-direction:column; font-size:11px; color:var(--board-muted); }
.chart-grid { position:absolute; inset:0 0 auto 34px; pointer-events:none; }
.chart-grid i { border-top:1px solid #e2e9f2; }
.columns { display:grid; align-items:end; grid-column:2; z-index:1; }
:deep(.column-target) { height:177px; display:grid; grid-template-rows:1fr 23px; align-items:end; justify-items:center; color:var(--board-muted); text-decoration:none; }
.bars { display:flex; align-items:flex-end; justify-content:center; height:100%; gap:3px; width:100%; }
.bar { width:min(29%,15px); border-radius:3px 3px 0 0; }
.bar.intake { background:var(--board-blue); }
.bar.accepted { background:var(--board-teal); border-radius:7px 7px 0 0; }
.bucket-label { font-size:11px; white-space:nowrap; }
.stage-rail { display:flex; gap:2px; height:11px; background:#eaf0f6; border-radius:4px; overflow:hidden; margin:17px 0 10px; }
.stage-rail span,:deep(.stage-line i) { background:var(--stage-color); }
.slate { --stage-color:#74869e; }.blue { --stage-color:var(--board-blue); }.teal { --stage-color:var(--board-teal); }.rose { --stage-color:var(--board-rose); }
.stage-list,.round-list,.department-list { display:grid; }
:deep(.stage-line),:deep(.compact-link),:deep(.department-line) { display:grid; min-height:42px; align-items:center; gap:8px; border-bottom:1px solid #e8edf3; color:var(--board-navy); text-decoration:none; font-size:13px; padding:2px 5px; }
:deep(.stage-line) { grid-template-columns:minmax(0,1fr) 48px 43px; }
:deep(.stage-line span) { display:flex; align-items:center; gap:7px; }
:deep(.stage-line strong),:deep(.stage-line small),:deep(.department-line small) { text-align:right; font-variant-numeric:tabular-nums; }
:deep(.stage-line small),:deep(.department-line small),.board-table small { font-size:12px; color:var(--board-muted); }
:deep(.stage-line.highlighted),:deep(.compact-link.highlighted) { background:#e9f0fc; }
:deep(.compact-link) { grid-template-columns:1fr auto; }
.round-list,.files-list { margin-top:8px; }
.subtle-text { margin:9px 0 0; }
.board-table-wrap { overflow:auto; }
.board-table { width:100%; border-collapse:collapse; text-align:left; font-size:13px; }
.board-table th { background:#edf2f8; color:#435c7a; font-weight:700; }
.board-table th,.board-table td { padding:8px 9px; border-bottom:1px solid #e7edf4; }
.board-table td small { display:block; margin-top:1px; }
.board-table td:not(:first-child) { font-variant-numeric:tabular-nums; }
.board-table tbody tr:hover { background:#f4f7fc; }
.board-table td :deep(a) { display:inline-block; padding:3px 0; }
.board-table td :deep(a.gap-number) { color:#934f16; }
.distribution { margin-top:16px; }
.distribution h3 { font-size:15px; }
.gap-number { color:#934f16; font-weight:750; }
.board-empty,.coverage-note { padding:8px 5px; color:var(--board-muted); font-size:13px; }
:deep(.department-line) { grid-template-columns:minmax(85px,1fr) minmax(38px,1.2fr) auto; }
.department-track { height:8px; background:#e7edf5; border-radius:4px; overflow:hidden; }
.department-track i { display:block; height:100%; background:var(--board-teal); }
.shortcut-panel { display:grid; grid-template-columns:auto repeat(3,minmax(0,1fr)); gap:9px; align-items:center; }
.shortcut-panel h2 { padding-right:8px; }
:deep(.shortcut) { display:flex; align-items:center; justify-content:space-between; gap:7px; min-height:48px; padding:6px 10px; color:var(--board-navy); text-decoration:none; background:#eaf0f8; border:1px solid var(--board-line); border-radius:7px; }
:deep(.shortcut strong),:deep(.shortcut small) { display:block; }
:deep(.shortcut strong) { font-size:13px; }
:deep(.shortcut small) { font-size:12px; color:var(--board-muted); }
.report-tabs { display:flex; flex-wrap:wrap; gap:5px; border-bottom:1px solid #c5d2e3; }
.report-tabs a { padding:10px 12px; border-bottom:2px solid transparent; color:#425e81; text-decoration:none; font-size:14px; }
.report-tabs a[aria-current=page] { color:#244b80; border-color:#5379bb; font-weight:750; }
.report-view .board-grid:has(> :only-child) { grid-template-columns:minmax(0,1fr); }
.report-view .trend-panel .board-table-wrap { margin-top:14px; }
.board-loading { min-height:300px; display:grid; place-items:center; color:var(--board-muted); }
:deep(.summary-item:hover),:deep(.summary-item:focus-visible),:deep(.column-target:hover),:deep(.column-target:focus-visible),:deep(.stage-line:hover),:deep(.compact-link:hover),:deep(.department-line:hover),:deep(.shortcut:hover) { background:#edf3fc; }
.recruitment-board :deep(:is(a,button,select,input,[tabindex]):focus-visible) { outline:2px solid #3563a1; outline-offset:-2px; }
@media (max-width:1180px) { .summary-grid { grid-template-columns:repeat(3,minmax(0,1fr)); }:deep(.summary-item:nth-child(3)) { border-right:0; }:deep(.summary-item:nth-child(-n+3)) { border-bottom:1px solid var(--board-line); } }
@media (max-width:920px) { .board-grid { grid-template-columns:minmax(0,1fr); } }
@media (max-width:580px) {
  .board-inner { padding:14px; gap:12px; }.board-heading { align-items:flex-start; flex-direction:column; }
  .summary-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }:deep(.summary-item) { border-bottom:1px solid var(--board-line); }:deep(.summary-item:nth-child(3)) { border-right:1px solid var(--board-line); }:deep(.summary-item:nth-child(even)) { border-right:0; }:deep(.summary-item:nth-last-child(-n+2)) { border-bottom:0; }
  .board-filters { align-items:flex-start; gap:9px; }.scope-filters select,.custom-period input { max-width:160px; }
  .board-panel { padding:12px; }.panel-heading { flex-wrap:wrap; }.shortcut-panel { grid-template-columns:1fr; }
  .date-ranges button,.scope-filters select,.custom-period input,.custom-period select,:deep(.stage-line),:deep(.department-line),:deep(.compact-link) { min-height:44px; }
}
</style>
