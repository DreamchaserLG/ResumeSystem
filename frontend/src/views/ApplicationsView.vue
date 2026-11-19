<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { FunnelOutline, CloudUploadOutline, ChevronBackOutline, DocumentTextOutline, AttachOutline, CalendarOutline, SparklesOutline, EllipsisHorizontal } from '@vicons/ionicons5'
import { ElButton, ElDialog, ElDropdown, ElDropdownMenu, ElDropdownItem, ElInput, ElOption, ElPagination, ElSelect, ElTag, ElMessage } from 'element-plus'
import { api } from '@/api/client'
import { readResumeView, saveResumeView } from '@/api/session'
import { dateLabel, label, locationText, schoolTagLabels, type Detail, type Job, type ResumeCard, type ResumeCardPage, type TransferJob } from '@/api/hr'
import { followupPath, jobDirectory, type Followup, type MatchReport, type NotePage } from '@/api/recruiting'
import ResumeBook from '@/components/ResumeBook.vue'
import ResumeFilters, { type FilterSelection } from '@/components/ResumeFilters.vue'
import ResumeBatchImport from '@/components/ResumeBatchImport.vue'

const route=useRoute(), router=useRouter()
const positive=(value:unknown)=>{
  if(typeof value!=='string' || !/^\d+$/.test(value)) return undefined
  const number=Number(value)
  return Number.isSafeInteger(number) && number>0 ? number : undefined
}
const incomingJob=computed(()=>positive(route.query.job))
const incomingCandidate=computed(()=>positive(route.query.candidate))
const incomingDepartment=computed(()=>positive(route.query.department))
type ResumeTab='overview'|'resume'|'fields'|'notes'
type SavedView={path:string; page:number; sort:'recent'|'match'; filters:FilterSelection;
  tab:ResumeTab; candidate?:number; mobileDetails?:boolean; listWidth?:number; detailWidth?:number; widthTuned?:boolean; viewportWidth?:number}
let restored:SavedView|undefined
try {
  const saved=JSON.parse(readResumeView() || 'null') as SavedView|null
  if(saved?.path===route.fullPath) restored=saved
} catch { /* Invalid browser state falls back to the route defaults. */ }
const departmentName=computed(()=>directoryDepartments.value.find(item=>item.id===incomingDepartment.value)?.name)
const directoryDepartments=ref<{id:number;name:string}[]>([])
const jobs=ref<Job[]>([])
const list=ref<ResumeCard[]>([])
const current=ref<ResumeCard>()
const details=ref<Detail>()
const notes=ref<Followup[]>([])
const nextNote=ref<number|null>(null)
const report=ref<MatchReport>()
const page=ref(restored?.page || 1)
const size=ref(15)
const total=ref(0)
const sort=ref<'recent'|'match'>(restored?.sort || 'recent')
const available=ref<string[]>([])
const followupOptions=ref<string[]>([])
const filters=ref<FilterSelection>(restored?.filters || (incomingJob.value?{job:[String(incomingJob.value)]}:{}))
const filterOpen=ref(false)
const filterButton=ref<InstanceType<typeof ElButton>>()
const singleImportOpen=ref(false), batchImportOpen=ref(false)
const singleUploading=ref(false), batchUploading=ref(false)
onBeforeRouteLeave(()=>{
  if(!singleUploading.value && !batchUploading.value) return true
  ElMessage.warning('简历正在上传，提交后即可切换页面。')
  return false
})
const listLoading=ref(false), detailLoading=ref(false), publishing=ref(false), matching=ref(false)
const error=ref(''), detailError=ref('')
const tab=ref<ResumeTab>(restored?.tab || 'overview')
const rankable=computed(()=>filters.value.job?.length===1)
const noteBody=ref('')
const noteAttachment=ref<File>()
const noteField=ref<HTMLTextAreaElement>()
const noteFile=ref<HTMLInputElement>()
const mentionsOpen=ref(false)
const collaborators=ref<{id:number;display_name:string}[]>([])
const evidenceGroups=computed(()=>{
  const result = new Map<string,Detail['evidence']>()
  for(const entry of details.value?.evidence || []) {
    const key=entry.field_key.split('.')[0]
    const group=result.get(key) || []
    if(group.length<18) group.push(entry)
    result.set(key,group)
  }
  return [...result.entries()]
})
const groupNames:Record<string,string>={profile:'基本信息',work:'工作经历',education:'教育背景',project:'项目经历',skill:'技能',contact:'联系方式'}
const candidateOverview=computed(()=>{
  const row=current.value
  if(!row) return
  const evidence=details.value?.evidence ?? []
  const valueFor=(key:string,record?:number)=>evidence.find(item=>item.field_key===key && (record==null || item.record_no===record))?.text
  const highest=row.tags.find(item=>item.type==='degree')?.value
  const schools=details.value?.education ?? []
  const education=schools.find(item=>item.degree===highest) ?? schools[0]
  const skills=[...new Set(evidence.filter(item=>item.field_key==='skill.name')
    .map(item=>item.text.trim()).filter(Boolean))]
  const companies=[...new Set(evidence.filter(item=>item.field_key==='work.company')
    .map(item=>item.text.trim()).filter(Boolean))]
  const projects=[...new Set(evidence.filter(item=>item.field_key==='project.project_name')
    .map(item=>item.text.trim()).filter(Boolean))]
  return {
    education,degree:highest?label(highest):'',major:education&&valueFor('education.major',education.record_no),
    role:row.target_role, summary:valueFor('profile.summary'),
    companies,projects,
    tags:row.tags.filter(item=>['language','capability','domain'].includes(item.type)),skills:skills.slice(0,12),
  }
})
const interviewOpen=ref(false)
const interviewTime=ref('')
const interviewText=ref('')
const transferOpen=ref(false)
const transferId=ref<number>()
const transferReason=ref('')
const destinations=ref<TransferJob[]>([])
const bounded=(value:number|undefined,min:number,max:number,fallback:number)=>Math.min(max,Math.max(min,value ?? fallback))
/* 打开工作台时按可用空间计算最合适的栏宽；用户拖动过的宽度只在同一屏幕宽度下沿用。 */
const viewportWidth=()=>window.innerWidth-300
/* 换了屏幕（窗口宽度相差超过 160px）就重新自适应，避免沿用别处拖出来的窄栏。 */
const sameViewport=!restored?.viewportWidth || Math.abs(restored.viewportWidth-window.innerWidth)<=160
const widthTuned=ref(Boolean(restored?.widthTuned) && sameViewport)
function fitWidth(container:number,share:number,min:number,max:number) {return Math.round(Math.min(max,Math.max(min,container*share)))}
const fitListWidth=()=>fitWidth(viewportWidth(),.26,260,520)
const fitDetailWidth=()=>fitWidth(viewportWidth(),.36,340,780)
const listWidth=ref(widthTuned.value?bounded(restored?.listWidth,260,520,fitListWidth()):fitListWidth())
const detailWidth=ref(widthTuned.value?bounded(restored?.detailWidth,340,780,fitDetailWidth()):fitDetailWidth())
const mobileDetails=ref(Boolean(restored?.mobileDetails))
const candidateWorkspace=ref<HTMLElement>()
const candidateDocument=ref<HTMLElement>()
const candidateScroll=ref<HTMLElement>()
const avatar=ref('')
let listVersion=0
let detailVersion=0
let refreshTimer:ReturnType<typeof setTimeout>|undefined
let dragEnd:(()=>void)|undefined
const filterCount=computed(()=>{
  const {salaryRange,experienceRange,...facets}=filters.value
  return Object.values(facets).reduce((count,values)=>count+(values?.length ?? 0),0)
    + Number(Boolean(salaryRange)) + Number(Boolean(experienceRange))
})
const displayName=(row:ResumeCard)=>row.name || '申请 #'+row.id
const degreeOf=(row:ResumeCard)=>{const tag=row.tags.find(item=>item.type==='degree');return tag?label(tag.value):''}
const latestMatch=computed(()=>report.value?.matches[0])
const currentPageCount=computed(()=>list.value.length)
function resetDraft() { noteBody.value='';noteAttachment.value=undefined;mentionsOpen.value=false;detailError.value='' }

