<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowUpOutline, AddOutline, AlertCircleOutline, ChevronForwardOutline, CloseOutline, CopyOutline, DocumentOutline, DownloadOutline, HourglassOutline, RefreshOutline, ShareSocialOutline, SparklesOutline, StatsChartOutline, ThumbsDownOutline, ThumbsUpOutline, TimeOutline, TrashOutline, VolumeHighOutline } from '@vicons/ionicons5'
import { ElButton, ElDrawer, ElTable, ElTableColumn, ElTag } from 'element-plus'
import { jobDirectory, pollAttachment, runAnalysis, uploadAttachment, type AnalysisAttachment, type AnalysisContext, type AnalysisRequest, type JobDirectory, type Report } from '@/api/recruiting'
import { useAuthStore } from '@/stores/auth'
import { useAnalysisHistory, type AnalysisHistoryItem } from '@/stores/analysisHistory'
import ContextRing from '@/components/ContextRing.vue'
import AnalysisHistoryPanel from '@/components/AnalysisHistoryPanel.vue'

type Entry = {id:number;question:string;request:AnalysisRequest;summary:string;report?:Report;source?:'llm'|'rules';configured?:boolean;model?:string|null;error?:string;context?:AnalysisContext;historyId?:string;fromHistory?:boolean;finishedAt?:number;thinking?:string;thinkingOpen?:boolean;liked?:number;fileNames?:string[]}
const route=useRoute()
const router=useRouter()
const auth=useAuthStore()
const history=useAnalysisHistory()
const mobileHistoryOpen=ref(false)
const historyCollapsed=ref(false)
function toggleHistory() {
  if (mobileHistoryOpen.value) mobileHistoryOpen.value=false
  else historyCollapsed.value=!historyCollapsed.value
}
const directory=ref<JobDirectory>({items:[],departments:[],companies:[]})
const question=ref('')
const department=ref<number>()
const job=ref<number>()
const entries=ref<Entry[]>([])
const activeId=ref<number>()
const active=computed(()=>entries.value.find(entry=>entry.id===activeId.value))
const loading=ref(false)
const globalError=ref('')
const notice=ref('')
const copiedId=ref<number>()
const streamEnd=ref<HTMLElement>()
const drawerOpen=ref(false)
const drawerEntry=ref<Entry>()
let sessionId = crypto.randomUUID()
let noticeTimer:ReturnType<typeof setTimeout>|undefined
function resetMemory() {sessionId = crypto.randomUUID();if(active.value) active.value.context = undefined}
let sequence=0
let controller:AbortController|undefined
/* 没有岗位上下文时不提供“按岗位匹配”，否则后端必然 422。 */
const prompts=computed(()=>{
  const base=[
    {name:'本月投递与录取趋势',mode:'stats' as const},
    {name:'当前岗位招聘缺口',mode:'stats' as const},
    {name:'各部门投递对比',mode:'stats' as const},
  ]
  return job.value ? [...base,{name:'按岗位描述匹配候选人',mode:'match' as const}] : base
})
/* 从岗位详情页进入时保留岗位上下文；对话页本身不再提供选择器。 */
const selectedJob=computed(()=>directory.value.items.find(option=>option.id===job.value))
const drawerStats=computed(()=>drawerEntry.value?.report?.kind==='stats'?drawerEntry.value.report:undefined)
const drawerMatch=computed(()=>drawerEntry.value?.report?.kind==='match'?drawerEntry.value.report:undefined)
const drawerDoc=computed(()=>drawerEntry.value?.report?.kind==='doc'?drawerEntry.value.report:undefined)
const chartView=ref<'trend'|'departments'|'jobs'>('trend')
const trendScale=computed(()=>Math.max(1,...(drawerStats.value?.trend || []).map(row=>Math.max(row.applications,row.hired))))
const selectedPoint=ref<number>()
const hoverPoint=computed(()=>selectedPoint.value===undefined?undefined:drawerStats.value?.trend[selectedPoint.value])
const metrics=computed(()=>drawerStats.value ? [
  {label:'新增投递',value:drawerStats.value.metrics.applications,unit:'份'},
  {label:'首次录取',value:drawerStats.value.metrics.hired,unit:'人'},
  {label:'招聘中岗位',value:drawerStats.value.metrics.open,unit:'个'},
  {label:'计划缺口',value:drawerStats.value.metrics.gap,unit:'人'},
  ...(drawerStats.value.metrics.interviews===null ? [] : [{label:'已记录面试安排',value:drawerStats.value.metrics.interviews,unit:'份申请'}]),
] : [])
function titleFromQuestion(text:string) {
  const clean=text.trim().replace(/^(请|麻烦|帮我|请帮我|能否|可以)?(分析一下|分析|看看|查询一下|查询|告诉我)/,'')
    .replace(/[？?。！!，,]+$/,'').trim() || text.trim()
  return clean.length>24?clean.slice(0,24)+'…':clean
}
function titleFromConversation(question:string,answer:string) {
  const subject=titleFromQuestion(question)
  if(subject.length>=5) return subject
  const first=answer.split('\n').map(line=>line.replace(/^[-•]?\s*[FA]\d+：/,'').trim())
    .find(line=>line && !/^【/.test(line)) || subject
  return first.length>24?first.slice(0,24)+'…':first
}
const currentHistoryId=computed(()=>entries.value.find(entry=>entry.historyId)?.historyId || String(route.query.h || ''))
const conversationTitle=computed(()=>{
  const first=entries.value[0]
  if(!first) return ''
  return history.find(currentHistoryId.value)?.title || titleFromConversation(first.question,first.summary)
})
function answerLines(summary:string) {
  return summary.split('\n').map(text=>text.trim()).filter(Boolean).map(text=>({
    text,kind:/^【[^】]+】$/.test(text)?'heading':/^[-•]\s*/.test(text)?'bullet':'body',
  }))
}
const scopeLabel=computed(()=>{
  const name=auth.isSuperAdmin?departmentName(department.value):''
  const parts=[name,selectedJob.value?.title].filter(Boolean)
  return parts.length ? '范围：'+parts.join(' · ') : ''
})

onMounted(async()=>{
  try {
    directory.value=await jobDirectory()
    const fromRoute=directory.value.items.find(option=>String(option.id)===route.query.job)
    if(fromRoute){department.value=fromRoute.department_id;job.value=fromRoute.id}
  } catch(error){globalError.value=(error as Error).message}
})
onBeforeUnmount(()=>{
  controller?.abort()
  if(noticeTimer)clearTimeout(noticeTimer)
})

