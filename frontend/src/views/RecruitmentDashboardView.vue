<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElButton, ElConfigProvider, ElDatePicker, ElOption, ElSelect } from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { BriefcaseOutline, CalendarOutline, CheckmarkCircleOutline, DocumentsOutline,
  DownloadOutline, PeopleOutline, RefreshOutline } from '@vicons/ionicons5'
import { fetchRecruitmentDashboard, watchRecruitmentBoard, type BoardPeriod, type BoardJobStatus, type BoardStatus, type RecruitmentDashboard } from '@/api/statistics'
import { jobStatusLabel, jobStatusOptions } from '@/api/hr'

const periods = [{id:'week',label:'周'}, {id:'month',label:'月'}, {id:'year',label:'年'}] as const
const period = ref<BoardPeriod>('month')
const anchor = ref<string>()
const department = ref<number>()
const job = ref<number>()
const status = ref<BoardStatus>()
const jobStatus = ref<BoardJobStatus>()
const report = ref<RecruitmentDashboard>()
const busy = ref(true)
const error = ref('')
const selected = ref('')
const lastUpdated = ref('')
const scopeAdjustments = ref<RecruitmentDashboard['scope']['adjustments']>([])
let activeRequest:AbortController|undefined
let updates:AbortController|undefined
let reconnectTimer:number|undefined
let latestRevision = -1n

const series = [
  {key:'applications',label:'新增申请',tone:'teal'},
  {key:'interviews',label:'安排面试',tone:'blue'},
  {key:'offers',label:'首次录取',tone:'gold'},
] as const
const periodNames = { week:'周报',month:'月报',year:'年报' }
const dateFormats = { week:'YYYY 年第 ww 周',month:'YYYY 年 MM 月',year:'YYYY 年' }
const shanghaiTime = new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'})
const shanghaiDate = new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'})
const digits = new Intl.NumberFormat('zh-CN')
const number = (value:number|null) => value==null ? '—' : digits.format(value)
const departmentsById = computed(()=>new Map(report.value?.options.departments.map(item=>[item.id,item.name])))
const jobsForDepartment = computed(()=>{
  const jobs = report.value?.options.jobs.filter(item=>!department.value || item.department_id===department.value) ?? []
  const titles = new Map<string,number>()
  for(const item of jobs) titles.set(item.title,(titles.get(item.title) ?? 0)+1)
  return jobs.map(item=>({...item,label:(titles.get(item.title) ?? 0)>1
    ? item.title+' · '+departmentsById.value.get(item.department_id) : item.title}))
})
const scopeLabel = computed(()=>{
  const data = report.value
  if (!data) return '全部部门'
  const selectedJob = data.options.jobs.find(item=>item.id===data.scope.job_id)
  if (selectedJob) return [selectedJob.title,departmentsById.value.get(selectedJob.department_id)].filter(Boolean).join(' · ')
  return departmentsById.value.get(data.scope.department_id ?? -1) ?? '全部部门'
})
const scopeNotice = computed(()=>scopeAdjustments.value.map(adjustment=>{
  switch(adjustment) {
    case 'job_unavailable': return '所选岗位已不可用，已清除岗位筛选。'
    case 'job_department_changed': return '岗位所属部门已变更，已切换到当前部门。'
    case 'department_unavailable': return '所选部门已不可用，已切换到全部部门。'
  }
}).join(''))
const metrics = computed(()=>{
  const t = report.value?.totals
  if (!t) return []
  return [
    {label:'开放岗位',value:t.open_jobs,unit:'个',note:'当前招聘中',icon:BriefcaseOutline,tone:'blue'},
    {label:'招聘候选人',value:t.talent_pool,unit:'人',note:'当前人才库人数',icon:PeopleOutline,tone:'blue'},
    {label:'已入职人员',value:t.onboarded_people,unit:'人',note:'当前范围累计确认',icon:CheckmarkCircleOutline,tone:'teal'},
    {label:'本期已入职',value:t.period_onboarded,unit:'人',note:t.period_onboarded==null?'入职记录精确到月':'按已确认的入职月份',icon:CheckmarkCircleOutline,tone:'teal'},
    {label:'当前待入职',value:report.value?.stages.pending_onboard ?? 0,unit:'人',note:'面试已通过',icon:PeopleOutline,tone:'gold'},
    {label:'新增申请',value:t.applications,unit:'份',note:'按申请提交时间',icon:DocumentsOutline,tone:'teal'},
    {label:'安排面试',value:t.interviews,unit:'场',note:'按面试安排保存日期',icon:CalendarOutline,tone:'blue'},
    {label:'首次录取',value:t.offers,unit:'人',note:'按首次录取记录',icon:CheckmarkCircleOutline,tone:'gold'},
  ]
})
const totalPeople = computed(()=>Object.values(report.value?.stages ?? {}).reduce((sum,value)=>sum+value,0))
const stageRows = computed(()=>{
  const counts = report.value?.stages ?? {}
  const statuses = report.value?.options.talent_statuses ?? []
  const known = new Set<string>(statuses.map(item=>item.code))
  const rows: {code:string;label:string;color:string;count:number}[] = statuses.map(item=>({...item,count:counts[item.code] ?? 0}))
  const remaining = Object.entries(counts).reduce((sum,[code,count])=>known.has(code) ? sum : sum+count,0)
  if (remaining) rows.push({code:'',label:'其他状态',color:'#718090',count:remaining})
  return rows
})
const maxStage = computed(()=>Math.max(1,...stageRows.value.map(item=>item.count)))
const shares = (part:number,total:number) => total ? Math.round(part*100/total)+'%' : '0%'