async function pick(row:ResumeCard, bringForward=true) {
  if(bringForward) mobileDetails.value=true
  if(current.value?.id===row.id && current.value.version_id===row.version_id
    && details.value?.workflow_version===row.workflow_version && details.value.parse_status===row.parse_status) {
    current.value=row
    return
  }
  const sequence=++detailVersion
  const id=row.id
  current.value=row
  if(avatar.value) URL.revokeObjectURL(avatar.value)
  avatar.value=''
  details.value=undefined; notes.value=[]; nextNote.value=null; report.value=undefined
  resetDraft();detailLoading.value=true;matching.value=false
  const requests = [
    api.get<Detail>('/hr/applications/'+id),
    api.get<NotePage>(followupPath(id)),
    api.get<MatchReport|null>('/workspace/applications/'+id+'/match'),
    row.has_avatar && row.version_id ? api.get<Blob>('/hr/applications/'+id+'/avatar?version='+row.version_id) : Promise.resolve(undefined),
  ] as const
  const [detail, history, match, photo]=await Promise.allSettled(requests)
  if(sequence!==detailVersion) return
  if(detail.status==='fulfilled') details.value=detail.value
  else detailError.value=(detail.reason as Error).message
  if(history.status==='fulfilled'){notes.value=history.value.items;nextNote.value=history.value.next}
  else if(!detailError.value) detailError.value=(history.reason as Error).message
  if(match.status==='fulfilled') report.value=match.value || undefined
  if(photo.status==='fulfilled' && photo.value) avatar.value=URL.createObjectURL(photo.value)
  detailLoading.value=false
}

function resetCurrent() {
  current.value=undefined
  details.value=undefined
  notes.value=[]
  report.value=undefined
  detailVersion++
  if(avatar.value) URL.revokeObjectURL(avatar.value)
  avatar.value=''
}
async function load(preferred?:number, bringForward?:boolean) {
  const sequence=++listVersion
  if(refreshTimer) clearTimeout(refreshTimer)
  listLoading.value=true;error.value=''
  const {job,followup,salaryRange,experienceRange,...facets}=filters.value
  try {
    const records=await api.post<ResumeCardPage>('/hr/resume-cards/search',{
      page:page.value,size:size.value,sort:rankable.value?sort.value:'recent',facets,
      job_ids:job?.map(Number) || [],department_id:incomingDepartment.value,
      salary_min:salaryRange?.[0],salary_max:salaryRange?.[1],
      experience_min_months:experienceRange?.[0],experience_max_months:experienceRange?.[1],
      followup:followup?.find(value=>value==='recorded' || value==='unrecorded') || null,
      followup_values:followup?.filter(value=>value.startsWith('note:')).map(value=>value.slice(5)) || [],
    })
    if(sequence!==listVersion) return
    list.value=records.items
    total.value=records.total
    available.value=records.available_facets || []
    const maxPage=Math.max(1,Math.ceil(records.total/size.value))
    if(page.value>maxPage){page.value=maxPage;return}
    const explicit=incomingCandidate.value
    const requested=preferred ?? explicit
    let row=records.items.find(row=>row.id===(requested ?? current.value?.id))
    if(!row && requested && explicit===requested) {
      const direct=await api.post<ResumeCardPage>('/hr/resume-cards/search',{application_id:requested,size:1})
      if(sequence!==listVersion) return
      row=direct.items[0]
      if(!row) error.value='该候选申请不存在，或超出当前可查看范围。'
    }
    if(row || records.items[0]) void pick(row || records.items[0],bringForward ?? Boolean(requested))
    else resetCurrent()
    if(records.items.some(row=>['parsing','parsed'].includes(row.parse_status))) {
      refreshTimer=setTimeout(()=>void load(),5000)
    }
  } catch(cause) {if(sequence===listVersion)error.value=(cause as Error).message}
  finally {if(sequence===listVersion)listLoading.value=false}
}
async function applyFilters(values:FilterSelection) {
  filters.value=values
  if(!rankable.value) sort.value='recent'
  if(route.query.job || route.query.candidate) {
    // Preserve new filter selections when removing the shortcut in the URL.
    const query={...route.query}
    delete query.job
    delete query.candidate
    ignoredRoute=true
    try {await router.replace({name:'applications',query})}
    finally {ignoredRoute=false}
  }
  candidateScroll.value?.scrollTo({top:0})
  if(page.value!==1) page.value=1
  else void load()
}
let ignoredRoute=false
watch([incomingJob,incomingCandidate,incomingDepartment],([job,candidate],previous)=>{
  if(ignoredRoute) return
  if(job) filters.value={...filters.value,job:[String(job)]}
  else if(previous[0] || previous[2]!==incomingDepartment.value) filters.value={...filters.value,job:undefined}
  if(!rankable.value) sort.value='recent'
  if(page.value!==1) page.value=1
  else void load(candidate)
})
watch([page,size,sort],()=>void load())
watch([page,sort,filters,tab,()=>current.value?.id,mobileDetails,listWidth,detailWidth],()=>{
  if(route.name!=='applications') return
  const {followup,...selection}=filters.value
  // Save filters and references only; never save the resume or any free-text follow-up.
  const flags=followup?.filter(value=>value==='recorded'||value==='unrecorded')
  saveResumeView(JSON.stringify({path:route.fullPath,page:page.value,sort:sort.value,
    filters:{...selection,followup:flags?.length?flags:undefined},
    tab:tab.value,candidate:current.value?.id,mobileDetails:mobileDetails.value,
    listWidth:listWidth.value,detailWidth:detailWidth.value,widthTuned:widthTuned.value,
    viewportWidth:window.innerWidth} satisfies SavedView))
},{deep:true,flush:'post'})
onMounted(()=>{
  void load(restored?.candidate,restored?.mobileDetails)
  jobDirectory().then(result=>{jobs.value=result.items;directoryDepartments.value=result.departments}).catch(cause=>{error.value=(cause as Error).message})
  if(!widthTuned.value) void nextTick(()=>{
    const workspace=candidateWorkspace.value?.clientWidth
    if(workspace) listWidth.value=fitWidth(workspace,.26,260,Math.min(520,Math.max(260,workspace-720)))
    const doc=candidateDocument.value?.clientWidth
    if(doc) detailWidth.value=fitWidth(doc,.36,340,Math.min(780,Math.max(340,doc-340)))
  })
})
onBeforeUnmount(()=>{
  detailVersion++;listVersion++
  if(refreshTimer)clearTimeout(refreshTimer)
  if(avatar.value)URL.revokeObjectURL(avatar.value)
  dragEnd?.()
})