function flash(message:string) {
  notice.value=message
  if(noticeTimer)clearTimeout(noticeTimer)
  noticeTimer=setTimeout(()=>{notice.value=''},2200)
}
function departmentName(id?:number) {return directory.value.departments.find(item=>item.id===id)?.name}
function sourceLabel(entry:Entry) {
  if(entry.model) return entry.model
  if(entry.source==='llm') return '模型'
  if(entry.source==='rules') return '本地规则'
  return ''
}
function toggleThinking(entry:Entry) {
  entry.thinkingOpen=!entry.thinkingOpen
}
function thinkingLabel(entry:Entry) {
  const busy=loading.value && entry.id===activeId.value && !entry.summary && !entry.error && !entry.finishedAt
  return busy?'思考中…':'已深度思考'
}
/* 对话附件：+ 选择或拖拽上传 → worker 异步解析 → 就绪后随提问送入模型。 */
const attachments=ref<AnalysisAttachment[]>([])
const fileInput=ref<HTMLInputElement>()
const dragging=ref(false)
const MAX_ATTACHMENTS=5
function addFiles(files:FileList|File[]) {
  const room=MAX_ATTACHMENTS-attachments.value.length
  const list=Array.from(files).slice(0,Math.max(0,room))
  if(list.length<Array.from(files).length) flash('每个问题最多附带 '+MAX_ATTACHMENTS+' 个附件')
  for(const file of list) void addOne(file)
}
async function addOne(file:File) {
  function update(state:AnalysisAttachment) {
    const target=attachments.value.find(item=>item.id===state.id)
    if(!target) return
    Object.assign(target,state)
  }
  function markFailed(id:string,message:string) {
    const target=attachments.value.find(item=>item.id===id)
    if(target){target.status='failed';target.error=message}
  }
  try {
    const added=await uploadAttachment(file)
    attachments.value.push(added)
    /* worker 异步解析：1.2s 轮询，最长 ~110s */
    for(let round=0;round<90;round++) {
      await new Promise(resolve=>setTimeout(resolve,1200))
      let state:AnalysisAttachment
      try {
        state=await pollAttachment(added.id)
      } catch(error) {
        markFailed(added.id,(error as Error).message)
        return
      }
      update(state)
      if(state.status!=='pending') return
    }
    markFailed(added.id,'附件解析超时，请移除后重新上传')
  } catch(error) {
    globalError.value=(error as Error).message
  }
}
function removeAttachment(id:string) {
  attachments.value=attachments.value.filter(item=>item.id!==id)
}
function onPick(event:Event) {
  const input=event.target as HTMLInputElement
  if(input.files?.length) addFiles(input.files)
  input.value=''
}
function onDrop(event:DragEvent) {
  dragging.value=false
  if(event.dataTransfer?.files?.length) addFiles(event.dataTransfer.files)
}
function timeLabel(at?:number) {
  if(!at) return ''
  return new Date(at).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})
}
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
    globalError.value='按岗位匹配请进入岗位详情页，点「AI 分析」后再提问。'
    return
  }
  void ask()
}
function scrollToEnd() {void nextTick(()=>streamEnd.value?.scrollIntoView({block:'nearest'}))}
function jumpTo(id:number) {
  document.getElementById('msg-'+id)?.scrollIntoView({behavior:'smooth',block:'start'})
  const entry=entries.value.find(item=>item.id===id)
  if(entry) activate(entry)
}

async function ask(preset?:string, reuse=false) {
  const input=(preset ?? question.value).trim()
  if(!input || loading.value) return
  if(attachments.value.some(item=>item.status==='pending')) {
    globalError.value='附件仍在解析中，请稍候，或先移除未就绪的附件。'
    return
  }
  const readyIds=attachments.value.filter(item=>item.status==='ready').map(item=>item.id)
  /* 天数暂固定 30 天：后续由服务端从问句里解析“这一周 / 这一个月”。 */
  const request:AnalysisRequest={
    question:input,
    department_id:auth.isSuperAdmin?department.value:undefined,
    job_id:job.value,
    days:30,
    session_id:sessionId,
    ...(readyIds.length ? {attachment_ids:readyIds} : {}),
  }
  /* 记录口径快照：等待期间用户可能改了选择，历史分组要按发起时的口径归组。 */
  const scopeJobTitle=selectedJob.value?.title
  const scopeDepartmentName=auth.isSuperAdmin?departmentName(request.department_id):undefined
  const fileNames=attachments.value.filter(item=>item.status==='ready').map(item=>item.name)
  const id=++sequence
  const entry:Entry={id,question:input,request,summary:'',...(fileNames.length?{fileNames}:{})}
  entries.value.push(entry)
  if(entries.value.length>20) entries.value.shift()
  const stored=entries.value.find(item=>item.id===id)
  if(!stored) return
  activate(stored)
  if(!preset) question.value=''
  globalError.value=''
  loading.value=true
  controller=new AbortController()
  const signal=controller.signal
  scrollToEnd()
  try {
    await runAnalysis(request,event=>{
      if(event.event==='report') {
        stored.report=event.report;stored.configured=event.model_configured;stored.model=event.model
        if(activeId.value===id) activate(stored)
      } else if(event.event==='thinking'){
        /* 原生协议真流式：思考帧逐帧追加，完整内容随对话页滚动。 */
        if(stored.thinking===undefined){stored.thinking='';stored.thinkingOpen=true}
        stored.thinking+=event.text
      } else if(event.event==='delta'){
        stored.summary+=event.text
      }
      else if(event.event==='reset'){stored.summary='';stored.source=undefined}
      else if(event.event==='context') stored.context=event.context
      else if(event.event==='done') {
        stored.source=event.source
        stored.finishedAt=Date.now()
        if(stored.context) stored.context.phase='done'
        if(!reuse && stored.summary) {
          const turn={question:stored.question,summary:stored.summary,thinking:stored.thinking,
            source:event.source,at:stored.finishedAt,fileNames:stored.fileNames}
          const resumed=entries.value.find(item=>item.id!==id && item.historyId)?.historyId
          if(resumed) {
            history.appendTurn(resumed,turn)
            stored.historyId=resumed
          } else if(!stored.historyId) {
            const saved=history.add({
              question:stored.question, title:titleFromConversation(stored.question,stored.summary), summary:stored.summary, source:event.source,
              turns:[turn],
              departmentId:auth.isSuperAdmin?request.department_id:undefined,
              departmentName:scopeDepartmentName,
              jobId:request.job_id, jobTitle:scopeJobTitle,
            })
            stored.historyId=saved.id
          }
        }
      }
    },signal)
  } catch(error) {
    if(!signal.aborted) stored.error=(error as Error).message
  } finally {
    if(sequence===id){
      loading.value=false;controller=undefined
      if(stored.context) stored.context.phase='done'
      if(!stored.finishedAt) stored.finishedAt=Date.now()
    }
  }
  scrollToEnd()
}
watch(()=>active.value?.summary,()=>{void nextTick(()=>streamEnd.value?.scrollIntoView({block:'nearest'}))})