// —— 候选人画像：性别 / 学历 / 院校层级 / 地区 ——
const profiles = computed(()=>report.value?.profiles)
const genderLabels:Record<string,string> = {male:'男',female:'女',other:'其他'}
const genderColors:Record<string,string> = {male:'#3f7f8f',female:'#b4685f',other:'#8a7f9e'}
const genderRows = computed(()=>{
  const counts = profiles.value?.gender ?? {}
  const total = Object.values(counts).reduce((sum,value)=>sum+(value ?? 0),0)
  if (!total) return []
  const order = ['male','female','other']
  const codes = [...order.filter(code=>counts[code as keyof typeof counts]), ...Object.keys(counts).filter(code=>!order.includes(code))]
  return codes.map(code=>({
    code,label:genderLabels[code] ?? code,color:genderColors[code] ?? '#718090',
    count:counts[code as keyof typeof counts] ?? 0,width:shares(counts[code as keyof typeof counts] ?? 0,total),
  }))
})
const degreeLabels:Record<string,string> = {
  phd:'博士',master:'硕士',bachelor:'本科',associate:'大专',high_school:'高中及以下',other:'其他',unknown:'未识别',
}
const degreeRows = computed(()=>{
  const rows = profiles.value?.degree ?? []
  const max = Math.max(1,...rows.map(item=>item.people))
  return rows.map(item=>({...item,label:degreeLabels[item.code] ?? item.code,width:shares(item.people,max)}))
})
const tierRows = computed(()=>{
  const tier = profiles.value?.tier
  if (!tier) return []
  const rows = [
    {code:'is_985',label:'985 院校',count:tier.is_985,color:'#9c5a4a'},
    {code:'is_211',label:'211 院校',count:tier.is_211,color:'#3f7f8f'},
    {code:'is_double_first_class',label:'双一流',count:tier.is_double_first_class,color:'#6b7f4a'},
    {code:'unknown',label:'院校未识别',count:tier.unknown,color:'#9a9a90'},
  ].filter(item=>item.count>0)
  const max = Math.max(1,...rows.map(item=>item.count))
  return rows.map(item=>({...item,width:shares(item.count,max)}))
})
const regionNames:Record<string,string> = {
  '110000':'北京','120000':'天津','130000':'河北','140000':'山西','150000':'内蒙古',
  '210000':'辽宁','220000':'吉林','230000':'黑龙江','310000':'上海','320000':'江苏',
  '330000':'浙江','340000':'安徽','350000':'福建','360000':'江西','370000':'山东',
  '410000':'河南','420000':'湖北','430000':'湖南','440000':'广东','450000':'广西',
  '460000':'海南','500000':'重庆','510000':'四川','520000':'贵州','530000':'云南',
  '540000':'西藏','610000':'陕西','620000':'甘肃','630000':'青海','640000':'宁夏',
  '650000':'新疆','710000':'台湾','810000':'中国香港','820000':'中国澳门',
  overseas:'海外院校',unknown:'地区未识别',
}
const regionLabel = (code:string | null) => code ? (regionNames[code] ?? code) : '地区未识别'
const regionRows = computed(()=>{
  const rows = profiles.value?.regions ?? []
  const max = Math.max(1,...rows.map(item=>item.people))
  return rows.map(item=>({...item,label:regionLabel(item.code),width:shares(item.people,max)}))
})
const topSchools = computed(()=>profiles.value?.schools ?? [])
const chartMax = computed(()=>{
  const peak = Math.max(1,...(report.value?.trend ?? []).flatMap(bucket=>series.map(item=>bucket[item.key])))
  return Math.ceil(peak/4)*4
})
const ticks = computed(()=>Array.from({length:5},(_,index)=>chartMax.value*(4-index)/4))
const activeBucket = computed(()=>{
  const trend = report.value?.trend ?? []
  return trend.find(item=>item.start===selected.value)
    ?? [...trend].reverse().find(item=>item.applications || item.interviews || item.offers)
    ?? trend[trend.length-1]
})
const periodText = (start:string,end:string) => start===end ? start : start+' 至 '+end
const bucketLabel = (date:string) => report.value?.period.unit==='month' ? Number(date.slice(5,7))+'月' : date.slice(5).replace('-','/')
const rateText = (part:number|null,total:number|null) => part==null || !total ? '—' : (part*100/total).toLocaleString('zh-CN',{maximumFractionDigits:1})+'%'
const completion = computed(()=>{
  const planned = report.value?.totals.planned ?? null
  const gap = report.value?.totals.gap ?? null
  const filled = planned==null || gap==null ? null : planned-gap
  return {planned,gap,filled,rate:rateText(filled,planned),width:filled==null || !planned ? '0%' : filled*100/planned+'%'}
})
function funnelRate(count:number,total:number) {
  if (!total) return '—'
  const percent = count*100/total
  if (percent>0 && percent<0.01) return '<0.01%'
  return percent.toLocaleString('zh-CN',{maximumFractionDigits:2})+'%'
}
const funnel = computed(()=>{
  const values = report.value?.funnel
  if (!values) return []
  const steps = [
    {label:'投递人数',count:values.applicants,color:'#466482',icon:DocumentsOutline},
    {label:'人才库',count:values.talent_pool,color:'#397387',icon:PeopleOutline},
    {label:'安排面试',count:values.interviewed,color:'#287d7f',icon:CalendarOutline},
    {label:'面试通过',count:values.offered,color:'#277562',icon:CheckmarkCircleOutline},
    {label:'确认入职',count:values.onboarded,color:'#245b49',icon:BriefcaseOutline},
  ]
  return steps.map((item,index)=>{
    const previous = steps[index-1]
    return {
      ...item,
      from:previous?.label,
      width:values.applicants ? item.count*100/values.applicants+'%' : '0%',
      conversion:previous ? funnelRate(item.count,previous.count) : null,
      share:funnelRate(item.count,values.applicants),
      remaining:previous?.count ? previous.count-item.count : null,
    }
  })
})
const largestFunnelGap = computed(()=>funnel.value.reduce<typeof funnel.value[number] | undefined>((largest,item)=>
  (item.remaining ?? 0)>(largest?.remaining ?? 0) ? item : largest,undefined))
