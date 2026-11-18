<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { AnalyticsOutline, ArrowUpOutline, DownloadOutline, SparklesOutline, StatsChartOutline, TimeOutline } from '@vicons/ionicons5'
import { ElButton, ElInput, ElSelect, ElOption, ElTable, ElTableColumn, ElTag } from 'element-plus'
import { jobDirectory, runAnalysis, type AnalysisRequest, type JobDirectory, type Report } from '@/api/recruiting'

type Entry = {id:number;question:string;request:AnalysisRequest;summary:string;report?:Report;source?:'llm'|'rules';configured?:boolean;model?:string|null;error?:string}
const route=useRoute()
const directory=ref<JobDirectory>({items:[],departments:[],companies:[]})
const question=ref('')
const department=ref<number>()
const job=ref<number>()
const days=ref<7|30|90>(30)
const entries=ref<Entry[]>([])
const activeId=ref<number>()
const active=computed(()=>entries.value.find(entry=>entry.id===activeId.value))
const loading=ref(false)
const globalError=ref('')
const chatEnd=ref<HTMLElement>()
let sequence=0
let controller:AbortController|undefined
const prompts=computed(()=>[
  {name:'本月投递与录取趋势',mode:'stats'},
  {name:'当前岗位招聘缺口',mode:'stats'},
  {name:'近 30 天各部门投递对比',mode:'stats'},
  {name:'按岗位描述匹配候选人',mode:'match'},
])
const jobs=computed(()=>directory.value.items.filter(option=>!department.value || option.department_id===department.value))
const selectedJob=computed(()=>directory.value.items.find(option=>option.id===job.value))
const chartView=ref<'trend'|'departments'|'jobs'>('trend')
const stats=computed(()=>active.value?.report?.kind==='stats'?active.value.report:undefined)
const match=computed(()=>active.value?.report?.kind==='match'?active.value.report:undefined)
const trendScale=computed(()=>Math.max(1,...(stats.value?.trend || []).map(row=>Math.max(row.applications,row.hired))))
const selectedPoint=ref<number>()
const hoverPoint=computed(()=>selectedPoint.value===undefined?undefined:stats.value?.trend[selectedPoint.value])
const metrics=computed(()=>stats.value ? [
  {label:'新增投递',value:stats.value.metrics.applications,unit:'份'},
  {label:'首次录取',value:stats.value.metrics.hired,unit:'人'},
  {label:'招聘中岗位',value:stats.value.metrics.open,unit:'个'},
  {label:'计划缺口',value:stats.value.metrics.gap,unit:'人'},
  ...(stats.value.metrics.interviews===null ? [] : [{label:'已记录面试安排',value:stats.value.metrics.interviews,unit:'份申请'}]),
] : [])

onMounted(async()=>{
  try {
    directory.value=await jobDirectory()
    const selected=directory.value.items.find(option=>String(option.id)===route.query.job)
    if(selected){department.value=selected.department_id;job.value=selected.id}
  } catch(error){globalError.value=(error as Error).message}
})
onBeforeUnmount(()=>controller?.abort())