/* 主体左侧历史对话：通过 ?h=<id> 选中，仅回显问题与结论。 */
watch(()=>route.query.h,id=>{
  mobileHistoryOpen.value=false
  const key=String(id ?? '')
  if(!key) return
  const item=history.find(key)
  if(item) showHistory(item)
},{immediate:true})
function showHistory(item:AnalysisHistoryItem) {
  const turns=item.turns?.length?item.turns:[{question:item.question,summary:item.summary,source:item.source,at:item.at}]
  entries.value=turns.map(turn=>({
    id:++sequence, question:turn.question, summary:turn.summary, source:turn.source,
    configured:true, fromHistory:true, historyId:item.id, finishedAt:turn.at,
    thinking:turn.thinking, thinkingOpen:Boolean(turn.thinking), fileNames:turn.fileNames,
    request:{question:turn.question,days:30},
  }))
  activate(entries.value[entries.value.length-1])
  if(auth.isSuperAdmin && item.departmentId) department.value=item.departmentId
  else if(!auth.isSuperAdmin) department.value=undefined
  job.value=item.jobId
  globalError.value=''
  drawerOpen.value=false
  scrollToEnd()
}
function openReport(entry:Entry) {
  if(!entry.report) return
  drawerEntry.value=entry
  activate(entry)
  drawerOpen.value=true
}

async function copy(entry:Entry) {
  const text=[entry.question,entry.summary].filter(Boolean).join('\n\n')
  try {
    await navigator.clipboard.writeText(text)
    copiedId.value=entry.id
    setTimeout(()=>{if(copiedId.value===entry.id) copiedId.value=undefined},1600)
  } catch { globalError.value='浏览器拒绝了剪贴板写入，请手动选择文本复制。' }
}
function react(entry:Entry,value:number) { entry.liked=entry.liked===value?0:value }
function formatTokens(value:number) { return value>=1000 ? (value/1000).toFixed(1)+'k' : String(value) }
function speak(entry:Entry) {
  if(!entry.summary || !('speechSynthesis' in window)) return
  window.speechSynthesis.cancel()
  const utterance=new SpeechSynthesisUtterance(entry.summary)
  utterance.lang='zh-CN'
  window.speechSynthesis.speak(utterance)
}
async function shareEntry(entry:Entry) {
  const text=[entry.question,entry.summary].filter(Boolean).join('\n\n')
  if(navigator.share) {
    try { await navigator.share({title:entry.question,text}); return } catch { /* 用户取消分享后继续尝试复制 */ }
  }
  try { await navigator.clipboard.writeText(text);flash('该条回答已复制') }
  catch { globalError.value='当前环境无法分享，也无法写入剪贴板。' }
}
function regenerate(entry:Entry) {
  if(loading.value) return
  entries.value=entries.value.filter(item=>item.id!==entry.id)
  void ask(entry.question,true)
}
function resetChat() {
  if(loading.value) return
  entries.value=[]
  activeId.value=undefined
  drawerOpen.value=false
  resetMemory()
  if(route.query.h) void router.replace({name:'analysis'})
}
function chatMarkdown() {
  const lines=['# '+conversationTitle.value,'']
  if(scopeLabel.value) lines.push('范围：'+scopeLabel.value,'')
  for(const entry of entries.value) {
    lines.push('## '+entry.question,'')
    if(entry.summary) lines.push(entry.summary,'')
    const meta=[sourceLabel(entry),timeLabel(entry.finishedAt)].filter(Boolean)
    if(meta.length) lines.push('> '+meta.join(' · '),'')
  }
  return lines.join('\n')
}
async function share() {
  if(!entries.value.length) return
  const text=chatMarkdown()
  if(navigator.share) {
    try { await navigator.share({title:conversationTitle.value,text}); return }
    catch { /* 用户取消分享时继续尝试复制 */ }
  }
  try { await navigator.clipboard.writeText(text);flash('对话已复制，可直接粘贴分享') }
  catch { globalError.value='当前环境不支持系统分享，也无法写入剪贴板。' }
}
function exportChat() {
  if(!entries.value.length) return
  const url=URL.createObjectURL(new Blob([chatMarkdown()],{type:'text/markdown;charset=utf-8'}))
  const link=document.createElement('a')
  link.href=url;link.download=(conversationTitle.value.slice(0,20)||'招聘分析对话')+'.md';link.click()
  setTimeout(()=>URL.revokeObjectURL(url),1000)
}

function percent(value:number,scale:number) {return value===0?'0%':Math.max(4,value/scale*100)+'%'}
function cell(value:unknown) {return String(value).replaceAll('|','\\|').replaceAll('\n',' ')}
function download() {
  const entry=drawerEntry.value
  if(!entry?.report)return
  const report=entry.report
  const text=['# 招聘分析报告','',entry.question,'']
  if(report.kind==='stats'){
    text.push(report.start+' 至 '+report.end,'',...metrics.value.map(metric=>'- '+metric.label+' '+metric.value+' '+metric.unit),'','## 岗位数据',
      '| 岗位 | 部门 | 投递 | 录取 | 缺口 |','| --- | --- | ---: | ---: | ---: |',
      ...report.jobs.map(row=>'| '+[row.title,row.department_name,row.new_applications,row.period_hired,row.gap ?? '未设置'].map(cell).join(' | ')+' |'))
  } else if(report.kind==='match'){
    text.push('岗位 '+report.job_title,'','技能 '+report.skills.join('、'),'','| 顺序 | 申请 | 有证据 | 缺失证据 |',
      '| ---: | --- | --- | --- |',...report.matches.map((row,index)=>'| '+[index+1,row.name || '#'+row.id,row.hits.join('、'),row.missing.join('、')].map(cell).join(' | ')+' |'))
  } else {
    text.push('','',...report.attachments.map(item=>'- '+item.name+'（'+item.chars+' 字）'))
  }
  text.push('','## 总结',entry.summary)
  const url=URL.createObjectURL(new Blob([text.join('\n')],{type:'text/markdown;charset=utf-8'}))
  const link=document.createElement('a')
  link.href=url;link.download='招聘分析报告.md';link.click()
  setTimeout(()=>URL.revokeObjectURL(url),1000)
}
</script>

