<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { RefreshOutline, ChevronBackOutline, DocumentTextOutline, EllipsisHorizontal, GitNetworkOutline, CloudUploadOutline, FolderOpenOutline } from '@vicons/ionicons5'
import { ElButton, ElDropdown, ElDropdownMenu, ElDropdownItem, ElPagination, ElTag, ElMessage } from 'element-plus'
import { api } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { readResumeView, saveResumeView } from '@/api/session'
import { dateLabel, label, locationText, schoolTagLabels, type Detail, type Job, type ResumeCard, type ResumeCardPage, type Options } from '@/api/hr'
import { jobDirectory } from '@/api/recruiting'
import { addTalent, talentStatusOptions, type TalentStatusOption } from '@/api/talent'
import ResumeBook from '@/components/ResumeBook.vue'
import ResumeBatchImport from '@/components/ResumeBatchImport.vue'
import CandidateFilterMenu from '@/components/CandidateFilterMenu.vue'
import CandidateListCard from '@/components/CandidateListCard.vue'
import { filterSearchBody, type FilterSelection } from '@/components/ResumeFilters.vue'

const route=useRoute(), router=useRouter()
const auth=useAuthStore()
const positive=(value:unknown)=>{
  if(typeof value!=='string' || !/^\d+$/.test(value)) return undefined
  const number=Number(value)
  return Number.isSafeInteger(number) && number>0 ? number : undefined
}
const incomingJob=computed(()=>positive(route.query.job))
const incomingCandidate=computed(()=>positive(route.query.candidate))
const incomingDepartment=computed(()=>auth.isSuperAdmin ? positive(route.query.department) : undefined)
type ResumeTab='overview'|'resume'|'fields'
type SavedView={path:string; page:number; filters:FilterSelection;
  tab:ResumeTab; candidate?:number; mobileDetails?:boolean; listWidth?:number; detailWidth?:number; widthTuned?:boolean; viewportWidth?:number}