const statusOptions = computed(()=>[
  {code:'unassigned' as const,label:'未进入人才库',color:'#718090'}, ...(report.value?.options.talent_statuses ?? []),
])
const selectionStatus = computed(()=>{
  const scope=report.value?.scope
  return [
    scope?.job_status ? jobStatusLabel(scope.job_status) : undefined,
    statusOptions.value.find(option=>option.code===scope?.status)?.label,
  ].filter(Boolean).join(' · ') || '全部状态'
})
const selectionRows = computed(()=>statusOptions.value.flatMap(option=>{
  const count = report.value?.selection.statuses[option.code] ?? 0
  return count ? [{...option,count}] : []
}))
const maxSelection = computed(()=>Math.max(1,...selectionRows.value.map(item=>item.count)))
function setPeriod(value:BoardPeriod) {
  if (period.value===value) return
  period.value=value
  anchor.value=undefined
}
function changeDepartment(value:number|undefined) {
  department.value=value
  job.value=undefined
  void load()
}
function changeJob(value:number|undefined) {
  job.value=value
  void load()
}
function changeStatus(value:BoardStatus|undefined) {
  status.value=value
  void load()
}
function changeJobStatus(value:BoardJobStatus|undefined) {
  jobStatus.value=value
  void load()
}
function isFuture(day:Date) {
  const localDay = [day.getFullYear(),String(day.getMonth()+1).padStart(2,'0'),String(day.getDate()).padStart(2,'0')].join('-')
  return localDay > shanghaiDate.format(new Date())
}
async function load(background=false) {
  activeRequest?.abort()
  const current = new AbortController()
  activeRequest = current
  const timeout = window.setTimeout(()=>current.abort(),8000)
  if (!background) {
    busy.value = true
    error.value = ''
  }
  const params = new URLSearchParams({period:period.value})
  if (anchor.value) params.set('anchor',anchor.value)
  if (department.value) params.set('department_id',String(department.value))
  if (job.value) params.set('job_id',String(job.value))
  if (status.value) params.set('status',status.value)
  if (jobStatus.value) params.set('job_status',jobStatus.value)
  let completed = false
  try {
    const result = await fetchRecruitmentDashboard(params,current.signal)
    if (current!==activeRequest) return
    if (current.signal.aborted) throw new Error('招聘数据加载超时。')
    const { department_id, job_id, adjustments } = result.scope
    error.value = ''
    lastUpdated.value = shanghaiTime.format(new Date())
    report.value = result
    department.value = department_id ?? undefined
    job.value = job_id ?? undefined
    if (!background || adjustments.length) {
      scopeAdjustments.value = adjustments
      selected.value = ''
    }
    completed = true
  } catch (cause) {
    if (current===activeRequest) {
      error.value = current.signal.aborted ? '招聘数据加载超时。' : (cause as Error).message
    }
  } finally {
    window.clearTimeout(timeout)
    if (current===activeRequest) {
      activeRequest = undefined
      busy.value = false
      if (completed) refreshIfChanged()
    }
  }
}
watch([period,anchor],()=>void load(),{immediate:true})
function refreshIfChanged() {
  if (!document.hidden && !activeRequest && report.value && latestRevision>BigInt(report.value.revision)) {
    void load(true)
  }
}
function connectUpdates() {
  const connection = new AbortController()
  updates = connection
  void watchRecruitmentBoard(revision=>{
    if (revision>latestRevision) {
      latestRevision = revision
      refreshIfChanged()
    }
  },connection.signal).catch(()=>{
    if (!connection.signal.aborted) reconnectTimer = window.setTimeout(connectUpdates,2000)
  })
}
onMounted(()=>{
  connectUpdates()
  document.addEventListener('visibilitychange',refreshIfChanged)
})
onBeforeUnmount(()=>{
  activeRequest?.abort()
  activeRequest = undefined
  updates?.abort()
  window.clearTimeout(reconnectTimer)
  document.removeEventListener('visibilitychange',refreshIfChanged)
})