function closeFilters() {
  filterOpen.value=false
  void nextTick(()=>filterButton.value?.$el.focus())
}
function confirmFilters(values:FilterSelection) {
  closeFilters()
  void applyFilters(values)
}
async function viewImport(job:number,id:number) {
  filters.value={job:[String(job)]}
  page.value=1
  await router.replace({name:'applications',query:{job:String(job),candidate:String(id)}})
  await load(id)
}
function changeSort() {
  if(!rankable.value){
    ElMessage.info('请在筛选中选择一个岗位，再按岗位相关性排序。')
    void openFilters()
    return
  }
  page.value=1
  sort.value='match'
}
async function openFilters() {
  if(filterOpen.value) {closeFilters();return}
  filterOpen.value=true
  try {
    const result=await api.get<{items:string[]}>('/workspace/followup-options')
    const selected=(filters.value.followup || []).filter(value=>value.startsWith('note:')).map(value=>value.slice(5))
    followupOptions.value=[...new Set([...selected,...result.items])]
  } catch { followupOptions.value=[] }
}
function horizontalResize(event:PointerEvent,current:number,min:number,max:number,update:(value:number)=>void) {
  if(event.button!==0) return
  dragEnd?.()
  const start=event.clientX
  const element=event.currentTarget as HTMLElement
  element.setPointerCapture(event.pointerId)
  document.body.classList.add('candidate-column-resizing')
  const move=(point:PointerEvent)=>update(Math.min(max,Math.max(min,current+point.clientX-start)))
  const end=()=>{
    element.removeEventListener('pointermove',move)
    element.removeEventListener('pointerup',end)
    element.removeEventListener('pointercancel',end)
    document.body.classList.remove('candidate-column-resizing')
    dragEnd=undefined
  }
  element.addEventListener('pointermove',move)
  element.addEventListener('pointerup',end)
  element.addEventListener('pointercancel',end)
  dragEnd=end
}
function resizeList(event:PointerEvent) {
  const available=candidateWorkspace.value?.clientWidth ?? 1200
  horizontalResize(event,listWidth.value,260,Math.max(260,Math.min(520,available-720)),value=>{widthTuned.value=true;listWidth.value=value})
}
function resizeDetail(event:PointerEvent) {
  const available=candidateDocument.value?.clientWidth ?? 980
  horizontalResize(event,detailWidth.value,340,Math.max(340,Math.min(780,available-340)),value=>{widthTuned.value=true;detailWidth.value=value})
}
function nudgeList(delta:number) {widthTuned.value=true;listWidth.value=Math.min(520,Math.max(260,listWidth.value+delta))}
function nudgeDetail(delta:number) {widthTuned.value=true;detailWidth.value=Math.min(780,Math.max(340,detailWidth.value+delta))}
async function focusFollowup() {
  tab.value='notes'
  await nextTick()
  noteField.value?.focus()
}
async function toggleMentions() {
  if(mentionsOpen.value){mentionsOpen.value=false;return}
  const id=current.value?.id
  if(!id) return
  try {
    const data=await api.get<{items:{id:number;display_name:string}[]}>('/workspace/applications/'+id+'/collaborators')
    if(current.value?.id===id){collaborators.value=data.items;mentionsOpen.value=true}
  } catch(cause){detailError.value=(cause as Error).message}
}
function mention(name:string) {
  const field=noteField.value
  const position=field?.selectionStart ?? noteBody.value.length
  const ending=field?.selectionEnd ?? position
  const suffix='@'+name+' '
  noteBody.value=(noteBody.value.slice(0,position)+suffix+noteBody.value.slice(ending)).slice(0,1000)
  mentionsOpen.value=false
  void nextTick(()=>{field?.focus();field?.setSelectionRange(position+suffix.length,position+suffix.length)})
}
async function moreNotes() {
  const id=current.value?.id
  const before=nextNote.value
  if(!id || !before) return
  try {
    const data=await api.get<NotePage>(followupPath(id)+'?before='+before)
    if(current.value?.id===id && nextNote.value===before){notes.value.push(...data.items);nextNote.value=data.next}
  } catch(cause){detailError.value=(cause as Error).message}
}
async function publish(action:'note'|'interview') {
  const id=current.value?.id
  const value=(action==='note'?noteBody.value:interviewText.value).trim()
  if(!id || !value || publishing.value || (action==='interview' && !interviewTime.value))return
  const data=new FormData()
  data.append('content',value)
  data.append('action',action)
  if(action==='interview') data.append('scheduled_at',new Date(interviewTime.value).toISOString())
  else if(noteAttachment.value) data.append('attachment',noteAttachment.value)
  publishing.value=true;detailError.value=''
  try {
    const record=await api.upload<Followup>(followupPath(id),data)
    for(const row of list.value)if(row.id===id)row.latest_note=record.body
    if(current.value?.id===id) {
      current.value.latest_note=record.body
      notes.value.unshift(record)
      if(action==='note')resetDraft()
      else interviewOpen.value=false
    }
    if(filters.value.followup?.length) void load()
    ElMessage.success('跟进记录已保存')
  } catch(cause){detailError.value=(cause as Error).message}
  finally{publishing.value=false}
}
async function calculate() {
  const id=current.value?.id
  const generation=detailVersion
  if(!id || matching.value) return
  matching.value=true;detailError.value=''
  try {
    const result=await api.post<MatchReport>('/workspace/applications/'+id+'/match')
    if(generation!==detailVersion) return
    report.value=result
  } catch(cause){if(generation===detailVersion)detailError.value=(cause as Error).message}
  finally{if(generation===detailVersion)matching.value=false}
}
async function command(action:string) {
  const id=current.value?.id
  if(!id)return
  detailError.value=''
  try {
    if(action==='reparse'){
      await api.post('/hr/applications/'+id+'/reparse')
      await load(id)
      ElMessage.success('已提交重新解析')
    } else if(action==='transfer'){
      const options=await api.get<{items:TransferJob[]}>('/hr/applications/'+id+'/transfer-options')
      if(current.value?.id===id){destinations.value=options.items;transferId.value=undefined;transferReason.value='';transferOpen.value=true}
    } else if(action==='download') await api.download('/hr/applications/'+id+'/original')
  } catch(cause){detailError.value=(cause as Error).message}
}
async function transfer() {
  const row=current.value
  if(!row || !transferId.value || !transferReason.value.trim() || publishing.value)return
  publishing.value=true;detailError.value=''
  try {
    const id=transferId.value
    await api.post('/hr/applications/'+row.id+'/transfer',{job_id:id,reason:transferReason.value,workflow_version:row.workflow_version})
    transferOpen.value=false
    await applyFilters({...filters.value,job:[String(id)]})
    ElMessage.success('已调剂到目标岗位')
  } catch(cause){detailError.value=(cause as Error).message}
  finally{publishing.value=false}
}
function tagValue(tag:ResumeCard['tags'][number]) {
  if(tag.type==='school') return tag.value.startsWith('qs-')?'QS #'+tag.value.slice(3):schoolTagLabels[tag.value] || tag.value
  if(tag.type==='experience') {
    const months=Number(tag.value)
    return months<12?months+' 个月':Math.floor(months/12)+' 年'+(months%12?' '+months%12+' 个月':'')
  }
  return label(tag.value)
}
</script>