function activate(entry:Entry) {
  activeId.value=entry.id
  const report=entry.report
  chartView.value=report?.kind==='stats' && report.focus==='缺口' ? 'jobs'
    : report?.kind==='stats' && report.focus==='部门' ? 'departments' : 'trend'
  selectedPoint.value=undefined
}
function shortcut(prompt:{name:string;mode:string}) {
  question.value=prompt.name
  if(prompt.mode==='match' && !job.value) {
    globalError.value='请先选择需要匹配的岗位。'
    return
  }
  void ask()
}
async function ask() {
  const input=question.value.trim()
  if(!input || loading.value) return
  const request:AnalysisRequest={
    question:input,department_id:department.value,job_id:job.value,days:days.value,
  }
  const id=++sequence
  const entry:Entry={id,question:input,request,summary:''}
  entries.value.push(entry)
  if(entries.value.length>20) entries.value.shift()
  const stored=entries.value.find(item=>item.id===id)!
  activate(stored)
  question.value=''
  globalError.value=''
  loading.value=true
  controller=new AbortController()
  const signal=controller.signal
  await nextTick()
  chatEnd.value?.scrollIntoView({block:'nearest'})
  try {
    await runAnalysis(request,event=>{
      if(event.event==='report') {
        stored.report=event.report;stored.configured=event.model_configured;stored.model=event.model
        if(activeId.value===id) activate(stored)
      } else if(event.event==='delta') stored.summary+=event.text
      else if(event.event==='reset') stored.summary=''
      else if(event.event==='done') stored.source=event.source
    },signal)
  } catch(error) {
    if(!signal.aborted) stored.error=(error as Error).message
  } finally {
    if(sequence===id){loading.value=false;controller=undefined}
  }
}
function percent(value:number,scale:number) {return value===0?'0%':Math.max(4,value/scale*100)+'%'}
function download() {
  const entry=active.value
  if(!entry?.report)return
  const report=entry.report
  const text=['# 招聘分析报告','',entry.question,'']
  if(report.kind==='stats'){
    text.push(report.start+' 至 '+report.end,'',...metrics.value.map(metric=>'- '+metric.label+' '+metric.value+' '+metric.unit),'','## 岗位数据',
      '| 岗位 | 部门 | 投递 | 录取 | 缺口 |','| --- | --- | ---: | ---: | ---: |',
      ...report.jobs.map(row=>'| '+[row.title,row.department_name,row.new_applications,row.period_hired,row.gap ?? '未设置'].map(cell).join(' | ')+' |'))
  } else {
    text.push('岗位 '+report.job_title,'','技能 '+report.skills.join('、'),'','| 顺序 | 申请 | 有证据 | 待核实 |',
      '| ---: | --- | --- | --- |',...report.matches.map((row,index)=>'| '+[index+1,row.name || '#'+row.id,row.hits.join('、'),row.missing.join('、')].map(cell).join(' | ')+' |'))
  }
  text.push('','## 总结',entry.summary)
  const url=URL.createObjectURL(new Blob([text.join('\n')],{type:'text/markdown;charset=utf-8'}))
  const link=document.createElement('a')
  link.href=url;link.download='招聘分析报告.md';link.click()
  setTimeout(()=>URL.revokeObjectURL(url),1000)
}
function cell(value:unknown) {return String(value).replaceAll('|','\\|').replaceAll('\n',' ')}
</script>