<template>
  <main class="hire-page analysis-page" :class="{'history-collapsed':historyCollapsed}" aria-label="AI 招聘分析">
    <AnalysisHistoryPanel :current-id="currentHistoryId" :class="{'mobile-open':mobileHistoryOpen}"
      :collapsed="historyCollapsed" :mobile-open="mobileHistoryOpen"
      @toggle="toggleHistory" @new-chat="resetChat();mobileHistoryOpen=false"/>
    <button v-if="mobileHistoryOpen" type="button" class="history-scrim" aria-label="关闭历史对话" @click="mobileHistoryOpen=false"></button>
    <section class="analysis-conversation" aria-label="当前对话">
    <button v-if="!entries.length" type="button" class="mobile-history-toggle empty-toggle" @click="mobileHistoryOpen=!mobileHistoryOpen">历史对话</button>
    <header v-if="entries.length" class="analysis-topbar">
      <button type="button" class="mobile-history-toggle" @click="mobileHistoryOpen=!mobileHistoryOpen">历史对话</button>
      <div class="topbar-title">
        <h1>{{ conversationTitle }}</h1>
        <small>{{ entries.length }} 轮对话{{ scopeLabel ? ' · ' + scopeLabel : '' }}</small>
      </div>
      <div class="topbar-actions">
        <button type="button" title="分享对话" aria-label="分享对话" :disabled="!entries.length" @click="share"><ShareSocialOutline aria-hidden="true"/></button>
        <button type="button" title="导出对话" aria-label="导出对话" :disabled="!entries.length" @click="exportChat"><DownloadOutline aria-hidden="true"/></button>
        <button type="button" title="清空对话" aria-label="清空对话" :disabled="!entries.length" @click="resetChat"><TrashOutline aria-hidden="true"/></button>
      </div>
    </header>

    <div class="analysis-body" :class="{'no-rail':!entries.length}">
      <div class="analysis-stream" :class="{empty:!entries.length}">
        <div v-if="!entries.length" class="chat-hero">
          <span class="hero-icon" aria-hidden="true"><SparklesOutline/></span>
          <h2>问招聘数据，要证据不要套话</h2>
          <p>例如“这一周研发部投递了多少份”“这一个月哪些岗位没收到简历”“找出会 Go 的后端”。</p>
          <div class="hero-prompts" aria-label="快捷问题">
            <button v-for="prompt in prompts" :key="prompt.name" type="button" :disabled="loading" @click="shortcut(prompt)">
              <ArrowUpOutline aria-hidden="true"/>{{ prompt.name }}
            </button>
          </div>
        </div>
        <template v-else>
          <div v-for="entry in entries" :id="'msg-'+entry.id" :key="entry.id" class="chat-msg">
            <div class="chat-bubble user">
              {{ entry.question }}
              <span v-if="entry.fileNames?.length" class="user-files">
                <span v-for="name in entry.fileNames" :key="name" class="user-file"><DocumentOutline aria-hidden="true"/>{{ name }}</span>
              </span>
            </div>
            <div class="chat-bubble ai" :class="{active:entry.id===activeId}">
              <div v-if="entry.thinking" class="think-block">
                <button type="button" class="think-head" :aria-expanded="entry.thinkingOpen?'true':'false'"
                  :aria-label="entry.thinkingOpen?'收起思考过程':'展开思考过程'" @click="toggleThinking(entry)">
                  <ChevronForwardOutline aria-hidden="true"/><span>{{ thinkingLabel(entry) }}</span>
                </button>
                <pre v-show="entry.thinkingOpen" :id="'think-'+entry.id" class="think-body" tabindex="-1">{{ entry.thinking }}</pre>
              </div>
              <p v-if="entry.error" class="hire-error">{{ entry.error }}</p>
              <div v-else-if="entry.summary" class="chat-summary">
                <p v-for="(line,index) in answerLines(entry.summary)" :key="index" :class="'answer-'+line.kind">{{ line.text }}</p>
              </div>
              <p v-else class="chat-status" role="status"><TimeOutline aria-hidden="true"/>
                {{ !entry.report?(entry.request.attachment_ids?.length?'正在解析并读取附件…':'正在查询招聘数据…'):(entry.report.kind==='doc'?'附件已读取，正在生成回答…':'证据已加载，正在生成总结…') }}</p>
              <div v-if="entry.report || entry.fromHistory || entry.summary" class="chat-actions">
                <button v-if="entry.report" type="button" class="chat-report-link" @click="openReport(entry)">
                  <StatsChartOutline aria-hidden="true"/>查看数据明细
                </button>
                <span v-if="entry.fromHistory" class="chat-flag">历史记录</span>
                <div v-if="entry.summary" class="msg-toolbar" role="group" aria-label="回答操作">
                  <button type="button" class="tool" :title="copiedId===entry.id?'已复制':'复制'" :aria-label="copiedId===entry.id?'已复制':'复制'" @click="copy(entry)">
                    <CheckmarkOutline v-if="copiedId===entry.id" aria-hidden="true"/><CopyOutline v-else aria-hidden="true"/>
                  </button>
                  <button type="button" class="tool" :class="{on:entry.liked===1}" title="有帮助" aria-label="有帮助" @click="react(entry,1)">
                    <ThumbsUpOutline aria-hidden="true"/>
                  </button>
                  <button type="button" class="tool" :class="{on:entry.liked===-1}" title="没帮助" aria-label="没帮助" @click="react(entry,-1)">
                    <ThumbsDownOutline aria-hidden="true"/>
                  </button>
                  <button type="button" class="tool" title="朗读回答" aria-label="朗读回答" @click="speak(entry)">
                    <VolumeHighOutline aria-hidden="true"/>
                  </button>
                  <button type="button" class="tool" title="重新生成" aria-label="重新生成" :disabled="loading" @click="regenerate(entry)">
                    <RefreshOutline aria-hidden="true"/>
                  </button>
                  <button type="button" class="tool" title="分享此回答" aria-label="分享此回答" @click="shareEntry(entry)">
                    <ShareSocialOutline aria-hidden="true"/>
                  </button>
                </div>
                <span class="msg-meta">
                  <i v-if="entry.context?.tokens_used">上下文 {{ formatTokens(entry.context.tokens_used) }} tokens</i>
                  <i v-if="sourceLabel(entry)">{{ sourceLabel(entry) }}</i>
                  <time v-if="entry.finishedAt">{{ timeLabel(entry.finishedAt) }}</time>
                </span>
              </div>
            </div>
          </div>
        </template>
        <span ref="streamEnd" aria-hidden="true"></span>
      </div>

      <nav v-if="entries.length" class="chat-rail" aria-label="对话索引">
        <button v-for="entry in entries" :key="'rail-'+entry.id" type="button"
          :aria-current="entry.id===activeId?'true':undefined" :aria-label="entry.question" :title="entry.question"
          @click="jumpTo(entry.id)"><span>{{ titleFromQuestion(entry.question) }}</span></button>
      </nav>
    </div>

    <div class="analysis-composer">
      <p v-if="globalError" class="hire-error" role="alert">{{ globalError }}</p>
      <p v-else-if="notice" class="composer-notice" role="status">{{ notice }}</p>
      <div v-if="attachments.length" class="attach-row" aria-label="待发送附件">
        <span v-for="item in attachments" :key="item.id" class="attach-chip" :class="item.status">
          <HourglassOutline v-if="item.status==='pending'" aria-hidden="true"/>
          <AlertCircleOutline v-else-if="item.status==='failed'" aria-hidden="true"/>
          <DocumentOutline v-else aria-hidden="true"/>
          <span class="attach-name" :title="item.status==='failed'?item.error:item.name">{{ item.name }}</span>
          <small v-if="item.status==='ready'">{{ item.chars }} 字</small>
          <small v-else-if="item.status==='pending'">解析中…</small>
          <small v-else>解析失败</small>
          <button type="button" class="attach-remove" :aria-label="'移除附件 '+item.name" @click="removeAttachment(item.id)">
            <CloseOutline aria-hidden="true"/>
          </button>
        </span>
      </div>
      <div class="composer-box" :class="{dragging}"
        @dragover.prevent="dragging=true" @dragleave.prevent="dragging=false"
        @drop.prevent="onDrop">
        <textarea v-model="question" rows="1" maxlength="600" aria-label="输入招聘问题"
          :placeholder="attachments.length ? '就这些附件提问，例如“总结这两份文件的重点”' : selectedJob ? '追问这份岗位的候选人情况…' : '问投递趋势、岗位缺口，或直接描述要找的简历'"
          @keydown.ctrl.enter.prevent="ask()" @keydown.meta.enter.prevent="ask()"></textarea>
        <div class="composer-foot">
          <div class="composer-scope">
            <button type="button" class="attach-add" title="添加附件（PDF / Word / 图片，可直接拖入输入框）"
              aria-label="添加附件" @click="fileInput?.click()">
              <AddOutline aria-hidden="true"/>
            </button>
            <span v-if="scopeLabel" class="composer-scope-hint">{{ scopeLabel }}</span>
          </div>
          <div class="composer-tail">
            <ContextRing :context="active?.context" @clear-scope="resetMemory"/>
            <button type="button" class="send" :disabled="!question.trim() || loading" aria-label="发送" @click="ask()">
              <ArrowUpOutline aria-hidden="true"/>
            </button>
          </div>
        </div>
        <input ref="fileInput" type="file" hidden multiple
          accept=".pdf,.docx,.doc,.txt,.md,.png,.jpg,.jpeg,.webp,.bmp,.tiff" @change="onPick">
      </div>
      <p class="composer-hint">Ctrl + Enter 发送 · 支持拖拽 PDF / Word / 图片到输入框提问</p>
    </div>
    </section>

    <ElDrawer v-model="drawerOpen" size="min(560px, 92vw)" direction="rtl" :with-header="false">
      <div v-if="drawerEntry" class="drawer-body">
        <header class="drawer-head">
          <div><h2>{{ drawerMatch?'人岗匹配明细':drawerDoc?'附件问答明细':drawerStats?.focus==='总览'?'招聘概览':'招聘'+drawerStats?.focus }}</h2>
            <p>{{ drawerStats ? drawerStats.start+' 至 '+drawerStats.end : drawerMatch?.job_title ?? '基于对话附件的回答' }}</p></div>
          <div class="drawer-actions">
            <ElTag v-if="drawerEntry.source" :type="drawerEntry.source==='llm'?'success':'info'" effect="plain">
              {{ drawerEntry.source==='llm'?'模型解读':'规则总结' }}
            </ElTag>
            <ElButton :icon="DownloadOutline" @click="download">下载</ElButton>
          </div>
        </header>
        <div class="drawer-scroll">
          <p class="drawer-question">{{ drawerEntry.question }}</p>
          <template v-if="drawerStats">
            <div class="result-metrics">
              <article v-for="metric in metrics" :key="metric.label">
                <span>{{ metric.label }}</span><strong>{{ metric.value.toLocaleString('zh-CN') }} <small>{{ metric.unit }}</small></strong>
              </article>
            </div>
            <section class="report-card">
              <div class="report-head"><h3>数据明细</h3><div class="report-tabs" aria-label="分析维度">
                <button type="button" :aria-pressed="chartView==='trend'" @click="chartView='trend'">时间趋势</button>
                <button type="button" :aria-pressed="chartView==='departments'" @click="chartView='departments'">部门分布</button>
                <button type="button" :aria-pressed="chartView==='jobs'" @click="chartView='jobs'">岗位缺口</button>
              </div></div>
              <div v-if="chartView==='trend'" class="chart" aria-label="投递与录取趋势">
                <div class="chart-legend"><span><i></i>新增投递</span><span><i></i>首次录取</span>
                  <span v-if="hoverPoint" class="chart-tooltip">{{ hoverPoint.start_date }} · 投递 {{ hoverPoint.applications }} · 录取 {{ hoverPoint.hired }}</span></div>
                <div class="chart-columns" :style="{'--bar-count':Math.max(1,drawerStats.trend.length)}">
                  <button v-for="(row,index) in drawerStats.trend" :key="row.bucket" type="button"
                    :aria-label="row.start_date+' 投递 '+row.applications+'，录取 '+row.hired"
                    @mouseenter="selectedPoint=index" @focus="selectedPoint=index" @click="selectedPoint=index"
                    @mouseleave="selectedPoint=undefined" @blur="selectedPoint=undefined">
                    <span :style="{height:percent(row.applications,trendScale)}"></span>
                    <span :style="{height:percent(row.hired,trendScale)}"></span>
                  </button>
                </div>
                <div class="chart-dates"><span>{{ drawerStats.trend[0]?.start_date }}</span><span>{{ drawerStats.trend.at(-1)?.end_date }}</span></div>
                <p v-if="!drawerStats.trend.some(row=>row.applications || row.hired)" class="hire-muted">该时段暂无投递或录取记录。</p>
              </div>
              <ElTable v-else-if="chartView==='departments'" :data="drawerStats.departments" empty-text="暂无部门数据">
                <ElTableColumn prop="name" label="部门" min-width="110"/><ElTableColumn prop="new_applications" label="投递" min-width="75"/>
                <ElTableColumn prop="period_hired" label="首次录取" min-width="89"/><ElTableColumn prop="open_jobs" label="招聘中岗位" min-width="95"/>
              </ElTable>
              <ElTable v-else :data="drawerStats.jobs" empty-text="暂无岗位数据">
                <ElTableColumn label="岗位" min-width="145"><template #default="{row}">
                  <RouterLink class="hire-inline-button" :to="{name:'jobs',query:{job:row.id}}">{{ row.title }}</RouterLink>
                </template></ElTableColumn>
                <ElTableColumn prop="department_name" label="部门" min-width="100"/>
                <ElTableColumn label="计划缺口" min-width="95"><template #default="{row}">{{ row.gap ?? '未设置' }}</template></ElTableColumn>
                <ElTableColumn prop="new_applications" label="投递" min-width="75"/>
              </ElTable>
            </section>
          </template>
          <section v-else-if="drawerMatch" class="report-card">
            <div class="report-head"><h3>按岗位证据排序</h3><span class="hire-muted">已核对 {{ drawerMatch.reviewed }} 份，最多展示 40 份</span></div>
            <div class="report-skills"><span>岗位要求</span>
              <ElTag v-for="skill in drawerMatch.skills" :key="skill" effect="plain">{{ skill }}</ElTag>
              <span class="hire-muted">{{ drawerMatch.source==='llm'?'模型解析岗位要求':'词典解析岗位要求' }}</span>
            </div>
            <ElTable :data="drawerMatch.matches" empty-text="此岗位暂无已发布简历">
              <ElTableColumn label="顺序" type="index" width="64"/>
              <ElTableColumn label="候选人" min-width="105"><template #default="{row}">
                <RouterLink :to="{name:'applications',query:{job:drawerMatch.job_id,candidate:row.id}}" class="hire-inline-button">{{ row.name || '申请 #'+row.id }}</RouterLink>
              </template></ElTableColumn>
              <ElTableColumn label="有证据" min-width="120"><template #default="{row}">{{ row.hits.join('、') || '—' }}</template></ElTableColumn>
              <ElTableColumn label="缺失证据" min-width="120"><template #default="{row}">{{ row.missing.join('、') || '—' }}</template></ElTableColumn>
            </ElTable>
          </section>
          <section v-else-if="drawerDoc" class="report-card">
            <div class="report-head"><h3>本次附件</h3><span class="hire-muted">解析文本随提问送入模型，共 {{ drawerDoc.attachments.length }} 个</span></div>
            <div class="doc-list">
              <span v-for="item in drawerDoc.attachments" :key="item.name" class="doc-item">
                <DocumentOutline aria-hidden="true"/>{{ item.name }}<small>{{ item.chars }} 字</small>
              </span>
            </div>
          </section>
          <section class="report-card">
            <div class="report-head"><h3><SparklesOutline/> 分析结论</h3></div>
            <p v-if="drawerEntry.summary" class="result-summary">{{ drawerEntry.summary }}</p>
            <p v-else class="hire-muted">该记录没有保存结论文本。</p>
          </section>
        </div>
      </div>
    </ElDrawer>
  </main>