<template>
  <main class="hire-page candidate-page">
    <header class="hire-heading">
      <div><h1>简历管理</h1><p v-if="departmentName">{{ departmentName }} ·
        <RouterLink class="hire-inline-button" :to="{name:'applications'}">查看全部简历</RouterLink></p>
        <p v-else>集中查看简历与候选人跟进记录</p></div>
      <div class="import-actions">
        <ElButton :icon="DocumentTextOutline" @click="singleImportOpen=true">单份导入</ElButton>
        <ElButton type="primary" :icon="CloudUploadOutline" @click="batchImportOpen=true">批量导入</ElButton>
      </div>
    </header>
    <p v-if="error" class="hire-error candidate-error" role="alert">{{ error }} <button class="hire-inline-button" @click="load()">重试</button></p>

    <div ref="candidateWorkspace" class="candidate-workspace hire-surface" :style="{'--candidate-width':listWidth+'px'}"
      :class="{ 'mobile-details':mobileDetails && current }">
      <aside class="candidate-aside" aria-label="候选人列表">
        <header v-show="!filterOpen" class="candidate-toolbar">
            <div class="candidate-toolbar-main"><strong>候选人</strong><span>{{ total }}</span>
              <ElButton ref="filterButton" :icon="FunnelOutline" :type="filterOpen || filterCount?'primary':'default'"
                :plain="filterOpen || !!filterCount" :aria-expanded="filterOpen"
                :aria-controls="filterOpen?'candidate-filter-panel':undefined" @click="openFilters">
                {{ filterOpen ? '收起' : '筛选' }} <b v-if="filterCount" class="filter-count">{{ filterCount }}</b>
              </ElButton>
            </div>
            <div v-show="!filterOpen" class="candidate-sort" aria-label="候选人排序">
              <button type="button" :aria-pressed="sort==='recent'" @click="sort='recent'">最近导入</button>
              <button type="button" :aria-pressed="rankable && sort==='match'" @click="changeSort">按岗位相关性</button>
              <button v-if="filterCount" type="button" class="clear-filters" @click="applyFilters({})">清空</button>
            </div>
        </header>
          <ResumeFilters v-if="filterOpen" :selected="filters" :available="available" :jobs="jobs" :followups="followupOptions"
            @apply="confirmFilters" @close="closeFilters()"/>
          <div v-show="!filterOpen" ref="candidateScroll" class="candidate-list" :aria-busy="listLoading">
            <button v-for="row in list" :key="row.id" type="button" class="candidate-row" :aria-current="current?.id===row.id?'true':undefined"
              @click="tab='overview';pick(row)">
              <span class="candidate-main">
                <span class="candidate-row-top">
                  <strong>{{ displayName(row) }}</strong>
                  <span v-if="degreeOf(row)" class="candidate-degree">{{ degreeOf(row) }}</span>
                  <small v-if="['parsing','parsed','failed'].includes(row.parse_status)">{{ label(row.parse_status) }}</small>
                </span>
                <span class="candidate-job">{{ row.job_title }}</span>
              </span>
              <span v-if="row.latest_note" class="latest-note" :title="row.latest_note">
                <em>最近跟进</em><span>{{ row.latest_note }}</span>
              </span>
            </button>
            <p v-if="!currentPageCount" class="hire-empty">{{ listLoading?'正在加载简历…':'没有符合条件的候选人' }}</p>
          </div>
          <footer v-show="!filterOpen" class="candidate-pagination"><span>{{ total }} 份</span><ElPagination v-model:current-page="page" :page-size="size" :total="total" layout="prev, pager, next" :pager-count="3" small/></footer>
      </aside>
      <div class="candidate-resize" role="separator" aria-label="调整列表宽度" aria-orientation="vertical" tabindex="0"
        title="拖动调整候选人栏宽度" :aria-valuemin="260" :aria-valuemax="520" :aria-valuenow="listWidth" @pointerdown="resizeList"
        @keydown.left.prevent="nudgeList(-12)" @keydown.right.prevent="nudgeList(12)"></div>
      <section v-if="current" class="candidate-details" aria-label="候选简历详情">
        <div class="candidate-detail-body">
          <div ref="candidateDocument" class="candidate-document-layout" :class="{ 'show-compact-preview':tab==='resume' }"
            :style="{'--detail-width':detailWidth+'px'}">
            <div class="candidate-context">
              <div class="candidate-context-top">
                <header class="candidate-detail-header">
                  <button v-if="mobileDetails" type="button" class="aside-toggle mobile-back" aria-label="返回候选列表" @click="mobileDetails=false"><ChevronBackOutline/></button>
                  <span class="candidate-avatar"><img v-if="avatar" :src="avatar" alt="候选人头像"><i v-else class="avatar-fallback" aria-hidden="true">{{ displayName(current).slice(0,2) }}</i></span>
                  <div class="candidate-identity"><h2>{{ displayName(current) }}</h2>
                    <p>{{ current.job_title }} <span>· 申请 #{{ current.id }}</span></p></div>
                  <div class="hire-toolbar candidate-action">
                    <ElButton type="primary" :icon="CalendarOutline" @click="interviewTime='';interviewText='';detailError='';interviewOpen=true">安排面试</ElButton>
                    <ElButton @click="focusFollowup">写跟进</ElButton>
                    <ElDropdown trigger="click" @command="command">
                      <ElButton aria-label="更多简历操作" :icon="EllipsisHorizontal"/>
                      <template #dropdown><ElDropdownMenu>
                        <ElDropdownItem command="download" :disabled="!current.version_id">下载原件</ElDropdownItem>
                        <ElDropdownItem command="reparse">重新解析</ElDropdownItem>
                        <ElDropdownItem command="transfer">调剂岗位</ElDropdownItem>
                      </ElDropdownMenu></template>
                    </ElDropdown>
                  </div>
                </header>
                <nav class="candidate-tabs" aria-label="简历详情内容">
                  <button type="button" :aria-pressed="tab==='overview'" @click="tab='overview'">候选概览</button>
                  <button class="compact-preview-tab" type="button" :aria-pressed="tab==='resume'" @click="tab='resume'">简历原文</button>
                  <button type="button" :aria-pressed="tab==='fields'" @click="tab='fields'">结构化字段</button>
                  <button type="button" :aria-pressed="tab==='notes'" @click="tab='notes'">跟进记录 <small v-if="notes.length">{{ notes.length }}{{ nextNote?'+':'' }}</small></button>
                </nav>
              </div>
              <div class="candidate-context-scroll">
                <p v-if="detailError" class="hire-error" role="alert">{{ detailError }}</p>
              <template v-if="tab==='overview' || tab==='resume'">
                <section v-if="candidateOverview" class="candidate-info" aria-label="基本信息">
                  <h3>基本信息</h3>
                  <dl class="candidate-summary">
                    <div v-if="candidateOverview.degree"><dt>最高学历</dt><dd>{{ candidateOverview.degree }}</dd></div>
                    <div v-if="current.tags.some(tag=>tag.type==='experience')"><dt>工作经验</dt><dd>{{ current.tags.filter(tag=>tag.type==='experience').map(tagValue).join('') }}</dd></div>
                    <div v-if="current.current_company || current.recent_company"><dt>当前公司</dt><dd>{{ current.current_company || current.recent_company }}</dd></div>
                    <div v-if="current.salary_midpoint!=null"><dt>期望月薪</dt><dd>约 {{ current.salary_midpoint.toLocaleString('zh-CN') }} 元</dd></div>
                    <div v-if="candidateOverview.education"><dt>毕业院校 / 院校信息</dt><dd>{{ candidateOverview.education.school }}</dd></div>
                    <div v-if="candidateOverview.major"><dt>所学专业</dt><dd>{{ candidateOverview.major }}</dd></div>
                    <div v-if="candidateOverview.role"><dt>求职意向</dt><dd>{{ candidateOverview.role }}</dd></div>
                    <div v-if="current.city"><dt>所在城市</dt><dd>{{ current.city }}</dd></div>
                    <div v-if="current.phone"><dt>手机号</dt><dd>{{ current.phone }}</dd></div>
                    <div v-if="current.address"><dt>地址</dt><dd>{{ current.address }}</dd></div>
                  </dl>
                  <div v-if="candidateOverview.summary" class="candidate-extra">
                    <h4>个人简介</h4><p>{{ candidateOverview.summary }}</p>
                  </div>
                  <div v-if="candidateOverview.companies.length || candidateOverview.projects.length || candidateOverview.tags.length || candidateOverview.skills.length" class="candidate-extra">
                    <h4>经历与技能</h4>
                    <div v-if="candidateOverview.companies.length || candidateOverview.projects.length" class="profile-history">
                      <p v-for="company in candidateOverview.companies" :key="'c-'+company"><i></i>{{ company }}</p>
                      <p v-for="project in candidateOverview.projects" :key="'p-'+project"><i></i>{{ project }}</p>
                    </div>
                    <div v-if="candidateOverview.tags.length" class="profile-evidence-tags">
                      <ElTag v-for="item in candidateOverview.tags" :key="item.id" size="small" effect="light">{{ tagValue(item) }}</ElTag>
                    </div>
                    <div v-if="candidateOverview.skills.length" class="profile-evidence-tags">
                      <span v-for="skill in candidateOverview.skills" :key="skill" class="profile-skill-chip">{{ skill }}</span>
                    </div>
                  </div>
                </section>
                <section class="candidate-match" aria-label="AI 匹配摘要">
                  <div class="match-heading"><SparklesOutline aria-hidden="true"/><h3>AI 匹配摘要</h3>
                    <ElButton text type="primary" :loading="matching" :disabled="!current.version_id" @click="calculate">{{ latestMatch?'重新生成':'生成摘要' }}</ElButton>
                  </div>
                  <template v-if="latestMatch">
                    <p v-if="report?.summary" class="candidate-match-text">{{ report.summary }}</p>
                    <small v-if="report?.summary" class="summary-source">{{ report.summary_source==='llm'?'模型整理 · 来自简历证据':'依据简历证据整理' }}</small>
                    <div class="match-tags">
                      <ElTag v-for="skill in latestMatch.hits" :key="skill" size="small" type="success" effect="light">{{ skill }}</ElTag>
                    </div>
                  </template>
                  <p v-else class="hire-muted">暂无匹配摘要，生成后将基于简历证据整理匹配项。</p>
                </section>
                <section class="candidate-recent" aria-label="最近跟进">
                  <div class="match-heading"><h3>最近跟进</h3><ElButton text type="primary" @click="tab='notes'">查看记录</ElButton></div>
                  <template v-if="notes[0]">
                    <p>{{ notes[0].body }}</p>
                    <small>{{ notes[0].author_name }} · {{ dateLabel(notes[0].created_at) }}</small>
                  </template>
                  <p v-else class="hire-muted">{{ detailLoading?'正在载入跟进…':'暂无跟进记录' }}</p>
                </section>
              </template>
          <section v-else-if="tab==='fields'" class="candidate-fields">
            <p v-if="detailLoading" class="hire-muted">正在载入结构化字段…</p>
            <p v-else-if="!evidenceGroups.length" class="hire-empty">暂无可展示的结构化字段。</p>
            <section v-for="[key,fields] in evidenceGroups" :key="key"><h3>{{ groupNames[key] || key }}</h3>
              <dl><div v-for="entry in fields" :key="entry.field_key+':'+entry.record_no+':'+entry.span_no">
                <dt>{{ label(entry.field_key) }} <small>{{ locationText(entry.locator) }}</small></dt><dd>{{ entry.text }}</dd>
              </div></dl>
            </section>
          </section>
          <section v-else class="candidate-notes" aria-label="跟进历史与备注">
            <p v-if="!notes.length" class="hire-muted">{{ detailLoading?'正在载入跟进记录…':'暂无跟进记录' }}</p>
            <ol v-else class="note-timeline">
              <li v-for="note in notes" :key="note.id">
                <div class="note-meta"><strong>{{ note.author_name }}</strong><time :datetime="note.created_at">{{ dateLabel(note.created_at) }}</time></div>
                <p v-if="note.scheduled_at" class="note-scheduled">面试安排 · {{ dateLabel(note.scheduled_at) }}</p>
                <p class="note-text">{{ note.body }}</p>
                <button v-if="note.attachment_name" type="button" class="note-download hire-inline-button"
                  @click="api.download(followupPath(current!.id)+'/'+note.id+'/attachment').catch(e=>detailError=e.message)"><AttachOutline/>{{ note.attachment_name }}</button>
              </li>
            </ol>
            <ElButton v-if="nextNote" text type="primary" @click="moreNotes">加载更早记录</ElButton>
            <form class="note-composer" @submit.prevent="publish('note')">
              <label for="followup-text">自定义跟进备注</label>
              <textarea id="followup-text" ref="noteField" v-model="noteBody" rows="3" maxlength="1000"
                placeholder="输入跟进记录，如：张总已面过。Ctrl + Enter 发布" @keydown.ctrl.enter.prevent="publish('note')" @keydown.meta.enter.prevent="publish('note')"></textarea>
              <div class="note-actions">
                <input ref="noteFile" type="file" hidden accept=".pdf,.doc,.docx,.png,.jpg,.jpeg,.webp,.txt"
                  @change="noteAttachment=($event.target as HTMLInputElement).files?.[0];($event.target as HTMLInputElement).value=''">
                <ElButton text :aria-expanded="mentionsOpen" @click="toggleMentions">@ 同事</ElButton>
                <ElButton text :icon="AttachOutline" @click="noteFile?.click()">添加附件</ElButton>
                <button v-if="noteAttachment" type="button" class="hire-inline-button" @click="noteAttachment=undefined">{{ noteAttachment.name }} ×</button>
                <ElButton type="primary" native-type="submit" :loading="publishing" :disabled="!noteBody.trim()">发布备注</ElButton>
              </div>
              <div v-if="mentionsOpen" class="mention-list" aria-label="同部门同事">
                <ElButton v-for="person in collaborators" :key="person.id" size="small" @click="mention(person.display_name)">{{ person.display_name }}</ElButton>
                <p v-if="!collaborators.length" class="hire-muted">暂无可提及的同事。</p>
              </div>
            </form>
          </section>
              </div>
            </div>
            <div class="candidate-document-resize" role="separator" aria-label="调整个人信息宽度" aria-orientation="vertical" tabindex="0"
              title="拖动调整个人信息与简历原文宽度" :aria-valuemin="340" :aria-valuemax="780" :aria-valuenow="detailWidth" @pointerdown="resizeDetail"
              @keydown.left.prevent="nudgeDetail(-12)" @keydown.right.prevent="nudgeDetail(12)"></div>
            <ResumeBook class="candidate-reader" :resume="current" :avatar-url="avatar" embedded/>
          </div>
        </div>
      </section>
      <section v-else class="candidate-no-selection">
        <DocumentTextOutline/><h2>查看候选人简历</h2><p>选择左侧候选人查看简历，或导入新的资料。</p>
        <ElButton v-if="!total" @click="singleImportOpen=true">导入简历</ElButton>
      </section>
    </div>

    <ResumeBatchImport v-model="singleImportOpen" mode="single" :jobs="jobs" :preferred-job="Number(filters.job?.[0]) || undefined"
      @updated="load()" @uploading="singleUploading=$event" @view="viewImport"/>
    <ResumeBatchImport v-model="batchImportOpen" mode="batch" :jobs="jobs" :preferred-job="Number(filters.job?.[0]) || undefined"
      @updated="load()" @uploading="batchUploading=$event" @view="viewImport"/>
    <ElDialog v-model="interviewOpen" title="记录面试安排" width="min(480px, 95vw)" append-to-body>
      <form id="interview-form" class="candidate-dialog" @submit.prevent="publish('interview')">
        <p v-if="detailError" class="hire-error" role="alert">{{ detailError }}</p>
        <label>面试时间<input v-model="interviewTime" type="datetime-local" required></label>
        <label>跟进内容<ElInput v-model="interviewText" type="textarea" :rows="3" maxlength="1000" placeholder="例如：张总面试，已与候选人确认" required/></label>
      </form>
      <template #footer><ElButton @click="interviewOpen=false">取消</ElButton>
        <ElButton type="primary" native-type="submit" form="interview-form" :loading="publishing" :disabled="!interviewTime || !interviewText.trim()">保存安排</ElButton></template>
    </ElDialog>
    <ElDialog v-model="transferOpen" title="调剂岗位" width="min(480px, 95vw)" append-to-body>
      <form id="transfer-form" class="candidate-dialog" @submit.prevent="transfer">
        <p v-if="detailError" class="hire-error" role="alert">{{ detailError }}</p>
        <label>目标岗位<ElSelect v-model="transferId" aria-label="目标岗位" placeholder="选择招聘中岗位">
          <ElOption v-for="job in destinations" :key="job.id" :label="job.department_name+' / '+job.title" :value="job.id"/>
        </ElSelect></label>
        <label>调剂原因<ElInput v-model="transferReason" type="textarea" :rows="3" maxlength="300" required/></label>
      </form>
      <template #footer><ElButton @click="transferOpen=false">取消</ElButton>
        <ElButton type="primary" native-type="submit" form="transfer-form" :loading="publishing" :disabled="!transferId || !transferReason.trim()">确认调剂</ElButton></template>
    </ElDialog>
  </main>