<template>
  <main class="hire-page analysis-page">
    <header class="hire-heading">
      <div><h1>AI 分析</h1><p>提问、核对数据并生成招聘报告</p></div>
      <ElButton :icon="DownloadOutline" :disabled="!active?.report || (loading && !active.source)" @click="download">下载报告</ElButton>
    </header>
    <div class="analysis-layout">
      <section class="hire-surface analysis-chat" aria-label="招聘查询对话">
        <header class="analysis-chat-head"><span class="analysis-icon"><SparklesOutline/></span>
          <div><h2>招聘分析助手</h2><p>基于当前账号的数据权限查询</p></div></header>
        <div class="analysis-scope">
          <ElSelect v-model="department" clearable placeholder="全部授权部门" aria-label="查询部门" @change="job=undefined">
            <ElOption v-for="item in directory.departments" :key="item.id" :label="item.name" :value="item.id"/>
          </ElSelect>
          <ElSelect v-model="job" clearable placeholder="选择岗位" aria-label="查询岗位">
            <ElOption v-for="item in jobs" :key="item.id" :label="item.title" :value="item.id"/>
          </ElSelect>
          <ElSelect v-model="days" aria-label="查询时段">
            <ElOption label="近 7 天" :value="7"/><ElOption label="近 30 天" :value="30"/><ElOption label="近 90 天" :value="90"/>
          </ElSelect>
        </div>
        <div class="analysis-chat-scroll">
          <div class="analysis-suggestions"><h3>快捷问题</h3>
            <button v-for="prompt in prompts" :key="prompt.name" type="button" :disabled="loading" @click="shortcut(prompt)">
              <ArrowUpOutline/>{{ prompt.name }}
            </button>
          </div>
          <div v-if="entries.length" class="chat-history" aria-label="当前会话查询历史">
            <article v-for="entry in entries" :key="entry.id">
              <button type="button" class="chat-question" :aria-pressed="entry.id===activeId" @click="activate(entry)">{{ entry.question }}</button>
              <p v-if="entry.error" class="hire-error">{{ entry.error }}</p>
              <p v-else class="chat-tool" role="status"><TimeOutline/>
                {{ !entry.report?'正在查询招聘数据…':!entry.source?'证据已加载，正在生成总结…':'已生成报告，点击查看' }}</p>
            </article>
          </div>
          <span ref="chatEnd" aria-hidden="true"></span>
        </div>
        <form class="analysis-composer" @submit.prevent="ask">
          <p v-if="globalError" class="hire-error" role="alert">{{ globalError }}</p>
          <ElInput v-model="question" type="textarea" :rows="3" maxlength="600" show-word-limit
            :placeholder="selectedJob ? '例如：查找匹配 Python 和 SQL 的候选人' : '例如：统计本月各部门投递与录取情况'"
            aria-label="输入招聘问题" @keydown.ctrl.enter.prevent="ask" @keydown.meta.enter.prevent="ask"/>
          <div><small>Ctrl + Enter 发送</small>
            <ElButton type="primary" :icon="ArrowUpOutline" native-type="submit" :loading="loading" :disabled="!question.trim()">查询</ElButton></div>
        </form>
      </section>

      <section class="analysis-result" aria-label="招聘分析结果" :aria-busy="loading">
        <div v-if="!active?.report" class="hire-surface analysis-empty">
          <span class="analysis-icon"><AnalyticsOutline/></span><h2>{{ loading?'正在查询数据':'招聘分析报告' }}</h2>
          <p>{{ active?.error || (loading?'数据就绪后将在这里展示指标和明细。':'从左侧选择快捷问题，或输入你希望核对的招聘问题。') }}</p>
        </div>
        <template v-else>
          <header class="hire-surface result-heading">
            <div><h2>{{ match?'人岗匹配报告':stats?.focus==='总览'?'招聘概览':'招聘'+stats?.focus }}</h2>
              <p>{{ stats ? stats.start+' 至 '+stats.end : match?.job_title }}</p></div>
            <ElTag v-if="active.source" :type="active.source==='llm'?'success':'info'" effect="plain">
              {{ active.source==='llm'?(active.model==='glm-5.3'?'百炼 · GLM-5.3':'模型解读'):active.configured?'模型暂不可用 · 规则总结':'本地规则总结' }}
            </ElTag>
          </header>
          <template v-if="stats">
            <div class="result-metrics">
              <article v-for="metric in metrics" :key="metric.label" class="hire-surface">
                <span>{{ metric.label }}</span><strong>{{ metric.value.toLocaleString('zh-CN') }} <small>{{ metric.unit }}</small></strong>
              </article>
            </div>
            <section class="hire-surface report-card">
              <div class="report-head"><h3>数据明细</h3><div class="report-tabs" aria-label="分析维度">
                <button type="button" :aria-pressed="chartView==='trend'" @click="chartView='trend'">时间趋势</button>
                <button type="button" :aria-pressed="chartView==='departments'" @click="chartView='departments'">部门分布</button>
                <button type="button" :aria-pressed="chartView==='jobs'" @click="chartView='jobs'">岗位缺口</button>
              </div></div>
              <div v-if="chartView==='trend'" class="chart" aria-label="投递与录取趋势">
                <div class="chart-legend"><span><i></i>新增投递</span><span><i></i>首次录取</span>
                  <span v-if="hoverPoint" class="chart-tooltip">{{ hoverPoint.start_date }} · 投递 {{ hoverPoint.applications }} · 录取 {{ hoverPoint.hired }}</span></div>
                <div class="chart-columns" :style="{'--bar-count':Math.max(1,stats.trend.length)}">
                  <button v-for="(row,index) in stats.trend" :key="row.bucket" type="button"
                    :aria-label="row.start_date+' 投递 '+row.applications+'，录取 '+row.hired"
                    @mouseenter="selectedPoint=index" @focus="selectedPoint=index" @click="selectedPoint=index"
                    @mouseleave="selectedPoint=undefined" @blur="selectedPoint=undefined">
                    <span :style="{height:percent(row.applications,trendScale)}"></span>
                    <span :style="{height:percent(row.hired,trendScale)}"></span>
                  </button>
                </div>
                <div class="chart-dates"><span>{{ stats.trend[0]?.start_date }}</span><span>{{ stats.trend.at(-1)?.end_date }}</span></div>
                <p v-if="!stats.trend.some(row=>row.applications || row.hired)" class="hire-muted">该时段暂无投递或录取记录。</p>
              </div>
              <ElTable v-else-if="chartView==='departments'" :data="stats.departments" empty-text="暂无部门数据">
                <ElTableColumn prop="name" label="部门" min-width="110"/><ElTableColumn prop="new_applications" label="投递" min-width="75"/>
                <ElTableColumn prop="period_hired" label="首次录取" min-width="89"/><ElTableColumn prop="open_jobs" label="招聘中岗位" min-width="95"/>
              </ElTable>
              <ElTable v-else :data="stats.jobs" empty-text="暂无岗位数据">
                <ElTableColumn label="岗位" min-width="145"><template #default="{row}">
                  <RouterLink class="hire-inline-button" :to="{name:'jobs',query:{job:row.id}}">{{ row.title }}</RouterLink>
                </template></ElTableColumn>
                <ElTableColumn prop="department_name" label="部门" min-width="100"/>
                <ElTableColumn label="计划缺口" min-width="95"><template #default="{row}">{{ row.gap ?? '未设置' }}</template></ElTableColumn>
                <ElTableColumn prop="new_applications" label="投递" min-width="75"/>
              </ElTable>
            </section>
          </template>
          <section v-else-if="match" class="hire-surface report-card">
            <div class="report-head"><h3>按岗位证据排序</h3><span class="hire-muted">已核对 {{ match.reviewed }} 份，最多展示 40 份</span></div>
            <div class="report-skills"><span>岗位要求</span>
              <ElTag v-for="skill in match.skills" :key="skill" effect="plain">{{ skill }}</ElTag>
              <span class="hire-muted">{{ match.source==='llm'?'模型解析岗位要求':'词典解析岗位要求' }}</span>
            </div>
            <ElTable :data="match.matches" empty-text="此岗位暂无已发布简历">
              <ElTableColumn label="顺序" type="index" width="64"/>
              <ElTableColumn label="候选人" min-width="105"><template #default="{row}">
                <RouterLink :to="{name:'applications',query:{job:match.job_id,candidate:row.id}}" class="hire-inline-button">{{ row.name || '申请 #'+row.id }}</RouterLink>
              </template></ElTableColumn>
              <ElTableColumn label="有证据" min-width="120"><template #default="{row}">{{ row.hits.join('、') || '—' }}</template></ElTableColumn>
              <ElTableColumn label="待核实" min-width="120"><template #default="{row}">{{ row.missing.join('、') || '—' }}</template></ElTableColumn>
            </ElTable>
          </section>
          <section class="hire-surface report-card report-conclusion">
            <div class="report-head"><h3><SparklesOutline/> 分析结论</h3><span v-if="!active.source" class="hire-muted">正在生成…</span></div>
            <p v-if="active.summary" class="result-summary">{{ active.summary }}</p>
            <p v-else class="hire-muted">分析将根据上方数据生成。</p>
          </section>
        </template>
      </section>
    </div>
  </main>