</template>

<style scoped>
.analysis-page{padding:0;height:100%;min-height:0;display:grid;grid-template-columns:252px minmax(0,1fr);grid-template-rows:minmax(0,1fr);background:#fff}
@media(min-width:761px){.analysis-page.history-collapsed{grid-template-columns:52px minmax(0,1fr)}}
.analysis-conversation{position:relative;min-width:0;min-height:0;display:grid;grid-template-rows:auto minmax(0,1fr) auto}
.mobile-history-toggle{display:none}
.history-scrim{display:none}
.analysis-topbar{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:13px 24px;border-bottom:1px solid #eef1f2;background:#fff}
.analysis-topbar{grid-row:1}
.topbar-title{min-width:0}
.topbar-title h1{overflow:hidden;color:#1b3a41;font-size:15px;font-weight:600;text-overflow:ellipsis;white-space:nowrap}
.topbar-title small{display:block;margin-top:2px;color:var(--hire-muted);font-size:11.5px}
.topbar-actions{display:flex;align-items:center;gap:4px;flex:none}
.topbar-actions button{display:grid;place-items:center;width:30px;height:30px;border:1px solid transparent;border-radius:8px;background:transparent;color:#55707a;cursor:pointer;transition:background-color var(--rs-motion-fast),border-color var(--rs-motion-fast),color var(--rs-motion-fast)}
.topbar-actions button svg{width:16px;height:16px}
.topbar-actions button:hover{background:var(--hire-soft);border-color:#cfe4e0;color:var(--hire-accent-dark)}
.topbar-actions button:disabled{opacity:.4;cursor:not-allowed}
.analysis-body{min-height:0;display:grid;grid-template-columns:minmax(0,1fr) 145px}
.analysis-body{grid-row:2}
.analysis-body.no-rail{grid-template-columns:minmax(0,1fr)}
.analysis-stream{overflow:auto;padding:26px max(20px,calc((100% - 880px) / 2)) 12px;display:grid;align-content:start;gap:22px;scrollbar-width:thin}
.analysis-stream.empty{place-content:center;justify-items:center;text-align:center}
.chat-hero{display:grid;justify-items:center;gap:11px;max-width:620px;padding:20px 8px}
.hero-icon{width:52px;height:52px;border-radius:15px;display:grid;place-items:center;background:var(--hire-soft);color:var(--hire-accent-dark)}
.hero-icon svg{width:25px;height:25px}
.chat-hero h2{font-size:21px;color:var(--hire-ink);font-weight:560}
.chat-hero p{color:var(--hire-muted);font-size:13px;line-height:1.7}
.hero-prompts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;width:100%;margin-top:10px}
.hero-prompts button{display:flex;align-items:center;gap:8px;text-align:left;background:#fff;border:1px solid var(--hire-line);border-radius:10px;padding:11px 13px;cursor:pointer;color:#33505a;font-size:13px;transition:border-color var(--rs-motion-fast),background-color var(--rs-motion-fast),transform var(--rs-motion-fast) var(--rs-ease)}
.hero-prompts button svg{width:14px;height:14px;color:var(--hire-accent);flex:none;transform:rotate(45deg)}
.hero-prompts button:hover{border-color:#9fcfc7;background:var(--hire-soft);transform:translateY(-2px)}
.chat-msg{display:grid;gap:9px;scroll-margin-top:12px}
.chat-bubble{max-width:min(880px,94%);border-radius:14px;padding:11px 15px;font-size:13.5px;line-height:1.76;overflow-wrap:anywhere}
.chat-bubble.user{justify-self:end;background:#e8f5f2;color:#26494f;border-bottom-right-radius:4px}
.user-files{display:flex;gap:5px;flex-wrap:wrap;margin-top:6px}
.user-file{display:inline-flex;align-items:center;gap:4px;max-width:220px;padding:2px 7px;border-radius:6px;background:rgba(255,255,255,.7);font-size:11px;color:#3d6360;overflow:hidden}
.user-file svg{width:11px;height:11px;flex:none}
.user-file{white-space:nowrap;text-overflow:ellipsis}
.chat-bubble.ai{justify-self:stretch;max-width:none;background:transparent;color:#2f4d4c;padding:2px 2px 0;border-radius:0}
.chat-summary{display:grid;gap:9px;font-size:14px;line-height:1.82;white-space:pre-wrap}
.chat-summary .answer-heading{margin-top:8px;font-weight:700;color:#214b51;font-size:14px}
.chat-summary .answer-bullet{padding-left:13px;border-left:2px solid #d5e9e3;color:#3a555a}
.chat-status{display:flex;align-items:center;gap:6px;color:var(--hire-muted);font-size:12.5px}
.chat-status svg{width:14px;height:14px}
.think-block{margin:14px 0 18px}
.think-head{display:inline-flex;align-items:center;gap:7px;padding:4px 0;border:0;background:transparent;color:#547178;font:inherit;font-size:12.5px;cursor:pointer;transition:color var(--rs-motion-fast)}
.think-head svg{width:12px;height:12px;flex:none;transition:transform var(--rs-motion-fast)}
.think-head[aria-expanded=true] svg{transform:rotate(90deg)}
.think-head:hover{color:var(--hire-accent-dark)}
.think-body{margin:8px 0 0 5px;max-height:none;overflow:visible;white-space:pre-wrap;color:#768c91;font-family:inherit;font-size:12.5px;line-height:1.8;background:transparent;border-inline-start:1px solid #d9e6e6;border-radius:0;padding:4px 0 6px 16px}
.chat-actions{display:flex;align-items:center;gap:10px;margin-top:8px;flex-wrap:wrap}
.chat-report-link{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border:1px solid #cfe5e1;border-radius:7px;background:#f4faf9;color:var(--hire-accent-dark);font:inherit;font-size:12px;cursor:pointer;transition:border-color var(--rs-motion-fast),background-color var(--rs-motion-fast)}
.chat-report-link svg{width:13px;height:13px}
.chat-report-link:hover{border-color:var(--hire-accent);background:#e4f4ef}
.chat-flag{padding:2px 7px;border:1px solid var(--hire-line);border-radius:5px;background:#fafcfc;font-size:11.5px;color:var(--hire-muted)}
.msg-meta{display:inline-flex;align-items:center;gap:2px;margin-inline-start:auto;color:#8a9ba0;font-size:11.5px}
.msg-meta i{font-style:normal;padding:0 6px;color:#7d8f95}
.msg-meta time{font-variant-numeric:tabular-nums;padding-inline-start:2px}
.msg-toolbar{display:inline-flex;align-items:center;gap:1px}
.tool{display:grid;place-items:center;width:24px;height:24px;padding:0;border:0;border-radius:6px;background:transparent;color:#7f959b;cursor:pointer;transition:background-color var(--rs-motion-fast),color var(--rs-motion-fast)}
.tool svg{width:14px;height:14px}
.tool:hover{background:#f0f7f5;color:var(--hire-accent-dark)}
.tool.on{background:#e2f3ee;color:var(--hire-accent-dark)}
.tool:disabled{opacity:.4;cursor:not-allowed}
/* 右侧对话定位线与问题摘要。 */
.chat-rail{position:relative;display:flex;flex-direction:column;align-items:stretch;justify-content:center;gap:3px;padding:18px 10px 18px 12px;overflow:auto;scrollbar-width:none}
.chat-rail::before{content:'';position:absolute;left:7px;top:20px;bottom:20px;border-left:1px dashed #d2dddf}
.chat-rail button{position:relative;min-width:0;min-height:28px;padding:5px 4px 5px 9px;border:0;background:transparent;color:#91a0a4;text-align:right;font:inherit;font-size:11px;cursor:pointer}
.chat-rail button::before{content:'';position:absolute;left:-5px;top:50%;width:8px;border-top:2px solid #c8d7d7}
.chat-rail button[aria-current=true]{color:var(--hire-accent-dark);font-weight:650}
.chat-rail button[aria-current=true]::before{border-color:var(--hire-accent);width:12px}
.chat-rail button:hover{color:#315f60}
.chat-rail button span{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.analysis-composer{padding:10px max(20px,calc((100% - 880px) / 2)) 18px}
.analysis-composer{grid-row:3}
.composer-notice{margin:0 0 6px;color:var(--hire-accent-dark);font-size:12px;text-align:center}
.attach-row{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-bottom:7px}
.attach-chip{display:inline-flex;align-items:center;gap:5px;max-width:250px;padding:4px 7px;border:1px solid var(--hire-line);border-radius:8px;background:#fff;font-size:11.5px;color:#33505a}
.attach-chip svg{width:13px;height:13px;flex:none;color:var(--hire-accent)}
.attach-chip.pending{border-style:dashed;color:var(--hire-muted)}
.attach-chip.pending svg{color:var(--hire-warning,#BA7517)}
.attach-chip.failed{border-color:#ecc8c8;background:#fdf6f6;color:#a32d2d}
.attach-chip.failed svg{color:#c84654}
.attach-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.attach-chip small{flex:none;color:var(--hire-muted);font-size:10.5px}
.attach-remove{display:grid;place-items:center;width:16px;height:16px;border:0;border-radius:4px;background:transparent;color:#8a9ba0;cursor:pointer;flex:none}
.attach-remove svg{width:11px;height:11px}
.attach-remove:hover{background:#f0f7f5;color:var(--hire-accent-dark)}
.attach-add{display:grid;place-items:center;width:28px;height:28px;flex:none;border:1px solid transparent;border-radius:8px;background:transparent;color:#55707a;cursor:pointer;transition:background-color var(--rs-motion-fast),border-color var(--rs-motion-fast),color var(--rs-motion-fast)}
.attach-add svg{width:16px;height:16px}
.attach-add:hover{background:var(--hire-soft);border-color:#cfe4e0;color:var(--hire-accent-dark)}
.composer-box.dragging{border-color:var(--hire-accent);border-style:dashed;background:var(--hire-primary-softer,#f3fbf9);box-shadow:0 10px 28px rgba(13,137,126,.12)}
.composer-box.dragging textarea::placeholder{color:var(--hire-accent-dark)}
.composer-box{border:1px solid #d9e6e4;border-radius:14px;background:#fff;box-shadow:0 8px 24px rgba(18,50,61,.07);padding:10px 12px 8px;transition:border-color var(--rs-motion-fast),box-shadow var(--rs-motion-fast),background-color var(--rs-motion-fast)}
.composer-box:focus-within{border-color:#9ecfc8;box-shadow:0 10px 28px rgba(18,50,61,.1)}
.composer-box textarea{display:block;width:100%;min-height:44px;max-height:190px;padding:4px 2px;border:0;outline:none;background:transparent;color:#28464d;font:inherit;font-size:14px;line-height:1.6;resize:none;field-sizing:content}
.composer-box textarea::placeholder{color:#9aa9ad}
@supports not (field-sizing: content){.composer-box textarea{min-height:72px}}
.composer-foot{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:4px;flex-wrap:wrap}
.composer-scope-hint{color:var(--hire-muted);font-size:11.5px;line-height:1.4}
.composer-tail{display:flex;align-items:center;gap:9px;margin-inline-start:auto}
.send{display:grid;place-items:center;width:32px;height:32px;flex:none;border:0;border-radius:50%;background:var(--hire-accent);color:#fff;cursor:pointer;transition:opacity var(--rs-motion-fast),transform var(--rs-motion-fast)}
.send svg{width:16px;height:16px}
.send:disabled{opacity:.35;cursor:not-allowed}
.send:not(:disabled):hover{transform:translateY(-1px)}
.composer-hint{margin:7px 2px 0;color:var(--hire-muted);font-size:11px;text-align:center}
.drawer-body{display:grid;grid-template-rows:auto minmax(0,1fr);height:100%}
.drawer-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:18px 20px 14px;border-bottom:1px solid var(--hire-line)}
.drawer-head h2{font-size:17px;color:var(--hire-ink)}
.drawer-head p{margin-top:4px;color:var(--hire-muted);font-size:12px}
.drawer-actions{display:flex;align-items:center;gap:8px}
.drawer-scroll{overflow:auto;padding:16px 20px 24px;display:grid;gap:12px;align-content:start}
.drawer-question{margin:0;padding:9px 12px;border-radius:9px;background:#f5faf9;color:#3b5b5e;font-size:12.5px;line-height:1.6}
.result-metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(103px,1fr));gap:8px}
.result-metrics article{padding:11px 13px;display:grid;gap:5px;border:1px solid var(--hire-line);border-radius:10px;background:#fff}
.result-metrics span,.result-metrics small{font-size:12px;color:var(--hire-muted);font-weight:400}
.result-metrics strong{font-size:23px;font-variant-numeric:tabular-nums;color:var(--hire-ink)}
.report-card{padding:14px;border:1px solid var(--hire-line);border-radius:12px;background:#fff}
.report-head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:11px;flex-wrap:wrap}
.report-head h3{display:flex;align-items:center;gap:6px;font-size:14px}
.report-head svg{width:17px;height:17px;color:var(--hire-accent)}
.report-tabs{display:flex;gap:4px;flex-wrap:wrap}
.report-tabs button{border:0;border-radius:5px;padding:5px 7px;background:transparent;color:var(--hire-muted);cursor:pointer;font-size:12px}
.report-tabs button[aria-pressed=true]{background:var(--hire-soft);color:var(--hire-accent-dark);font-weight:650}
.report-skills{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-bottom:12px;font-size:12px}
.chart-legend,.chart-dates{display:flex;align-items:center;justify-content:space-between;gap:8px}
.chart-legend{justify-content:flex-start;flex-wrap:wrap;color:var(--hire-muted);font-size:12px;margin-bottom:8px;min-height:20px}
.chart-legend span{display:flex;gap:5px;align-items:center}.chart-legend i{width:8px;height:8px;background:#209386;border-radius:2px}
.chart-legend span:nth-child(2) i{background:#718cd7}.chart-tooltip{margin-inline-start:auto}
.chart-columns{height:155px;display:grid;grid-template-columns:repeat(var(--bar-count),minmax(0,1fr));gap:1px;border-bottom:1px solid var(--hire-line);background:repeating-linear-gradient(to top,#e9eef3 0 1px,transparent 1px 38px)}
.chart-columns button{display:flex;align-items:flex-end;justify-content:center;border:0;background:transparent;cursor:pointer;gap:1px;padding-inline:1px}
.chart-columns button:hover,.chart-columns button:focus-visible{background:#e7f2f0}
.chart-columns button span{width:38%;background:#209386;border-radius:3px 3px 0 0;max-width:17px}
.chart-columns button span:nth-child(2){background:#718cd7}
.chart-dates{padding-top:6px;margin-bottom:5px;font-size:11px;color:var(--hire-muted)}
.result-summary{white-space:pre-wrap;color:#334c5d;font-size:13.5px;line-height:1.8}
.doc-list{display:flex;gap:7px;flex-wrap:wrap}
.doc-item{display:inline-flex;align-items:center;gap:5px;padding:5px 9px;border:1px solid var(--hire-line);border-radius:8px;background:#f8fbfa;font-size:12px;color:#33505a}
.doc-item svg{width:13px;height:13px;color:var(--hire-accent)}
.doc-item small{color:var(--hire-muted);font-size:11px}
@media(max-width:760px){
  .analysis-page{grid-template-columns:minmax(0,1fr)}
  .analysis-history{display:none;position:absolute;z-index:12;inset:0 auto 0 0;width:min(280px,calc(100% - 28px));box-shadow:12px 0 30px rgba(20,52,60,.14)}
  .analysis-history.mobile-open{display:grid}
  .history-scrim{display:block;position:absolute;z-index:11;inset:0;border:0;background:rgba(19,49,53,.22)}
  .mobile-history-toggle{display:inline-flex;align-items:center;flex:none;padding:6px 9px;border:1px solid #d5e8e4;border-radius:7px;background:#fff;color:var(--hire-accent-dark);font:inherit;font-size:11px;cursor:pointer}
  .mobile-history-toggle.empty-toggle{position:absolute;z-index:3;top:12px;left:12px}
  .analysis-topbar{padding:9px 14px}
  .analysis-stream{padding:18px 14px 8px}
  .analysis-composer{padding:8px 14px 14px}
  .hero-prompts{grid-template-columns:1fr}
  .analysis-body{grid-template-columns:minmax(0,1fr)}
  .chat-rail{display:none}
}
</style>