type CsvValue = string | number | null
function csvCell(value:CsvValue) {
  if (value==null) return ''
  if (typeof value==='number') return String(value)
  const literal = /^[\t\r\n]|^\s*[=+@-]/.test(value) ? "'"+value : value
  return '"'+literal.replaceAll('"','""')+'"'
}
function exportReport() {
  const data=report.value
  if (!data || busy.value || error.value) return
  const {start,end,kind}=data.period
  const rows:CsvValue[][] = [
    ['招聘看板',periodNames[kind]],
    ['统计范围',scopeLabel.value],['归属口径','按当前岗位和部门归属'],['开始日期',start],['结束日期',end],['时区','北京时间'],
    [],['关键指标','数量','单位','统计口径'],
    ...metrics.value.map(item=>[item.label,item.value,item.unit,item.note]),
    ['开放岗位计划',data.totals.planned,'人','当前'],
    ['计划内已入职',completion.value.filled,'人','分岗位按计划人数封顶'],
    ['招聘完成率',completion.value.rate,'','计划内已入职 ÷ 计划人数'],
    ['开放岗位缺口',data.totals.gap,'人','计划减已入职，未设计划留空'],
    [],['趋势开始日期','趋势结束日期','新增申请','安排面试','首次录取'],
    ...data.trend.map(item=>[item.start,item.end,item.applications,item.interviews,item.offers]),
    [],['招聘漏斗（本期投递）','去重人数','占投递比例','较上一步转化率','较上一步尚未进入人数'],
    ...funnel.value.map(item=>[item.label,item.count,item.share,item.conversion,item.remaining]),
    [],['本期招聘分布',selectionStatus.value,data.selection.people,'人'],
    ['当前候选人状态','人数'],
    ...selectionRows.value.map(item=>[item.label,item.count]),
    [],['本期岗位分布','部门','人数'],
    ...data.selection.jobs.map(item=>[item.title,item.department,item.people]),
    [],['人才状态（当前）','人数'],
    ...stageRows.value.map(item=>[item.label,item.count]),
    [],['部门','新增申请','安排面试','首次录取'],
    ...data.departments.map(item=>[item.name,item.applications,item.interviews,item.offers]),
    [],['岗位','部门','岗位状态','计划人数','当前已入职','当前缺口','新增申请','安排面试','首次录取'],
    ...data.jobs.map(item=>[item.title,item.department,jobStatusLabel(item.status),item.planned_headcount,item.onboarded,item.gap,
      item.applications,item.interviews,item.offers]),
  ]
  const file = new Blob(['\uFEFF',rows.map(row=>row.map(csvCell).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'})
  const url = URL.createObjectURL(file)
  const link = document.createElement('a')
  link.href=url
  link.download='招聘'+periodNames[kind]+'_'+start+'_'+end+'.csv'
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(()=>URL.revokeObjectURL(url),1000)
}
</script>

<template>
  <main class="hire-page recruiting-dashboard" :aria-busy="busy">
    <header class="dashboard-heading dashboard-enter">
      <div>
        <h1>招聘看板</h1>
        <p v-if="report">{{ scopeLabel }}<span></span>{{ periodText(report.period.start,report.period.end) }}<span></span>北京时间<span></span>更新于 {{ lastUpdated }}</p>
      </div>
      <div class="dashboard-actions">
        <ElButton :icon="RefreshOutline" :loading="busy" @click="load()">刷新</ElButton>
        <ElButton type="primary" :icon="DownloadOutline" :disabled="busy || !report || Boolean(error)" @click="exportReport">导出 CSV</ElButton>
      </div>
    </header>

    <div class="dashboard-filters dashboard-enter" style="--enter-delay:65ms" aria-label="统计筛选">
      <div class="period-switch" role="group" aria-label="统计周期">
        <button v-for="item in periods" :key="item.id" type="button" :aria-pressed="period===item.id" @click="setPeriod(item.id)">{{ item.label }}</button>
      </div>
      <ElConfigProvider :locale="zhCn">
        <ElDatePicker v-model="anchor" class="dashboard-calendar" :type="period"
          :placeholder="'选择'+periodNames[period].slice(0,1)+'份'" :format="dateFormats[period]" value-format="YYYY-MM-DD"
          :editable="false" :disabled-date="isFuture" aria-label="选择统计日期"/>
      </ElConfigProvider>
      <label class="dashboard-select"><span>部门</span>
        <ElSelect :model-value="department" @update:model-value="changeDepartment" placeholder="全部部门" no-data-text="暂无可选部门" aria-label="选择统计部门" clearable filterable>
          <ElOption v-for="item in report?.options.departments ?? []" :key="item.id" :label="item.name" :value="item.id"/>
        </ElSelect>
      </label>
      <label class="dashboard-select"><span>岗位</span>
        <ElSelect :model-value="job" @update:model-value="changeJob" placeholder="全部岗位" no-data-text="暂无可选岗位" aria-label="选择统计岗位" clearable filterable>
          <ElOption v-for="item in jobsForDepartment" :key="item.id" :label="item.label" :value="item.id"/>
        </ElSelect>
      </label>
      <label class="dashboard-select"><span>岗位状态</span>
        <ElSelect :model-value="jobStatus" @update:model-value="changeJobStatus" placeholder="全部岗位状态"
          aria-label="选择岗位状态" clearable>
          <ElOption v-for="item in jobStatusOptions" :key="item.value" :label="item.label" :value="item.value"/>
        </ElSelect>
      </label>
      <label class="dashboard-select status-select"><span>候选人状态</span>
        <ElSelect :model-value="status" @update:model-value="changeStatus" placeholder="全部状态"
          aria-label="选择候选人状态" clearable>
          <ElOption v-for="item in statusOptions" :key="item.code" :label="item.label" :value="item.code"/>
        </ElSelect>
      </label>
    </div>
    <p v-if="scopeNotice" class="panel-note" role="status">{{ scopeNotice }}</p>
    <p v-if="error" class="hire-error" role="alert">{{ error }}{{ report ? ' 页面保留上次获取的数据。' : '' }} <button class="hire-inline-button" @click="load()">重试</button></p>
    <p v-if="busy && !report" class="hire-empty" role="status">正在汇总招聘数据…</p>

    <template v-if="report">
      <div class="results-grid">
        <section class="completion-card dashboard-enter" aria-labelledby="dashboard-completion">
          <header>
            <h2 id="dashboard-completion">招聘完成率</h2>
            <p>当前开放岗位 · 按已设置计划统计</p>
          </header>
          <div class="completion-body">
            <div class="completion-dial" :style="{ '--completion':completion.width }">
              <strong>{{ completion.rate }}</strong>
            </div>
            <dl class="completion-figures">
              <div><dt>计划人数</dt><dd>{{ number(completion.planned) }}</dd></div>
              <div><dt>计划内已入职</dt><dd>{{ number(completion.filled) }}</dd></div>
              <div><dt>剩余缺口</dt><dd>{{ number(completion.gap) }}</dd></div>
            </dl>
          </div>
        </section>
        <section class="dashboard-metrics" aria-label="关键指标">
          <article v-for="(item,index) in metrics" :key="item.label" class="metric-card dashboard-enter"
            :style="{'--enter-delay':(index*24+45)+'ms'}" :data-tone="item.tone">
            <div class="metric-top"><span>{{ item.label }}</span><i><component :is="item.icon" aria-hidden="true"/></i></div>
            <div class="metric-value">{{ number(item.value) }} <small v-if="item.value!=null">{{ item.unit }}</small></div>
            <p>{{ item.note }}</p>
          </article>
        </section>
      </div>

      <div class="analytics-grid">
        <section class="dashboard-panel trend-panel dashboard-enter" style="--enter-delay:230ms" aria-labelledby="dashboard-trend">
          <header class="panel-heading">
            <h2 id="dashboard-trend">招聘活动趋势</h2>
            <div class="trend-key"><span v-for="item in series" :key="item.key"><i :data-tone="item.tone"></i>{{ item.label }}</span></div>
          </header>
          <div v-if="activeBucket" class="bucket-detail" aria-live="polite">
            <strong>{{ periodText(activeBucket.start,activeBucket.end) }}</strong>
            <span v-for="item in series" :key="item.key">{{ item.label }} <b>{{ number(activeBucket[item.key]) }}</b></span>
          </div>
          <div class="chart-frame">
            <div class="chart-axis" aria-hidden="true"><span v-for="tick in ticks" :key="tick">{{ number(tick) }}</span></div>
            <div class="chart-content">
              <div class="chart-grid" aria-hidden="true"><i v-for="tick in ticks" :key="tick"></i></div>
              <div class="chart-buckets" :style="{gridTemplateColumns:'repeat('+report.trend.length+',minmax(0,1fr))'}">
                <button v-for="bucket in report.trend" :key="bucket.start" class="chart-bucket" type="button"
                  :aria-label="periodText(bucket.start,bucket.end)+'，'+series.map(item=>item.label+' '+bucket[item.key]).join('，')"
                  :aria-pressed="activeBucket?.start===bucket.start" @click="selected=bucket.start">
                  <span class="chart-bars" aria-hidden="true">
                    <i v-for="item in series" :key="item.key" :data-tone="item.tone"
                      :style="{height:bucket[item.key]/chartMax*100+'%'}"></i>
                  </span>
                  <span class="bucket-label">{{ bucketLabel(bucket.start) }}</span>
                </button>
              </div>
            </div>
          </div>
        </section>

        <section class="dashboard-panel funnel-panel dashboard-enter" style="--enter-delay:295ms" aria-labelledby="dashboard-funnel">
          <header class="panel-heading"><h2 id="dashboard-funnel">招聘转化漏斗</h2></header>
          <p class="panel-note">按本期投递候选人去重，统计其截至当前的进展</p>
          <dl v-if="report.funnel.applicants" class="funnel-overview">
            <div>
              <dt>投递至入职</dt>
              <dd><strong>{{ funnelRate(report.funnel.onboarded,report.funnel.applicants) }}</strong>
                <span>{{ number(report.funnel.onboarded) }} / {{ number(report.funnel.applicants) }} 人</span></dd>
            </div>
            <div v-if="largestFunnelGap">
              <dt>人数下降最多</dt>
              <dd><strong>{{ number(largestFunnelGap.remaining) }} <small>人</small></strong>
                <span>{{ largestFunnelGap.from }} → {{ largestFunnelGap.label }}</span></dd>
            </div>
          </dl>
          <p v-else class="funnel-empty" role="status">本期暂无投递。</p>
          <ol class="funnel-steps">
            <li v-for="item in funnel" :key="item.label" :style="{'--stage-color':item.color}">
              <div class="funnel-stage-name">
                <span><i aria-hidden="true"><component :is="item.icon"/></i>{{ item.label }}</span>
                <strong>{{ number(item.count) }} <small>人</small></strong>
              </div>
              <div class="funnel-track" aria-hidden="true"><i v-if="item.count" :style="{width:item.width}"></i></div>
              <div class="funnel-stage-rates">
                <span>占投递 <b>{{ item.share }}</b></span>
                <span v-if="item.from">较上一步 <b>{{ item.conversion }}</b></span>
                <span v-if="item.remaining!==null">尚未进入 <b>{{ number(item.remaining) }} 人</b></span>
              </div>
            </li>
          </ol>
        </section>
      </div>

      <section class="selection-section dashboard-enter" style="--enter-delay:360ms"
        aria-labelledby="dashboard-selection" :aria-busy="busy">
        <header class="panel-heading selection-heading">
          <h2 id="dashboard-selection">本期招聘分布</h2>
          <span>{{ periodText(report.period.start,report.period.end) }} · {{ scopeLabel }} · {{ selectionStatus }} · {{ number(report.selection.people) }} 人</span>
        </header>
        <div class="analytics-grid secondary">
          <section class="dashboard-panel" aria-labelledby="selection-status">
            <header class="panel-heading"><h3 id="selection-status">候选人状态</h3></header>
            <p class="panel-note">按本期投递候选人去重 · 取当前状态</p>
            <ul v-if="selectionRows.length" class="stage-list selection-stages">
              <li v-for="item in selectionRows" :key="item.code">
                <button type="button" class="text-button" @click="changeStatus(status===item.code ? undefined : item.code)"
                  :aria-pressed="status===item.code">{{ item.label }}</button>
                <div class="stage-track" aria-hidden="true"><i :style="{width:shares(item.count,maxSelection),background:item.color}"></i></div>
                <strong>{{ number(item.count) }}</strong>
              </li>
            </ul>
            <p v-else class="hire-empty">当前筛选暂无候选人。</p>
          </section>
          <section class="dashboard-panel" aria-labelledby="selection-jobs">
            <header class="panel-heading"><h3 id="selection-jobs">岗位分布</h3></header>
            <div v-if="report.selection.jobs.length" class="dashboard-table-wrap selection-table" tabindex="0" aria-label="本期岗位分布">
              <table class="dashboard-table departments-table">
                <thead><tr><th scope="col">岗位</th><th scope="col">候选人数</th></tr></thead>
                <tbody>
                  <tr v-for="item in report.selection.jobs" :key="item.id ?? 'unassigned'">
                    <th scope="row">
                      <button v-if="item.id!==null" type="button" class="text-button" @click="changeJob(item.id)">{{ item.title }}</button>
                      <span v-else>{{ item.title }}</span>
                      <small v-if="item.department">{{ item.department }}</small>
                    </th>
                    <td>{{ number(item.people) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p v-else class="hire-empty">当前筛选暂无岗位分布。</p>
          </section>
        </div>
      </section>

      <section class="selection-section dashboard-enter" style="--enter-delay:420ms"
        aria-labelledby="dashboard-profile" :aria-busy="busy">
        <header class="panel-heading selection-heading">
          <h2 id="dashboard-profile">候选人画像</h2>
          <span>{{ periodText(report.period.start,report.period.end) }} · {{ scopeLabel }} · 已覆盖 {{ number(profiles?.covered ?? 0) }} 人</span>
        </header>
        <div class="analytics-grid profile-grid">
          <section class="dashboard-panel" aria-labelledby="profile-gender">
            <header class="panel-heading"><h3 id="profile-gender">性别</h3></header>
            <template v-if="genderRows.length">
              <p class="panel-note">按本期候选人去重 · 取简历性别字段</p>
              <ul class="stage-list selection-stages">
                <li v-for="item in genderRows" :key="item.code">
                  <span>{{ item.label }}</span>
                  <div class="stage-track" aria-hidden="true"><i :style="{width:item.width,background:item.color}"></i></div>
                  <strong>{{ number(item.count) }}</strong>
                </li>
              </ul>
            </template>
            <p v-else class="hire-empty">
              暂无可统计的性别数据（{{ number(profiles?.covered ?? 0) }} 人中已解析 {{ number(profiles?.gender_available ?? 0) }} 人）。
              简历解析尚未写入性别字段，补齐后此处自动显示。
            </p>
          </section>
          <section class="dashboard-panel" aria-labelledby="profile-degree">
            <header class="panel-heading"><h3 id="profile-degree">学历</h3></header>
            <p class="panel-note">取每人最高学历</p>
            <ul v-if="degreeRows.length" class="stage-list selection-stages">
              <li v-for="item in degreeRows" :key="item.code">
                <span>{{ item.label }}</span>
                <div class="stage-track" aria-hidden="true"><i :style="{width:item.width,background:'#3f7f8f'}"></i></div>
                <strong>{{ number(item.count) }}</strong>
              </li>
            </ul>
            <p v-else class="hire-empty">当前筛选暂无学历数据。</p>
          </section>
          <section class="dashboard-panel" aria-labelledby="profile-tier">
            <header class="panel-heading"><h3 id="profile-tier">院校层级</h3></header>
            <p class="panel-note">按毕业院校标签统计 · 同人可同时符合多项</p>
            <ul v-if="tierRows.length" class="stage-list selection-stages">
              <li v-for="item in tierRows" :key="item.code">
                <span>{{ item.label }}</span>
                <div class="stage-track" aria-hidden="true"><i :style="{width:item.width,background:item.color}"></i></div>
                <strong>{{ number(item.count) }}</strong>
              </li>
            </ul>
            <p v-else class="hire-empty">当前筛选暂无院校数据。</p>
          </section>
          <section class="dashboard-panel" aria-labelledby="profile-region">
            <header class="panel-heading"><h3 id="profile-region">院校地区</h3></header>
            <p class="panel-note">按毕业院校所在省份归类</p>
            <ul v-if="regionRows.length" class="stage-list selection-stages">
              <li v-for="item in regionRows" :key="item.code">
                <span>{{ item.label }}</span>
                <div class="stage-track" aria-hidden="true"><i :style="{width:item.width,background:'#7a6f4f'}"></i></div>
                <strong>{{ number(item.count) }}</strong>
              </li>
            </ul>
            <p v-else class="hire-empty">当前筛选暂无地区数据。</p>
          </section>
        </div>
        <section v-if="topSchools.length" class="dashboard-panel profile-schools" aria-labelledby="profile-schools">
          <header class="panel-heading"><h3 id="profile-schools">院校分布 · 前 {{ topSchools.length }} 所</h3></header>
          <div class="dashboard-table-wrap" tabindex="0" aria-label="本期候选人院校分布">
            <table class="dashboard-table departments-table">
              <thead><tr><th scope="col">院校</th><th scope="col">地区</th><th scope="col">候选人数</th></tr></thead>
              <tbody>
                <tr v-for="item in topSchools" :key="item.name">
                  <th scope="row">{{ item.name }}</th>
                  <td>{{ regionLabel(item.region) }}</td>
                  <td>{{ number(item.people) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </section>

      <section class="dashboard-panel dashboard-enter" style="--enter-delay:425ms" aria-labelledby="dashboard-jobs">
        <header class="panel-heading">
          <h2 id="dashboard-jobs">岗位进度</h2>
          <span>开放岗位缺口 <strong>{{ number(report.totals.gap) }}</strong></span>
        </header>
        <div class="dashboard-table-wrap" tabindex="0" aria-label="岗位统计表">
          <table class="dashboard-table">
            <thead><tr><th scope="col">岗位</th><th scope="col">当前计划</th><th scope="col">已入职</th><th scope="col">缺口</th><th scope="col">完成率</th><th scope="col">本期申请</th><th scope="col">本期安排面试</th><th scope="col">本期录取</th></tr></thead>
            <tbody>
              <tr v-for="item in report.jobs" :key="item.id">
                <th scope="row"><RouterLink :to="{name:'job-candidates',params:{jobId:item.id}}">{{ item.title }}</RouterLink><small>{{ item.department }} · {{ jobStatusLabel(item.status) }}</small></th>
                <td>{{ number(item.planned_headcount) }}</td><td>{{ number(item.onboarded) }}</td>
                <td :class="{ 'gap-value':(item.gap ?? 0)>0 }">{{ number(item.gap) }}</td>
                <td>{{ rateText(item.planned_headcount==null ? null : Math.min(item.onboarded,item.planned_headcount),item.planned_headcount) }}</td>
                <td>{{ number(item.applications) }}</td><td>{{ number(item.interviews) }}</td><td>{{ number(item.offers) }}</td>
              </tr>
              <tr v-if="!report.jobs.length"><td colspan="8" class="hire-empty">当前范围暂无岗位</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <div class="analytics-grid secondary">
        <section class="dashboard-panel dashboard-enter" style="--enter-delay:490ms" aria-labelledby="dashboard-departments">
          <header class="panel-heading"><h2 id="dashboard-departments">部门招聘活动</h2></header>
          <div class="dashboard-table-wrap" tabindex="0" aria-label="部门统计表">
            <table class="dashboard-table departments-table">
              <thead><tr><th scope="col">部门</th><th scope="col">申请</th><th scope="col">安排面试</th><th scope="col">录取</th></tr></thead>
              <tbody>
                <tr v-for="item in report.departments" :key="item.id ?? 'unassigned'">
                  <th scope="row">
                    <button v-if="item.id!==null" type="button" class="text-button" @click="changeDepartment(item.id)">{{ item.name }}</button>
                    <span v-else>{{ item.name }}</span>
                  </th>
                  <td>{{ number(item.applications) }}</td><td>{{ number(item.interviews) }}</td><td>{{ number(item.offers) }}</td>
                </tr>
                <tr v-if="!report.departments.length"><td colspan="4" class="hire-empty">当前范围暂无部门</td></tr>
              </tbody>
            </table>
          </div>
        </section>
        <section class="dashboard-panel stage-panel dashboard-enter" style="--enter-delay:555ms" aria-labelledby="dashboard-stages">
          <header class="panel-heading"><h2 id="dashboard-stages">当前人才状态</h2><span>{{ number(totalPeople) }} 人</span></header>
          <ul class="stage-list">
            <li v-for="stage in stageRows" :key="stage.code">
              <span>{{ stage.label }}</span>
              <div class="stage-track" aria-hidden="true"><i :style="{width:shares(stage.count,maxStage),background:stage.color}"></i></div>
              <strong>{{ number(stage.count) }}</strong>
            </li>
          </ul>
        </section>
      </div>
    </template>
  </main>
</template>

<style scoped>
.recruiting-dashboard {--board-teal:#187667;--board-blue:#536fb2;--board-gold:#ac7527;--board-line:#dce7e8;--board-muted:#506471;display:grid;gap:16px;align-content:start;min-height:100%;background:#f5f8f8;padding-bottom:24px;animation:none}
.dashboard-enter {animation:dashboard-section-enter 520ms cubic-bezier(.2,.72,.25,1) var(--enter-delay,0ms) backwards}
@keyframes dashboard-section-enter {
  from {opacity:.16;transform:translateY(9px)}
  to {opacity:1;transform:translateY(0)}
}
@media (prefers-reduced-motion: reduce) {
  .dashboard-enter {animation:none}
}
.dashboard-heading {display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:14px;padding:17px 20px;border:1px solid #d8e7e5;border-left:4px solid var(--board-teal);border-radius:10px;background:linear-gradient(108deg,#eef7f3,#fff 70%)}
.dashboard-heading h1 {font-size:29px;font-weight:760;color:#18373a;line-height:1.18}
.dashboard-heading p {display:flex;align-items:center;flex-wrap:wrap;gap:7px;margin-top:6px;color:var(--board-muted);font-size:13px}
.dashboard-heading p span {width:3px;height:3px;border-radius:50%;background:#708c89}
.dashboard-actions {display:flex;gap:8px}
.dashboard-actions .el-button {margin:0}
.dashboard-filters {display:flex;align-items:center;flex-wrap:wrap;gap:9px 14px;padding:10px 12px;border:1px solid var(--board-line);border-radius:10px;background:white}
.period-switch {display:inline-flex;gap:3px;padding:3px;border-radius:7px;background:#edf4f3}
.period-switch button {min-width:42px;min-height:28px;border:0;border-radius:5px;background:transparent;color:#476065;cursor:pointer;font:inherit;font-size:13px}
.period-switch button[aria-pressed=true] {background:#fff;color:#145a51;box-shadow:0 1px 4px #2349451f;font-weight:700}
.dashboard-filters :deep(.dashboard-calendar) {width:172px}
.dashboard-select {display:flex;align-items:center;gap:7px;min-width:0;color:var(--board-muted);font-size:13px}
.dashboard-select .el-select {width:147px}
.results-grid {display:grid;grid-template-columns:minmax(255px,.8fr) minmax(0,2fr);gap:14px}
.completion-card {display:flex;flex-direction:column;justify-content:space-between;gap:16px;padding:16px 18px;border-radius:11px;background:linear-gradient(128deg,#193b42,#1b5656);color:#f3faf9}
.completion-card h2 {font-size:19px;font-weight:730}
.completion-card p {margin-top:4px;color:#d1e5e3;font-size:12px}
.completion-body {display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:16px}
.completion-dial {display:grid;place-items:center;width:108px;height:108px;border-radius:50%;background:conic-gradient(#8ee2d0 var(--completion),#fff3 0);flex:none}
.completion-dial strong {display:grid;place-items:center;width:92px;height:92px;border-radius:50%;background:#1b484b;font-size:25px;font-variant-numeric:tabular-nums}
.completion-figures {display:grid;flex:1;min-width:106px;gap:8px;margin:0}
.completion-figures>div {display:flex;align-items:baseline;justify-content:space-between;gap:5px}
.completion-figures dt {font-size:12px;color:#d4e8e6}
.completion-figures dd {margin:0;font-size:18px;font-weight:720;font-variant-numeric:tabular-nums}
.dashboard-metrics {display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.metric-card,.dashboard-panel {min-width:0;border:1px solid var(--board-line);border-radius:11px;background:#fff;box-shadow:0 2px 8px #24404308}
.metric-card {padding:12px 13px}
.metric-top {display:flex;justify-content:space-between;align-items:center;gap:5px;color:#435a65;font-size:13px;font-weight:650}
.metric-top i {display:grid;place-items:center;flex:none;width:31px;height:31px;border-radius:8px;background:#e8f4ef;color:var(--board-teal)}
.metric-top svg {width:17px;height:17px}
.metric-card[data-tone=blue] i {background:#edf0fa;color:var(--board-blue)}
.metric-card[data-tone=gold] i {background:#f9f1e4;color:#925d16}
.metric-value {margin-top:5px;color:#1c353d;font-size:clamp(23px,3vw,36px);font-weight:700;line-height:1.18;font-variant-numeric:tabular-nums}
.metric-value small {font-size:13px;color:var(--board-muted);font-weight:400}
.metric-card p {margin-top:5px;color:#526472;font-size:12px;line-height:1.45}
.analytics-grid {display:grid;grid-template-columns:minmax(0,1.67fr) minmax(235px,1fr);gap:14px;align-items:stretch}
.dashboard-panel {padding:14px 16px}
.panel-heading {display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;color:var(--board-muted);font-size:12px}
.panel-heading h2 {color:#1d373d;font-size:16px;font-weight:730}
.panel-note {margin:5px 0 12px;color:var(--board-muted);font-size:12px}
.funnel-overview {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));margin:0 0 8px;
  border:1px solid #d6e5df;border-radius:8px;background:#eff6f3}
.funnel-overview>div {min-width:0;padding:8px 10px}
.funnel-overview>div+div {border-inline-start:1px solid #d6e5df}
.funnel-overview dt {color:#4a625d;font-size:12px;font-weight:600}
.funnel-overview dd {display:grid;gap:2px;margin:0;color:#405b55;font-size:11px;line-height:1.35}
.funnel-overview strong {color:#215648;font-size:22px;font-variant-numeric:tabular-nums}
.funnel-overview strong small {font-size:12px;font-weight:550}
.funnel-empty {margin:0 0 8px;padding:8px 10px;border-radius:7px;background:#eff6f3;color:var(--board-muted);font-size:12px}
.funnel-steps {display:grid;margin:0;padding:0;list-style:none}
.funnel-steps li {display:grid;gap:5px;padding-block:6px}
.funnel-steps li+li {border-top:1px solid #e5eceb}
.funnel-stage-name {display:flex;justify-content:space-between;align-items:center;gap:4px 8px;color:#253f46;font-size:13px;font-weight:650}
.funnel-stage-name>span {display:inline-flex;align-items:center;gap:6px}
.funnel-stage-name i {display:inline-grid;place-items:center;width:21px;height:21px;border-radius:5px;
  color:var(--stage-color);background:color-mix(in srgb,var(--stage-color) 11%,white)}
.funnel-stage-name svg {width:13px;height:13px}
.funnel-stage-name strong {white-space:nowrap;font-size:15px;font-variant-numeric:tabular-nums}
.funnel-stage-name small {color:var(--board-muted);font-size:11px;font-weight:500}
.funnel-track {display:flex;justify-content:center;height:6px;border-radius:4px;background:#e9f0ef}
.funnel-track i {min-width:2px;height:100%;border-radius:inherit;background:var(--stage-color)}
.funnel-stage-rates {display:flex;flex-wrap:wrap;gap:2px 10px;color:#4b6269;font-size:11px;font-variant-numeric:tabular-nums}
.funnel-stage-rates b {color:#293f45;font-weight:650}
.analytics-grid.secondary {grid-template-columns:repeat(2,minmax(0,1fr));align-items:start}
/* 候选人画像：四类维度并排，窄屏退化为两列 */
.analytics-grid.profile-grid {grid-template-columns:repeat(4,minmax(0,1fr));align-items:start;margin-bottom:14px}
.profile-schools {width:100%}
.selection-heading {margin-bottom:9px;padding-inline:2px}
.selection-section h3 {font-size:14px;color:#214750}
.selection-stages li {grid-template-columns:106px minmax(0,1fr) minmax(26px,auto)}
.selection-stages button {color:#185e55}
.selection-stages button[aria-pressed=true] {font-weight:750}
.selection-table {max-height:240px}
.trend-key,.bucket-detail {display:flex;align-items:center;flex-wrap:wrap;gap:5px 10px}
.trend-key span {display:inline-flex;align-items:center;gap:4px;white-space:nowrap}
.trend-key i {width:8px;height:8px;border-radius:2px}
[data-tone=teal]:is(i) {background:var(--board-teal)}
[data-tone=blue]:is(i) {background:var(--board-blue)}
[data-tone=gold]:is(i) {background:var(--board-gold)}
.bucket-detail {margin-top:12px;min-height:32px;padding:6px 9px;border-radius:6px;background:#f1f6f5;color:#4a6269;font-size:12px}
.bucket-detail :is(strong,b) {color:#24434a;font-variant-numeric:tabular-nums}
.chart-frame {flex:1;display:grid;grid-template-columns:32px minmax(0,1fr);gap:5px;padding-top:13px}
.trend-panel {--trend-plot-height:clamp(190px,18vw,255px);--trend-label-height:26px;display:flex;flex-direction:column}
.chart-axis,.chart-grid {display:flex;flex-direction:column;justify-content:space-between}
.chart-axis {margin-bottom:var(--trend-label-height);color:#586f7a;text-align:right;font-size:11px}
.chart-axis span {transform:translateY(-50%);line-height:1}
.chart-content {position:relative;min-width:0}
.chart-grid {position:absolute;inset:0 0 var(--trend-label-height);pointer-events:none}
.chart-grid i {display:block;border-top:1px solid #e4ebeb}
.chart-buckets {display:grid;height:100%;min-height:calc(var(--trend-plot-height) + var(--trend-label-height))}
.chart-bucket {display:grid;grid-template-rows:minmax(0,1fr) var(--trend-label-height);min-width:0;padding:0 2px;border:0;border-radius:5px;background:transparent;color:#49616e;text-align:center;font:inherit;font-size:11px;cursor:pointer}
.chart-bucket[aria-pressed=true],.chart-bucket:hover {background:#18816d0f}
.chart-bars {display:flex;align-items:end;justify-content:center;gap:2px;padding-inline:3px}
.chart-bars i {width:min(23%,15px);border-radius:3px 3px 0 0}
.bucket-label {padding:6px 0;white-space:nowrap}
.stage-list {display:grid;gap:12px;list-style:none;padding:0;margin:15px 0 0}
.stage-list li {display:grid;grid-template-columns:62px minmax(0,1fr) minmax(26px,auto);align-items:center;gap:8px;color:#405967;font-size:13px}
.stage-list strong {text-align:end;font-variant-numeric:tabular-nums}
.stage-track {height:8px;overflow:hidden;border-radius:8px;background:#edf1f3}
.stage-track i {display:block;height:100%;border-radius:inherit}
.dashboard-table-wrap {overflow:auto;max-height:330px;margin-top:12px}
.dashboard-table {width:100%;min-width:575px;border-collapse:separate;border-spacing:0;text-align:start;font-size:13px}
.departments-table {min-width:242px}
.dashboard-table :is(th,td) {padding:9px 8px;border-bottom:1px solid #e9eeee;text-align:start}
.dashboard-table thead th {position:sticky;top:0;z-index:1;background:#f3f7f7;color:#49626c;font-size:12px;white-space:nowrap}
.dashboard-table tbody td {font-variant-numeric:tabular-nums}
.dashboard-table tbody tr:hover {background:#f8fbfa}
.dashboard-table tbody th {font-weight:650}
.dashboard-table tbody th :is(a,button) {color:#185e55}
.dashboard-table tbody th :is(a,button):hover {text-decoration:underline}
.dashboard-table small {display:block;margin-top:2px;color:var(--board-muted);font-size:11px;font-weight:400}
.text-button {padding:0;border:0;background:none;font:inherit;cursor:pointer;text-align:start}
.gap-value {color:#8f541f;font-weight:700}
@media (max-width:960px) {
  .analytics-grid,.analytics-grid.secondary,.results-grid {grid-template-columns:minmax(0,1fr)}
  .trend-panel {--trend-plot-height:148px}
}
@media (max-width:540px) {
  .recruiting-dashboard {gap:12px;overflow-wrap:anywhere}
  .dashboard-heading,.dashboard-panel {padding:12px}
  .completion-card {padding:12px}
  .dashboard-heading h1 {font-size:25px}
  .dashboard-metrics {grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
  .dashboard-select .el-select {width:105px}
  .period-switch button {min-height:36px}
  .trend-panel:has(.chart-bucket:nth-child(9)) .chart-bucket:nth-child(even) .bucket-label {visibility:hidden}
}
</style>