</template>

<style scoped>
.analysis-page{height:100%;display:grid;grid-template-rows:auto minmax(0,1fr);min-height:560px}
.analysis-layout{display:grid;grid-template-columns:minmax(300px,396px) minmax(0,1fr);gap:14px;min-height:0}
.analysis-chat{display:grid;grid-template-rows:auto auto minmax(0,1fr) auto;overflow:hidden;min-height:0}
.analysis-chat-head{display:flex;gap:11px;align-items:center;border-bottom:1px solid var(--hire-line);padding:15px}
.analysis-icon{width:37px;height:37px;border-radius:9px;background:var(--hire-soft);display:grid;place-items:center;color:var(--hire-accent-dark);flex:none}
.analysis-icon svg{width:19px;height:19px}
.analysis-chat-head h2,.result-heading h2{font-size:17px;line-height:1.25}.analysis-chat-head p,.result-heading p{color:var(--hire-muted);font-size:12px;margin-top:4px}
.analysis-scope{display:grid;grid-template-columns:1fr 1fr 97px;gap:6px;padding:11px 12px;border-bottom:1px solid var(--hire-line);background:#fafcfd}
.analysis-scope>*{min-width:0}
.analysis-chat-scroll{overflow:auto;padding:13px}
.analysis-suggestions,.chat-history{display:grid;gap:7px}.analysis-suggestions h3{font-size:13px;color:#455a6c;margin-bottom:2px}
.analysis-suggestions button{display:flex;align-items:center;gap:6px;text-align:left;background:#f4f7f9;border:0;border-radius:7px;padding:8px 10px;cursor:pointer;color:#385166;font-size:13px}
.analysis-suggestions button:hover{background:var(--hire-soft)}
.analysis-suggestions button svg,.chat-tool svg{width:14px;height:14px;flex:none}
.chat-history{margin-top:17px}.chat-history article{display:grid;gap:6px}
.chat-question{justify-self:end;max-width:92%;border:1px solid transparent;background:#eef4f4;color:#2c4b52;border-radius:9px;padding:9px 11px;cursor:pointer;font-size:13px;overflow-wrap:anywhere}
.chat-question[aria-pressed=true]{border-color:#8dbbb5;background:var(--hire-soft)}
.chat-tool{display:flex;align-items:center;gap:5px;color:var(--hire-muted);font-size:12px}
.analysis-composer{padding:12px;border-top:1px solid var(--hire-line)}
.analysis-composer>div:last-child{display:flex;justify-content:space-between;align-items:center;margin-top:8px}
.analysis-composer small{color:var(--hire-muted)}
.analysis-result{display:grid;gap:12px;align-content:start;overflow:auto;min-width:0}
.analysis-empty{min-height:420px;display:grid;gap:9px;place-content:center;justify-items:center;text-align:center;color:var(--hire-muted);padding:20px}
.analysis-empty h2{color:var(--hire-ink);font-size:19px}.analysis-empty p{font-size:13px}.analysis-empty .analysis-icon{height:50px;width:50px}
.result-heading,.report-head,.chart-legend,.chart-dates{display:flex;align-items:center;justify-content:space-between;gap:8px}
.result-heading,.report-card{padding:14px}
.result-metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(103px,1fr));gap:8px}
.result-metrics article{padding:10px 12px;display:grid;gap:5px}
.result-metrics span,.result-metrics small{font-size:12px;color:var(--hire-muted);font-weight:400}
.result-metrics strong{font-size:23px;font-variant-numeric:tabular-nums;color:var(--hire-ink)}
.report-head{margin-bottom:11px;flex-wrap:wrap}.report-head h3{display:flex;align-items:center;gap:6px;font-size:14px}.report-head svg{width:17px;height:17px;color:var(--hire-accent)}
.report-tabs{display:flex;gap:4px;flex-wrap:wrap}
.report-tabs button{border:0;border-radius:5px;padding:5px 7px;background:transparent;color:var(--hire-muted);cursor:pointer;font-size:12px}
.report-tabs button[aria-pressed=true]{background:var(--hire-soft);color:var(--hire-accent-dark);font-weight:650}
.chart-legend{justify-content:flex-start;flex-wrap:wrap;color:var(--hire-muted);font-size:12px;margin-bottom:8px;min-height:20px}
.chart-legend span{display:flex;gap:5px;align-items:center}.chart-legend i{width:8px;height:8px;background:#209386;border-radius:2px}
.chart-legend span:nth-child(2) i{background:#718cd7}.chart-tooltip{margin-inline-start:auto}
.chart-columns{height:155px;display:grid;grid-template-columns:repeat(var(--bar-count),minmax(0,1fr));gap:1px;border-bottom:1px solid var(--hire-line);
  background:repeating-linear-gradient(to top,#e9eef3 0 1px,transparent 1px 38px)}
.chart-columns button{display:flex;align-items:flex-end;justify-content:center;border:0;background:transparent;cursor:pointer;gap:1px;padding-inline:1px}
.chart-columns button:hover,.chart-columns button:focus-visible{background:#e7f2f0}
.chart-columns button span{width:38%;background:#209386;border-radius:3px 3px 0 0;max-width:17px}
.chart-columns button span:nth-child(2){background:#718cd7}
.chart-dates{padding-top:6px;margin-bottom:5px;font-size:11px;color:var(--hire-muted)}
.report-skills{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-bottom:12px;font-size:12px}
.result-summary{white-space:pre-wrap;color:#334c5d;font-size:14px;line-height:1.8}
@media(max-width:960px){.analysis-layout{grid-template-columns:minmax(250px,325px) minmax(0,1fr)}.analysis-scope{grid-template-columns:1fr 1fr}.analysis-scope>:last-child{grid-column:1/-1}}
@media(max-width:760px){.analysis-page{height:auto}.analysis-layout{grid-template-columns:minmax(0,1fr)}.analysis-chat{min-height:370px;max-height:570px}.analysis-empty{min-height:230px}.analysis-result{overflow:visible}}
</style>