</template>

<style scoped>
.candidate-page {max-width:none;height:100%;display:grid;grid-template-rows:auto minmax(0,1fr);min-height:480px}
.import-actions {display:flex; flex-wrap:wrap; justify-content:end; gap:7px}
.import-actions .el-button {margin:0}
.candidate-page:has(.candidate-error){grid-template-rows:auto auto minmax(0,1fr)}
.candidate-workspace {display:grid;grid-template-columns:min(var(--candidate-width),38%) 7px minmax(0,1fr);min-height:0;overflow:hidden}
.candidate-aside {display:grid;grid-template-rows:auto minmax(0,1fr) auto;min-height:0;min-width:0}
.candidate-toolbar {position:relative;padding:12px;border-bottom:1px solid var(--hire-line);background:linear-gradient(180deg,#fff,#fbfdfd)}
.candidate-toolbar-main,.candidate-row-top,.candidate-pagination,.note-meta,.note-actions{display:flex;align-items:center;gap:8px;min-width:0}
.candidate-toolbar-main>span {font-size:12px;color:var(--hire-muted)}.candidate-toolbar-main>.el-button{margin-inline-start:auto}
.aside-toggle{display:grid;place-items:center;width:30px;height:30px;border:1px solid var(--hire-line);border-radius:7px;background:white;color:var(--hire-accent-dark);cursor:pointer;flex:none;transition:background-color var(--rs-motion-fast),border-color var(--rs-motion-fast),transform var(--rs-motion-fast)}
.aside-toggle:hover{background:var(--hire-soft);border-color:#9ac9c3;transform:scale(1.04)}
.aside-toggle svg {width:14px;height:14px}
.filter-count {text-align:center;min-width:17px;border-radius:9px;background:var(--hire-accent);color:white;font-size:11px;padding:2px 4px;margin-left:5px}
.candidate-sort {clear:both;display:flex;gap:4px;margin-top:9px}
.candidate-sort button {background:none;border:0;border-radius:6px;padding:5px 7px;color:var(--hire-muted);font-size:12px;cursor:pointer;transition:background-color var(--rs-motion-fast),color var(--rs-motion-fast)}
.candidate-sort button[aria-pressed=true] {background:var(--hire-soft);color:var(--hire-accent-dark)}
.candidate-sort .clear-filters{margin-left:auto;color:var(--hire-accent-dark)}
.candidate-list {overflow:auto;min-width:0}
.candidate-row {display:flex;align-items:center;gap:10px;width:100%;text-align:left;padding:12px 14px;
  border:0;border-bottom:1px solid #edf0f2;border-inline-start:3px solid transparent;background:#fff;cursor:pointer;color:var(--hire-ink);transition:background-color var(--rs-motion-fast),box-shadow var(--rs-motion-fast),transform var(--rs-motion-fast) var(--rs-ease)}
.candidate-row:hover {background:#f4faf8;transform:translateX(2px)}
.candidate-row[aria-current=true] {background:linear-gradient(90deg,#e9f8f4,#f6fbfa);border-inline-start-color:var(--hire-accent);box-shadow:inset 8px 0 18px rgba(13,137,126,.035)}
.candidate-main {display:grid;gap:4px;flex:1;min-width:0}
.candidate-row-top {font-size:14px;flex-wrap:nowrap;overflow:hidden}
.candidate-row-top strong,.candidate-job{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.candidate-row-top strong {min-width:0;font-size:14.5px;color:#1d3b45}
.candidate-row-top small{color:var(--hire-muted);font-size:12px}
.candidate-degree {flex:none;padding:1px 8px;border:1px solid #d5e7e2;border-radius:20px;background:#f0f7f5;color:var(--hire-accent-dark);font-size:11px;font-weight:600;line-height:1.7}
.candidate-job {font-size:13px;color:var(--hire-muted)}
.latest-note {flex:none;width:clamp(108px,38%,210px);display:grid;gap:1px;padding:6px 9px;border-radius:8px;background:#eef6f5;color:#355e57;font-size:11.5px;line-height:1.45}
.latest-note em {font-style:normal;font-size:9.5px;letter-spacing:.04em;color:#7d9c98}
.latest-note span {display:-webkit-box;-webkit-line-clamp:2;line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;white-space:normal;overflow-wrap:anywhere}
.candidate-pagination{padding:9px 10px;justify-content:space-between;border-top:1px solid var(--hire-line)}
.candidate-resize,.candidate-document-resize {position:relative;background:#edf2f2;cursor:col-resize;touch-action:none;outline:0}
.candidate-resize::after,.candidate-document-resize::after {content:'';position:absolute;inset:50% auto auto 50%;width:2px;height:38px;border-radius:2px;background:#b8c9c8;transform:translate(-50%,-50%);transition:height var(--rs-motion-fast),background-color var(--rs-motion-fast),box-shadow var(--rs-motion-fast)}
.candidate-resize:hover::after,.candidate-resize:focus-visible::after,.candidate-document-resize:hover::after,.candidate-document-resize:focus-visible::after {height:54px;background:var(--hire-accent);box-shadow:0 0 0 3px rgba(13,137,126,.1)}
:global(body.candidate-column-resizing){cursor:col-resize!important;user-select:none!important}
.candidate-details {min-width:0;min-height:0;display:grid;grid-template-rows:minmax(0,1fr)}
.candidate-detail-header {padding:11px 14px 8px;display:grid;grid-template-columns:auto minmax(0,1fr);align-items:center;gap:8px 10px}
.candidate-avatar{display:grid;place-items:center;width:42px;height:42px;background:linear-gradient(135deg,var(--hire-soft),#eef0ff);color:var(--hire-accent-dark);border:1px solid #d3e7e3;border-radius:50%;overflow:hidden;flex:none;box-shadow:0 5px 13px rgba(23,75,78,.08)}
.candidate-avatar img{width:100%;height:100%;object-fit:cover}.candidate-avatar svg{width:23px;height:23px}.candidate-avatar .avatar-fallback{font-style:normal;font-size:14px;font-weight:650;letter-spacing:.5px}.candidate-identity{min-width:0}
.candidate-identity h2{font-size:18px;line-height:1.3}.candidate-identity p{font-size:12px;color:var(--hire-muted);overflow-wrap:anywhere}
.candidate-action {grid-column:1/-1;justify-content:flex-start;gap:7px;padding-top:3px}
.candidate-action .el-button {min-width:0;margin:0}
.candidate-action .el-dropdown {margin-inline-start:auto}
.candidate-detail-body {overflow:hidden;display:flex;flex-direction:column;min-height:0;background:white;container-type:inline-size}
.candidate-document-layout {display:grid;grid-template-columns:var(--detail-width) 7px minmax(0,1fr);flex:1;min-height:0}
.candidate-context {min-width:0;min-height:0;display:flex;flex-direction:column;background:white}
.candidate-context-top {flex:none;border-bottom:1px solid var(--hire-line)}
.candidate-context-scroll {flex:1;min-height:0;overflow:auto;padding:13px 14px;scrollbar-width:thin}
.candidate-info,.candidate-match,.candidate-recent {padding:14px;border:1px solid var(--hire-line);border-radius:12px;background:white;margin-bottom:12px;box-shadow:0 3px 12px rgba(18,50,61,.035)}
.candidate-info h3,.candidate-match h3,.candidate-recent h3 {font-size:14px;line-height:1.4}
.candidate-summary {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px 12px;margin:13px 0 0}
.candidate-summary>div {display:grid;gap:3px;align-content:start;min-width:0}
.candidate-summary dt,.candidate-summary small,.candidate-recent small,.summary-source {font-size:12px;color:var(--hire-muted)}
.candidate-summary dd {margin:0;font-size:14px;font-weight:650;overflow-wrap:anywhere}
.candidate-extra {margin-top:12px;padding-top:10px;border-top:1px solid var(--hire-line)}
.candidate-extra h4 {font-size:13px}
.candidate-extra p {font-size:13px;line-height:1.65;margin-top:5px;white-space:pre-line;overflow-wrap:anywhere}
.profile-history {display:grid;gap:5px;margin-bottom:9px}
.profile-history p {display:flex;align-items:center;gap:8px;color:#31504f;font-size:12.5px;line-height:1.5}
.profile-history i {width:5px;height:5px;flex:none;border-radius:50%;background:var(--hire-accent);opacity:.65}
.profile-evidence-tags {display:flex;flex-wrap:wrap;gap:5px;margin-top:6px}
.profile-skill-chip {padding:2px 7px;border:1px solid #dce5ea;border-radius:4px;background:#f3f6f8;
  color:#405569;font-size:12px;line-height:1.35;overflow-wrap:anywhere}
.candidate-match {background:linear-gradient(135deg,#f1faf7,#f8fbff);border-color:#dbece8}
.match-heading {display:flex;align-items:center;gap:7px;min-height:27px}
.match-heading h3 {min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.match-heading svg {width:17px;height:17px;color:var(--hire-accent);flex:none}
.match-heading :deep(.el-button.is-text){margin-inline-start:auto;flex:none;height:26px;padding:0 11px;border:1px solid var(--hire-accent);border-radius:7px;background:var(--hire-accent) !important;color:#fff !important;font-size:12px;font-weight:600}
.match-heading :deep(.el-button.is-text:hover){border-color:var(--hire-accent-dark);background:var(--hire-accent-dark) !important;color:#fff !important}
.match-heading :deep(.el-button.is-text.is-disabled){border-color:#c3dcd7;background:#c3dcd7 !important;color:#fff !important;opacity:.85}
.candidate-match p,.candidate-recent p {margin:7px 0 3px;font-size:13px;line-height:1.65;overflow-wrap:anywhere}
.candidate-recent p:not(.hire-muted) {white-space:pre-wrap;display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}
.candidate-match .summary-source {display:inline-block;margin-top:2px;color:#4d6b72}
.match-tags {display:flex;gap:5px;flex-wrap:wrap;margin-top:9px}
.candidate-tabs {display:flex;gap:6px;align-items:center;background:#fff;padding:0 10px}
.candidate-tabs button {padding:10px 9px;border:none;border-bottom:2px solid transparent;border-radius:7px 7px 0 0;background:transparent;color:#4e6570;font:inherit;font-size:13px;cursor:pointer;white-space:nowrap;transition:background-color var(--rs-motion-fast),color var(--rs-motion-fast)}
.candidate-tabs button:hover {background:#f2faf8;color:var(--hire-accent-dark)}
.candidate-tabs button[aria-pressed=true] {color:var(--hire-accent-dark);border-bottom-color:var(--hire-accent);font-weight:650}
.candidate-tabs small {font-size:11px;margin-left:3px;color:#4e6570}
.candidate-tabs .compact-preview-tab {display:none}
.candidate-reader:not(.resume-fullscreen) {height:100%;min-height:0;border-radius:0;border:0;box-shadow:none}
@container (max-width:720px) {
  .candidate-tabs {gap:9px;padding-inline:10px}
  .candidate-tabs .compact-preview-tab {display:block}
  .candidate-document-layout {grid-template-columns:minmax(0,1fr)}
  .candidate-document-resize {display:none}
  .candidate-document-layout.show-compact-preview {grid-template-rows:auto minmax(0,1fr)}
  .candidate-document-layout.show-compact-preview .candidate-context {display:contents}
  .candidate-document-layout:not(.show-compact-preview) .candidate-reader:not(.resume-fullscreen),
  .candidate-document-layout.show-compact-preview .candidate-context-scroll {display:none}
  .candidate-reader:not(.resume-fullscreen) {border-inline-start:0}
}
.candidate-fields{display:grid;gap:12px}
.candidate-fields section{background:white;border:1px solid var(--hire-line);border-radius:7px;padding:12px;font-size:13px}
.candidate-fields h3{margin-bottom:7px;font-size:14px}.candidate-fields dl{display:grid;gap:7px}.candidate-fields dt{color:var(--hire-muted)}.candidate-fields dt small{font-size:10px}.candidate-fields dd{margin:3px 0 0;overflow-wrap:anywhere}
.candidate-notes{display:grid;gap:10px}
.note-timeline{padding:0;list-style:none}.note-timeline li{border-left:2px solid #b9d8d2;padding:0 0 12px 12px}
.note-meta{font-size:13px}.note-meta time,.note-scheduled{color:#556a75;font-size:12px}
.note-text{padding-top:4px;font-size:14px;white-space:pre-wrap;overflow-wrap:anywhere}
.note-download{display:inline-flex;align-items:center;gap:4px;margin-top:4px;font-size:12px;color:var(--hire-accent-dark)}.note-download svg{width:14px;height:14px}
.candidate-notes > :deep(.el-button.is-text){justify-self:start;height:30px;padding:0 12px;border:1px solid #d7e6e3;border-radius:7px;background:#fff;color:var(--hire-accent-dark) !important;font-size:12.5px}
.candidate-notes > :deep(.el-button.is-text:hover){border-color:var(--hire-accent);background:var(--hire-soft) !important}
.note-composer {position:sticky;bottom:0;display:grid;gap:7px;padding:11px;background:white;border:1px solid var(--hire-line);border-radius:8px;font-size:13px}
.note-composer textarea,.candidate-dialog input[type=datetime-local] {width:100%;padding:8px;border:1px solid #cdd8de;border-radius:6px;color:inherit;background:white;font:inherit}
.note-composer textarea{resize:vertical;min-height:65px}
.note-composer :deep(.el-button.is-text){height:29px;padding:0 10px;border:1px solid #dbe7e5;border-radius:7px;background:#f8fbfa;color:#35605b !important;font-size:12.5px}
.note-composer :deep(.el-button.is-text:hover){border-color:#b9d8d2;background:#eef7f5 !important;color:var(--hire-accent-dark) !important}
.mention-list{display:flex;gap:5px;flex-wrap:wrap}.note-actions{flex-wrap:wrap}
.note-actions button:last-child {margin-inline-start:auto}
.candidate-dialog{display:grid;gap:10px}.candidate-dialog label{display:grid;gap:6px}
.candidate-no-selection{display:grid;justify-items:center;align-content:center;gap:8px;padding:20px;text-align:center;background:#fcfdfd}
.candidate-no-selection svg{width:34px;height:34px;color:#67818b}.candidate-no-selection h2{font-size:16px}.candidate-no-selection p{font-size:13px;color:var(--hire-muted)}
.mobile-back{display:none}

@media(max-width:710px){
  .candidate-workspace{grid-template-columns:minmax(0,1fr)}.candidate-aside{grid-column:1;grid-row:1}
  .candidate-workspace .candidate-resize,.candidate-workspace .candidate-details,.candidate-workspace .candidate-no-selection{display:none}
  .candidate-workspace.mobile-details .candidate-aside{display:none}.candidate-workspace.mobile-details .candidate-details{display:grid}
  .mobile-details .mobile-back{display:grid}.candidate-detail-header:has(.mobile-back){grid-template-columns:auto auto minmax(0,1fr)}.candidate-summary{grid-template-columns:repeat(2,minmax(0,1fr))}
  .candidate-page .hire-heading {align-items:flex-start; flex-wrap:wrap}
  .import-actions .el-button {padding-inline:9px}
}
</style>