let restored:SavedView|undefined
try {
  const saved=JSON.parse(readResumeView() || 'null') as SavedView|null
  if(saved?.path===route.fullPath) restored=saved
} catch { /* Invalid browser state falls back to the route defaults. */ }
const directoryDepartments=ref<{id:number;name:string}[]>([])
const jobs=ref<Job[]>([])
const singleImportOpen=ref(false)
const batchImportOpen=ref(false)
const searchQuery=ref('')
const list=ref<ResumeCard[]>([])
const current=ref<ResumeCard>()
const details=ref<Detail>()
const page=ref(restored?.page || 1)
const size=ref(15)
const total=ref(0)
const available=ref<string[]>([])
const filters=ref<FilterSelection>({
  degree:restored?.filters.degree, school:restored?.filters.school, domain:restored?.filters.domain,
  capability:restored?.filters.capability, salaryRange:restored?.filters.salaryRange,
  jobs:restored?.filters.jobs,
  experienceRange:restored?.filters.experienceRange,
  department:auth.isSuperAdmin ? incomingDepartment.value ?? restored?.filters.department : undefined,
})
const statuses=ref<TalentStatusOption[]>([])
const listLoading=ref(false), detailLoading=ref(false)
const error=ref(''), detailError=ref('')
const semanticDegraded=ref(false)
const tab=ref<ResumeTab>(restored?.tab || 'overview')
const talentAdding=ref(false)
const evidenceGroups=computed(()=>{
  const result = new Map<string,Detail['evidence']>()
  for(const entry of details.value?.evidence || []) {
    const key=entry.field_key.split('.')[0]
    if(key==='project') continue
    const group=result.get(key) || []
    if(group.length<18) group.push(entry)
    result.set(key,group)
  }
  return [...result.entries()]
})
const projectGroups=computed(()=>{
  const projects=new Map<number, {header:Detail['evidence']; description:string[]}>()
  for(const entry of details.value?.evidence || []) {
    if(!entry.field_key.startsWith('project.')) continue
    let project=projects.get(entry.record_no)
    if(!project){project={header:[],description:[]};projects.set(entry.record_no,project)}
    if(entry.field_key==='project.description') project.description.push(entry.text)
    else project.header.push(entry)
  }
  return [...projects.entries()]
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
const menuFilters=computed(()=>{
  const shortcut=jobs.value.find(job=>job.id===incomingJob.value)
  if (!shortcut) return filters.value
  return { ...filters.value, jobs:[String(shortcut.id)],
    department:auth.isSuperAdmin ? filters.value.department ?? shortcut.department_id : undefined }
})
const displayName=(row:ResumeCard)=>row.name || '申请 #'+row.id
const currentMatchHits=computed(()=>current.value?.match_skills?.filter(skill=>skill.matched).length ?? 0)
const preferredImportJob=computed(()=>incomingJob.value ??
  (menuFilters.value.jobs?.length===1 ? positive(menuFilters.value.jobs[0]) : undefined))

function followupStatus(row:ResumeCard) {
  const option=statuses.value.find(status=>status.code===row.talent_status)
  if (row.in_talent) return {label:row.talent_status_label || option?.label || '待跟进',color:option?.color || '#64748b'}
  return row.latest_note ? {label:'已跟进',color:'#22694d'} : {label:'未跟进',color:'#64748b'}
}

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
  details.value=undefined
  detailError.value=''
  detailLoading.value=true
  const requests = [
    api.get<Detail>('/hr/applications/'+id),
    row.has_avatar && row.version_id ? api.get<Blob>('/hr/applications/'+id+'/avatar?version='+row.version_id) : Promise.resolve(undefined),
  ] as const
  const [detail, photo]=await Promise.allSettled(requests)
  if(sequence!==detailVersion) return
  if(detail.status==='fulfilled') details.value=detail.value
  else detailError.value=(detail.reason as Error).message
  if(photo.status==='fulfilled' && photo.value) avatar.value=URL.createObjectURL(photo.value)
  detailLoading.value=false
}

function resetCurrent() {
  current.value=undefined
  details.value=undefined
  detailVersion++
  if(avatar.value) URL.revokeObjectURL(avatar.value)
  avatar.value=''
}
async function load(preferred?:number, bringForward?:boolean) {
  const sequence=++listVersion
  if(refreshTimer) clearTimeout(refreshTimer)
  listLoading.value=true;error.value='';semanticDegraded.value=false
  try {
    const records=await api.post<ResumeCardPage>('/hr/resume-cards/search',{
      ...filterSearchBody(filters.value),page:page.value,size:size.value,job_id:incomingJob.value,sort:'relevance',
      semantic_query:searchQuery.value || undefined,
      department_id:auth.isSuperAdmin ? filters.value.department : undefined,
    })
    if(sequence!==listVersion) return
    list.value=records.items
    total.value=records.total
    semanticDegraded.value=Boolean(records.semantic_degraded)
    available.value=records.available_facets || []
    const maxPage=Math.max(1,Math.ceil(records.total/size.value))
    if(page.value>maxPage){page.value=maxPage;return}
    const explicit=searchQuery.value ? undefined : incomingCandidate.value
    const requested=preferred ?? explicit
    let row=records.items.find(row=>row.id===(requested ?? current.value?.id))
    if(!row && requested && explicit===requested) {
      const direct=await api.post<ResumeCardPage>('/hr/resume-cards/search',{application_id:requested,size:1,sort:'relevance'})
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
function refreshFilters() {
  candidateScroll.value?.scrollTo({top:0})
  if(page.value!==1) page.value=1
  else void load()
}
function resumeImported(_job:number,id:number) {
  void load(id)
}
function searchCandidates(query:string) {
  searchQuery.value=query
  refreshFilters()
}
async function applyFilters(values:FilterSelection) {
  filters.value=values
  if(route.query.job || route.query.candidate || route.query.department) {
    // Preserve new filter selections when removing the shortcut in the URL.
    const query={...route.query}
    delete query.job
    delete query.candidate
    delete query.department
    ignoredRoute=true
    try {await router.replace({name:'applications',query})}
    finally {ignoredRoute=false}
  }
  refreshFilters()
}
let ignoredRoute=false
watch([incomingJob,incomingCandidate,incomingDepartment],([,candidate,department])=>{
  if(ignoredRoute) return
  filters.value={...filters.value,department,jobs:undefined}
  if(page.value!==1) page.value=1
  else void load(candidate)
})
watch([page,size],()=>void load())
watch([page,filters,tab,()=>current.value?.id,mobileDetails,listWidth,detailWidth],()=>{
  if(route.name!=='applications') return
  saveResumeView(JSON.stringify({path:route.fullPath,page:page.value,
    filters:filters.value,
    tab:tab.value,candidate:current.value?.id,mobileDetails:mobileDetails.value,
    listWidth:listWidth.value,detailWidth:detailWidth.value,widthTuned:widthTuned.value,
    viewportWidth:window.innerWidth} satisfies SavedView))
},{deep:true,flush:'post'})
onMounted(()=>{
  void load(restored?.candidate,restored?.mobileDetails)
  api.get<Options>('/hr/options').then(result=>{
    directoryDepartments.value=result.departments
  }).catch(cause=>{error.value=(cause as Error).message})
  jobDirectory().then(result=>{jobs.value=result.items}).catch(cause=>{error.value=(cause as Error).message})
  talentStatusOptions().then(result=>{statuses.value=result}).catch(cause=>{error.value=(cause as Error).message})
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
async function putInTalent() {
  const row=current.value
  if(!row || talentAdding.value) return
  talentAdding.value=true;detailError.value=''
  try {
    const result=await addTalent({ application_id: row.id })
    // 复制语义：更新本地卡片的人才库标识
    if(current.value?.id===row.id) current.value.in_talent=true
    for(const item of list.value) if(item.id===row.id) item.in_talent=true
    ElMessage.success(result.duplicate ? '该候选人已在人才库中' : '已放入人才库')
    void load(current.value?.id)
  } catch(cause){detailError.value=(cause as Error).message}
  finally{talentAdding.value=false}
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
    } else if(action==='download') await api.download('/hr/applications/'+id+'/original')
  } catch(cause){detailError.value=(cause as Error).message}
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
      <div><h1>简历管理</h1><p>简历由部门共享目录自动入库</p></div>
      <div class="hire-toolbar import-actions">
        <ElButton :icon="RefreshOutline" :loading="listLoading" @click="load()">刷新简历</ElButton>
        <ElButton :icon="CloudUploadOutline" @click="singleImportOpen=true">单份导入</ElButton>
        <ElButton type="primary" :icon="FolderOpenOutline" @click="batchImportOpen=true">批量导入</ElButton>
      </div>
    </header>
    <p v-if="error" class="hire-error candidate-error" role="alert">{{ error }} <button class="hire-inline-button" @click="load()">重试</button></p>
    <p v-else-if="semanticDegraded" class="hire-error candidate-error" role="status">向量搜索暂不可用，当前为关键词结果。 <button class="hire-inline-button" @click="load()">重试</button></p>

    <div ref="candidateWorkspace" class="candidate-workspace hire-surface" :style="{'--candidate-width':listWidth+'px'}"
      :class="{ 'mobile-details':mobileDetails && current }">
      <aside class="candidate-aside" aria-label="候选人列表">
        <header class="candidate-toolbar">
            <div class="candidate-toolbar-main"><strong>候选人</strong><span>{{ total }}</span>
              <CandidateFilterMenu title="筛选简历" :selected="menuFilters" :available="available" :search-query="searchQuery"
                :jobs="jobs" :requires-department="auth.isSuperAdmin"
                :departments="auth.isSuperAdmin ? directoryDepartments : []" @apply="applyFilters" @search="searchCandidates"/>
            </div>
            <small class="candidate-order">按岗位匹配度排序</small>
        </header>
          <div ref="candidateScroll" class="candidate-list" :aria-busy="listLoading">
            <CandidateListCard v-for="row in list" :key="row.id" :row="row" :active="current?.id===row.id"
              appearance="resume" :status-label="followupStatus(row).label" :status-color="followupStatus(row).color"
              @select="tab='overview';pick(row)"/>
            <p v-if="!list.length" class="hire-empty">{{ listLoading?'正在加载简历…':'没有符合条件的候选人' }}</p>
          </div>
          <footer class="candidate-pagination"><span>{{ total }} 份</span><ElPagination v-model:current-page="page" :page-size="size" :total="total" layout="prev, pager, next" :pager-count="3" small/></footer>
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
                  <div class="candidate-identity"><h2>{{ displayName(current) }}
                    <span v-if="current.in_talent" class="talent-badge">已入人才库</span></h2>
                    <p>{{ current.job_title }} <span>· 申请 #{{ current.id }}</span></p></div>
                  <div class="hire-toolbar candidate-action">
                    <ElButton type="primary" :icon="GitNetworkOutline" :loading="talentAdding" :disabled="current.in_talent || !current.version_id" @click="putInTalent">
                      {{ current.in_talent ? '已在人才库' : '放入人才库' }}</ElButton>
                    <ElDropdown trigger="click" @command="command">
                      <ElButton aria-label="更多简历操作" :icon="EllipsisHorizontal"/>
                      <template #dropdown><ElDropdownMenu>
                        <ElDropdownItem command="download" :disabled="!current.version_id">下载原件</ElDropdownItem>
                        <ElDropdownItem command="reparse">重新解析</ElDropdownItem>
                      </ElDropdownMenu></template>
                    </ElDropdown>
                  </div>
                </header>
                <nav class="candidate-tabs" aria-label="简历详情内容">
                  <button type="button" :aria-pressed="tab==='overview'" @click="tab='overview'">候选概览</button>
                  <button class="compact-preview-tab" type="button" :aria-pressed="tab==='resume'" @click="tab='resume'">简历原文</button>
                  <button type="button" :aria-pressed="tab==='fields'" @click="tab='fields'">结构化字段</button>
                </nav>
              </div>
              <div class="candidate-context-scroll">
                <p v-if="detailError" class="hire-error" role="alert">{{ detailError }}</p>
              <template v-if="tab==='overview' || tab==='resume'">
                <section class="candidate-match" aria-labelledby="candidate-match-title">
                  <div class="match-heading"><h3 id="candidate-match-title">岗位匹配度</h3>
                    <strong v-if="current.match_percent!=null">{{ current.match_percent.toFixed(2) }}%</strong>
                  </div>
                  <p v-if="current.match_score" class="match-caption">
                    {{ current.job_title }} · 职责 {{ current.match_score.duty.toFixed(2) }} 分 · 技能 {{ current.match_score.skill.toFixed(2) }} 分
                  </p>
                  <p v-if="current.match_skills?.length" class="match-caption">
                    {{ currentMatchHits }} / {{ current.match_skills.length }} 项技能有简历证据
                  </p>
                  <table v-if="current.match_skills?.length" class="match-table">
                    <thead><tr><th scope="col">岗位技能要求</th><th scope="col">核对结果</th></tr></thead>
                    <tbody><tr v-for="skill in current.match_skills" :key="skill.name">
                      <th scope="row">{{ skill.name }}</th>
                      <td><span :class="{'match-met':skill.matched}">{{ skill.matched ? '有证据' : '待核实' }}</span></td>
                    </tr></tbody>
                  </table>
                  <p v-if="current.match_percent==null" class="match-caption">{{ !current.job_id ? '关联岗位后显示匹配结果。' : !current.version_id ? '简历解析完成后显示匹配结果。' : '待岗位索引或筛选条件就绪后显示匹配结果。' }}</p>
                </section>
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
              </template>
          <section v-else-if="tab==='fields'" class="candidate-fields">
            <p v-if="detailLoading" class="hire-muted">正在载入结构化字段…</p>
            <p v-else-if="!evidenceGroups.length && !projectGroups.length" class="hire-empty">暂无可展示的结构化字段。</p>
            <section v-for="[key,fields] in evidenceGroups" :key="key"><h3>{{ groupNames[key] || key }}</h3>
              <dl><div v-for="entry in fields" :key="entry.field_key+':'+entry.record_no+':'+entry.span_no">
                <dt>{{ label(entry.field_key) }} <small>{{ locationText(entry.locator) }}</small></dt><dd>{{ entry.text }}</dd>
              </div></dl>
            </section>
            <section v-if="projectGroups.length" class="candidate-projects">
              <h3>项目经历</h3>
              <article v-for="[record,project] in projectGroups" :key="record">
                <h4>{{ project.header.find(entry=>entry.field_key==='project.project_name')?.text || '项目' }}</h4>
                <p v-if="project.header.some(entry=>entry.field_key!=='project.project_name')" class="project-meta">
                  {{ project.header.filter(entry=>entry.field_key!=='project.project_name').map(entry=>entry.text).join(' · ') }}
                </p>
                <p v-if="project.description.length" class="project-description">{{ project.description.join('\n') }}</p>
              </article>
            </section>
          </section>
              </div>
            </div>
            <div class="candidate-document-resize" role="separator" aria-label="调整个人信息宽度" aria-orientation="vertical" tabindex="0"
              title="拖动调整个人信息与简历原文宽度" :aria-valuemin="340" :aria-valuemax="780" :aria-valuenow="detailWidth" @pointerdown="resizeDetail"
              @keydown.left.prevent="nudgeDetail(-12)" @keydown.right.prevent="nudgeDetail(12)"></div>
            <ResumeBook class="candidate-reader" :resume="current" :avatar-url="avatar" :pdf-original="details?.original_mime_type==='application/pdf'" embedded/>
          </div>
        </div>
      </section>
      <section v-else class="candidate-no-selection">
        <DocumentTextOutline/><h2>查看候选人简历</h2><p>导入后的简历将在左侧显示。</p>
      </section>
    </div>

    <ResumeBatchImport v-model="singleImportOpen" mode="single" :jobs="jobs" :preferred-job="preferredImportJob"
      @imported="resumeImported" @updated="load()"/>
    <ResumeBatchImport v-model="batchImportOpen" mode="batch" :jobs="jobs" :preferred-job="preferredImportJob"
      @imported="resumeImported" @updated="load()"/>
  </main>
</template>

<style scoped>
.candidate-page {max-width:none;height:100%;display:grid;grid-template-rows:auto minmax(0,1fr);min-height:480px}
.candidate-page:has(.candidate-error){grid-template-rows:auto auto minmax(0,1fr)}
.import-actions {gap:8px}
.import-actions :deep(.el-button+.el-button) {margin-left:0}
.candidate-workspace {display:grid;grid-template-columns:min(var(--candidate-width),38%) 7px minmax(0,1fr);min-height:0;overflow:hidden}
.candidate-aside {display:grid;grid-template-rows:auto minmax(0,1fr) auto;min-height:0;min-width:0}
.candidate-toolbar {position:relative;padding:12px;border-bottom:1px solid var(--hire-line);background:linear-gradient(180deg,#fff,#fbfdfd)}
.candidate-toolbar-main,.candidate-pagination{display:flex;align-items:center;gap:8px;min-width:0}
.candidate-toolbar-main>span {font-size:12px;color:var(--hire-muted)}
.candidate-toolbar-main>strong {white-space:nowrap}
.candidate-order {display:block;margin-top:5px;color:var(--hire-muted);font-size:11px}
.aside-toggle{display:grid;place-items:center;width:30px;height:30px;border:1px solid var(--hire-line);border-radius:7px;background:white;color:var(--hire-accent-dark);cursor:pointer;flex:none;transition:background-color var(--rs-motion-fast),border-color var(--rs-motion-fast),transform var(--rs-motion-fast)}
.aside-toggle:hover{background:var(--hire-soft);border-color:#9ac9c3;transform:scale(1.04)}
.aside-toggle svg {width:14px;height:14px}
.candidate-list {display:grid;align-content:start;gap:8px;padding:10px;overflow:auto;min-width:0;background:#f7faf9;scrollbar-width:thin}
.talent-badge {flex:none;display:inline-flex;align-items:center;gap:3px;padding:1px 7px;border-radius:20px;
  background:linear-gradient(135deg,#e8f1ff,#f0e8ff);border:1px solid #c7d4f0;color:#4a5f9e;font-size:10.5px;font-weight:600;line-height:1.6;white-space:nowrap}
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
.match-heading {display:flex;align-items:center;justify-content:space-between;gap:7px;min-height:27px}
.match-heading strong {color:#176c60;font-size:24px;font-variant-numeric:tabular-nums;line-height:1.2}
.match-caption {margin:5px 0 0;color:#4d6b72;font-size:12px;line-height:1.6;overflow-wrap:anywhere}
.match-table {margin-top:10px;width:100%;border-collapse:collapse;text-align:start;font-size:12px}
.match-table th,.match-table td {padding:7px 4px;border-bottom:1px solid #dceae7;text-align:start;overflow-wrap:anywhere}
.match-table thead th {color:#536976;font-size:11px;font-weight:600}
.match-table :is(th,td):last-child {width:78px;text-align:end}
.match-table tbody th {font-weight:600}
.match-table td span {white-space:nowrap;border-radius:5px;padding:2px 6px;background:#f4eee1;color:#805919}
.match-table td .match-met {background:#e1f3e9;color:#216547}
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
.candidate-projects article+article{margin-top:13px;padding-top:13px;border-top:1px solid var(--hire-line)}
.candidate-projects h4{font-size:13px}
.project-meta{margin-top:3px;color:var(--hire-muted);font-size:12px}
.project-description{margin-top:8px;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.7}
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
