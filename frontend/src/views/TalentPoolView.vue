<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { CalendarOutline, ChevronBackOutline, ChatbubbleEllipsesOutline, PeopleOutline, TrashOutline } from '@vicons/ionicons5'
import { ElButton, ElDialog, ElInput, ElMessage, ElPagination, ElSelect, ElOption, ElDatePicker } from 'element-plus'
import { api } from '@/api/client'
import { dateLabel, label, type Detail, type Job, type Options } from '@/api/hr'
import { jobDirectory } from '@/api/recruiting'
import {
  talentStatusOptions, searchTalent, talentDetail, addTalentFollowup, deleteTalentFollowup,
  scheduleTalentInterview, talentFollowups,
  type TalentResumeCard, type TalentStatusOption, type TalentFollowup, type TalentInterview,
} from '@/api/talent'
import { useAuthStore } from '@/stores/auth'
import CandidateFilterMenu from '@/components/CandidateFilterMenu.vue'
import CandidateListCard from '@/components/CandidateListCard.vue'
import ResumeBook from '@/components/ResumeBook.vue'
import { filterSearchBody, type FilterSelection } from '@/components/ResumeFilters.vue'

const auth = useAuthStore()
const departments = ref<Options['departments']>([])
const statuses = ref<TalentStatusOption[]>([])
const capabilities = ref<Options['capabilities']>([])
const jobs = ref<Job[]>([])
const available = ref<string[]>([])
const filters = ref<FilterSelection>({})
const filterStatus = ref<string>()
const list = ref<TalentResumeCard[]>([])
const listScroll = ref<HTMLElement>()
const loading = ref(false)
const error = ref('')
const total = ref(0)
const statusCounts = ref<Record<string,number>>({})
const page = ref(1)
const pageSize = 15
let listRequest = 0

const current = ref<TalentResumeCard>()
const candidate = ref<Detail>()
const detailLoading = ref(false)
const detailError = ref('')
const followups = ref<TalentFollowup[]>([])
const nextFollowup = ref<number | null>(null)
const interviews = ref<TalentInterview[]>([])
const mobileDetails = ref(false)
const compactPanel = ref<'resume'|'process'>('resume')
let detailRequest = 0

const noteBody = ref('')
const noteStatus = ref<string>()
const customStatus = ref('')
const noteSaving = ref(false)
const composer = ref<HTMLElement>()
const canPublish = computed(() => Boolean(noteBody.value.trim() || noteStatus.value)
  && (noteStatus.value !== 'custom' || Boolean(customStatus.value.trim())))

function statusLabel(code: string, custom?: string | null) {
  if (code === 'custom') return custom || '自定义'
  return statuses.value.find(status=>status.code===code)?.label || (code==='uncontacted' ? '待跟进' : '已有跟进')
}
const statusColor = (code: string) => statuses.value.find(status=>status.code===code)?.color || '#64748b'
const terminalStatus = computed(() => ['hired','rejected','blacklist'].includes(current.value?.talent_status ?? ''))
const hasInterview = computed(() => Boolean(interviews.value.length || current.value?.talent_status==='interviewing'))
const workflowSteps = computed(() => [
  {label:'跟进', detail:followups.value.length ? '已有记录' : '待记录', state:hasInterview.value || terminalStatus.value ? 'done' : 'active'},
  {label:'面试', detail:interviews.value.length ? `已安排 ${interviews.value.length} 场` : current.value?.talent_status==='interviewing' ? '进行中' : '待安排', state:terminalStatus.value ? (hasInterview.value ? 'done' : 'waiting') : hasInterview.value ? 'active' : 'waiting'},
  {label:'结果', detail:terminalStatus.value && current.value ? statusLabel(current.value.talent_status) : '待确认', state:terminalStatus.value ? 'active' : 'waiting'},
])
const basics = computed(() => {
  const row=current.value
  if (!row) return []
  const degree=row.tags.find(item=>item.type==='degree')?.value
  return [
    {label:'来源岗位',value:row.job_id ? row.job_title : '部门简历库'},
    {label:'求职意向',value:row.target_role},
    {label:'最高学历',value:degree && label(degree)},
    {label:'所在城市',value:row.city},
  ].filter(item=>item.value)
})

async function load() {
  const request = ++listRequest
  loading.value = true
  error.value = ''
  try {
    const result = await searchTalent({ ...filterSearchBody(filters.value), page:page.value, size:pageSize }, filterStatus.value)
    if (request !== listRequest) return
    total.value = result.total
    statusCounts.value = result.counts
    available.value = result.available_facets || []
    const lastPage = Math.max(1, Math.ceil(result.total / pageSize))
    if (page.value > lastPage) {
      page.value = lastPage
      void load()
      return
    }
    list.value = result.items
    const row = result.items.find(row=>row.talent_id===current.value?.talent_id) || result.items[0]
    if (row) void pick(row, false)
    else {current.value=undefined; mobileDetails.value=false; detailRequest++}
  } catch (cause) {
    if (request === listRequest) error.value = (cause as Error).message
  } finally {
    if (request === listRequest) loading.value = false
  }
}
function refreshFilters() {
  page.value = 1
  listScroll.value?.scrollTo({top:0})
  void load()
}
function applyFilters(values: FilterSelection) {
  filters.value = values
  refreshFilters()
}
function toggleStatus(code: string) {
  filterStatus.value = filterStatus.value === code ? undefined : code
  refreshFilters()
}

async function pick(row: TalentResumeCard, reveal = true) {
  if (reveal) {
    mobileDetails.value = true
    compactPanel.value = 'resume'
  }
  const unchanged = current.value?.talent_id === row.talent_id && current.value.version_id === row.version_id
    && current.value.job_id === row.job_id && current.value.workflow_version === row.workflow_version
  current.value = row
  if (unchanged && (candidate.value || detailLoading.value)) return
  const request = ++detailRequest
  candidate.value = undefined
  noteBody.value = ''
  noteStatus.value = undefined
  customStatus.value = ''
  followups.value = []
  interviews.value = []
  nextFollowup.value = null
  detailError.value = ''
  detailLoading.value = true
  const [info, history, resume] = await Promise.allSettled([
    talentDetail(row.talent_id), talentFollowups(row.talent_id),
    api.get<Detail>('/hr/applications/'+row.id+'?include_evidence=false'),
  ])
  if (request !== detailRequest) return
  if (info.status==='fulfilled') interviews.value = info.value.interviews
  if (history.status==='fulfilled') {
    followups.value = history.value.items
    nextFollowup.value = history.value.next
  }
  if (resume.status==='fulfilled') candidate.value = resume.value
  const failure = [info,history,resume].find(result=>result.status==='rejected')
  if (failure?.status==='rejected') detailError.value = (failure.reason as Error).message
  detailLoading.value = false
}

async function goToComposer() {
  compactPanel.value = 'process'
  await nextTick()
  composer.value?.scrollIntoView({block:'nearest'})
  composer.value?.querySelector('textarea')?.focus()
}

async function publishNote() {
  const id = current.value?.talent_id
  if (!id || !canPublish.value || noteSaving.value || detailLoading.value) return
  noteSaving.value = true
  detailError.value = ''
  try {
    const record = await addTalentFollowup(id, {
      content:noteBody.value.trim(),status:noteStatus.value,
      status_label:noteStatus.value==='custom' ? customStatus.value.trim() : undefined,
    })
    if (current.value?.talent_id===id) {
      followups.value.unshift(record)
      if (record.status) current.value.talent_status = record.status
      current.value.talent_status_label = record.status_label
      noteBody.value = ''
      noteStatus.value = undefined
      customStatus.value = ''
    }
    ElMessage.success('跟进已保存')
    void load()
  } catch (cause) {
    if (current.value?.talent_id===id) detailError.value = (cause as Error).message
  } finally {
    noteSaving.value = false
  }
}

async function moreFollowups() {
  const id = current.value?.talent_id
  if (!id || nextFollowup.value===null || detailLoading.value) return
  detailLoading.value = true
  try {
    const result = await talentFollowups(id,nextFollowup.value)
    if (current.value?.talent_id===id) {
      followups.value.push(...result.items)
      nextFollowup.value = result.next
    }
  } catch (cause) {
    if (current.value?.talent_id===id) detailError.value = (cause as Error).message
  } finally {
    if (current.value?.talent_id===id) detailLoading.value = false
  }
}

async function removeFollowup(note: TalentFollowup) {
  const id = current.value?.talent_id
  if (!id) return
  try {
    await deleteTalentFollowup(id,note.id)
    if (current.value?.talent_id===id) followups.value = followups.value.filter(record=>record.id!==note.id)
    ElMessage.success('跟进已删除')
  } catch (cause) {
    if (current.value?.talent_id===id) detailError.value = (cause as Error).message
  }
}

const interviewOpen = ref(false)
const interviewTime = ref<Date | null>(null)
const interviewNote = ref('')
const interviewSaving = ref(false)
const interviewError = ref('')
function openInterview() {
  interviewTime.value = null
  interviewNote.value = ''
  interviewError.value = ''
  interviewOpen.value = true
}
async function saveInterview() {
  const id = current.value?.talent_id
  if (!id || !interviewTime.value || interviewSaving.value) return
  interviewSaving.value = true
  interviewError.value = ''
  try {
    await scheduleTalentInterview(id, {
      scheduled_at:interviewTime.value.toISOString(), note:interviewNote.value.trim() || null,
    })
    interviewOpen.value = false
    ElMessage.success('面试已安排')
    void load()
    const updated = await talentDetail(id)
    if (current.value?.talent_id===id) interviews.value = updated.interviews
  } catch (cause) {
    const message = (cause as Error).message
    if (interviewOpen.value) interviewError.value = message
    else if (current.value?.talent_id===id) detailError.value = message
  } finally {
    interviewSaving.value = false
  }
}

onMounted(() => {
  void load()
  talentStatusOptions().then(result=>{statuses.value=result}).catch(cause=>{error.value=(cause as Error).message})
  api.get<Options>('/hr/options').then(result=>{
    capabilities.value = result.capabilities || []
    departments.value = result.departments
  }).catch(cause=>{error.value=(cause as Error).message})
  jobDirectory().then(result=>{jobs.value=result.items}).catch(cause=>{error.value=(cause as Error).message})
})
onBeforeUnmount(()=>{listRequest++;detailRequest++})
</script>

<template>
  <main class="hire-page talent-page">
    <header class="hire-heading talent-heading">
      <div><h1>人才管理</h1><p>跟进候选人和面试安排</p></div>
    </header>

    <p v-if="error" class="hire-error" role="alert">{{ error }} <button type="button" class="hire-inline-button" @click="load">重试</button></p>

    <section class="talent-board" aria-label="按跟进状态筛选">
      <button v-for="item in statuses" :key="item.code" type="button" class="board-stat"
        :style="{'--stat-color':item.color}" :aria-pressed="filterStatus===item.code" @click="toggleStatus(item.code)">
        <i aria-hidden="true"></i><span>{{ item.label }}</span><strong>{{ statusCounts[item.code] ?? 0 }}</strong>
      </button>
    </section>

    <div class="talent-workspace hire-surface" :class="{'mobile-details':mobileDetails && current}">
      <aside class="talent-aside" aria-label="人才列表">
        <header class="talent-toolbar"><strong>候选人</strong><span>{{ total }}</span>
          <CandidateFilterMenu title="筛选人才" :selected="filters" :available="available" :capabilities="capabilities"
            :jobs="jobs" :requires-department="auth.isSuperAdmin" :departments="auth.isSuperAdmin ? departments : []" @apply="applyFilters"/>
        </header>
        <div ref="listScroll" class="talent-list" :aria-busy="loading">
          <CandidateListCard v-for="row in list" :key="row.talent_id" :row="row" appearance="talent" :active="row.talent_id===current?.talent_id"
            :status-label="statusLabel(row.talent_status,row.talent_status_label)" :status-color="statusColor(row.talent_status)" @select="pick(row)"/>
          <p v-if="!list.length" class="hire-empty">{{ loading ? '正在加载人才…' : '没有符合条件的人才' }}</p>
        </div>
        <footer class="talent-pagination"><span>共 {{ total }} 位</span>
          <ElPagination v-model:current-page="page" :page-size="pageSize" :total="total" layout="prev, pager, next" :pager-count="3" small @current-change="load"/>
        </footer>
      </aside>

      <section v-if="current" class="talent-detail" aria-label="人才详情">
        <div class="talent-detail-grid" :class="{'compact-preview':compactPanel==='resume'}">
          <div class="talent-context">
            <header class="talent-context-header">
              <button type="button" class="talent-mobile-back" aria-label="返回人才列表" @click="mobileDetails=false"><ChevronBackOutline/></button>
              <span class="talent-initial" aria-hidden="true">{{ current.name?.slice(0,2) || '简历' }}</span>
              <div class="talent-identity"><h2>{{ current.name || '申请 #'+current.id }}</h2>
                <span class="talent-status" :style="{'--status-color':statusColor(current.talent_status)}">{{ statusLabel(current.talent_status,current.talent_status_label) }}</span>
              </div>
              <div class="talent-context-actions">
                <ElButton type="primary" :icon="CalendarOutline" @click="openInterview">安排面试</ElButton>
                <ElButton :icon="ChatbubbleEllipsesOutline" @click="goToComposer">写跟进</ElButton>
              </div>
              <nav class="talent-compact-tabs" aria-label="人才详情内容">
                <button type="button" :aria-pressed="compactPanel==='resume'" @click="compactPanel='resume'">简历原文</button>
                <button type="button" :aria-pressed="compactPanel==='process'" @click="compactPanel='process'">面试与跟进</button>
              </nav>
            </header>

            <div class="talent-context-scroll">
              <p v-if="detailError" class="hire-error" role="alert">{{ detailError }}</p>
              <section class="talent-block talent-process" aria-label="面试流程">
                <h3>面试流程</h3>
                <ol class="talent-steps">
                  <li v-for="(step,index) in workflowSteps" :key="step.label" :data-state="step.state" :aria-current="step.state==='active' ? 'step' : undefined">
                    <i aria-hidden="true">{{ index+1 }}</i><strong>{{ step.label }}</strong><span>{{ step.detail }}</span>
                  </li>
                </ol>
                <p v-if="candidate?.rounds.length" class="talent-rounds"><span>岗位轮次</span>{{ candidate.rounds.map(round=>round.name).join(' → ') }}</p>
                <ul v-if="interviews.length" class="interview-list">
                  <li v-for="interview in interviews" :key="interview.id"><CalendarOutline aria-hidden="true"/>
                    <div><time :datetime="interview.scheduled_at">{{ dateLabel(interview.scheduled_at) }}</time><p v-if="interview.note">{{ interview.note }}</p></div>
                  </li>
                </ul>
                <p v-else class="talent-muted">{{ detailLoading ? '正在加载面试安排…' : '暂无面试安排' }}</p>
              </section>

              <section class="talent-block" aria-label="基本信息">
                <h3>基本信息</h3>
                <dl class="talent-basics"><div v-for="item in basics" :key="item.label"><dt>{{ item.label }}</dt><dd>{{ item.value }}</dd></div></dl>
              </section>

              <section class="talent-block" aria-label="跟进记录">
                <h3>跟进记录</h3>
                <div ref="composer" class="note-composer">
                  <ElInput v-model="noteBody" type="textarea" :rows="2" maxlength="1000" aria-label="跟进内容"
                    placeholder="输入跟进内容" @keydown.ctrl.enter.prevent="publishNote" @keydown.meta.enter.prevent="publishNote"/>
                  <div class="note-actions">
                    <ElSelect v-model="noteStatus" clearable placeholder="选择状态" aria-label="跟进状态">
                      <ElOption v-for="option in statuses" :key="option.code" :label="option.label" :value="option.code"/>
                    </ElSelect>
                    <ElButton type="primary" :loading="noteSaving" :disabled="!canPublish || detailLoading" @click="publishNote">保存跟进</ElButton>
                  </div>
                  <ElInput v-if="noteStatus==='custom'" v-model="customStatus" maxlength="40" aria-label="自定义状态" placeholder="输入自定义状态"/>
                </div>
                <ul v-if="followups.length" class="followup-list">
                  <li v-for="note in followups" :key="note.id">
                    <div class="followup-meta"><strong>{{ note.author_name }}</strong><time :datetime="note.created_at">{{ dateLabel(note.created_at) }}</time>
                      <button v-if="auth.isSuperAdmin || note.author_id===auth.session?.userId" type="button" class="followup-delete" title="删除跟进" aria-label="删除跟进" @click="removeFollowup(note)"><TrashOutline/></button>
                    </div>
                    <p>{{ note.body }}</p>
                    <span v-if="note.status" class="talent-status" :style="{'--status-color':statusColor(note.status)}">{{ statusLabel(note.status,note.status_label) }}</span>
                  </li>
                </ul>
                <p v-else class="talent-muted">{{ detailLoading ? '正在加载跟进记录…' : '暂无跟进记录' }}</p>
                <ElButton v-if="nextFollowup!==null" size="small" :loading="detailLoading" @click="moreFollowups">加载更早记录</ElButton>
              </section>
            </div>
          </div>
          <ResumeBook class="talent-reader" :resume="current" :pdf-original="candidate?.original_mime_type==='application/pdf'" embedded/>
        </div>
      </section>
      <section v-else class="talent-empty"><PeopleOutline aria-hidden="true"/><h2>暂无候选人</h2><p>可以从简历管理将候选人放入人才库。</p></section>
    </div>

    <ElDialog v-model="interviewOpen" title="安排面试" width="min(440px, 92vw)" append-to-body>
      <div class="interview-form">
        <p v-if="interviewError" class="hire-error" role="alert">{{ interviewError }}</p>
        <ElDatePicker v-model="interviewTime" type="datetime" placeholder="选择面试时间" aria-label="面试时间" style="width:100%"
          :disabled-date="(day:Date)=>day.getTime()<Date.now()-86400000"/>
        <ElInput v-model="interviewNote" type="textarea" :rows="3" maxlength="1000" aria-label="面试备注" placeholder="面试备注（可选）"/>
      </div>
      <template #footer>
        <ElButton @click="interviewOpen=false">取消</ElButton>
        <ElButton type="primary" :loading="interviewSaving" :disabled="!interviewTime" @click="saveInterview">保存</ElButton>
      </template>
    </ElDialog>
  </main>
</template>

<style scoped>
.talent-page {height:100%;max-width:none;min-height:460px;display:flex;flex-direction:column}
.talent-heading {flex:none;margin-bottom:9px}
.talent-board {display:flex;flex:none;gap:7px;padding:2px 2px 12px;overflow:auto;scrollbar-width:thin}
.board-stat {display:flex;align-items:center;gap:7px;min-height:37px;padding:5px 10px;white-space:nowrap;border:1px solid #dce5ec;border-radius:9px;background:white;color:#3e5265;cursor:pointer;font:inherit;font-size:12px;transition:background var(--rs-motion-fast),border-color var(--rs-motion-fast)}
.board-stat:hover,.board-stat[aria-pressed=true] {border-color:color-mix(in srgb,var(--stat-color) 55%,white);background:color-mix(in srgb,var(--stat-color) 8%,white)}
.board-stat i {width:7px;height:7px;border-radius:50%;background:var(--stat-color)}
.board-stat strong {font-variant-numeric:tabular-nums;color:#2c4256;font-size:14px}
.talent-workspace {flex:1;min-height:0;display:grid;grid-template-columns:minmax(220px,26%) minmax(0,1fr);overflow:hidden}
.talent-aside {min-width:0;min-height:0;display:grid;grid-template-rows:auto minmax(0,1fr) auto;border-right:1px solid #e0e7ee}
.talent-toolbar,.talent-pagination {display:flex;align-items:center;gap:8px;padding:12px;border-bottom:1px solid #e0e7ee}
.talent-toolbar>span,.talent-pagination>span {font-size:12px;color:var(--hire-muted)}
.talent-toolbar>.el-button,.talent-pagination>.el-pagination {margin-inline-start:auto}
.talent-pagination {border-bottom:0;border-top:1px solid #e0e7ee;padding:9px 10px}
.talent-list {display:grid;align-content:start;gap:8px;padding:10px;overflow:auto;background:#f5f7fb;scrollbar-width:thin}
.talent-detail {container:talent-detail / inline-size;min-width:0;min-height:0}
.talent-detail-grid {display:grid;grid-template-columns:minmax(265px,42%) minmax(0,1fr);height:100%;min-height:0}
.talent-context {min-width:0;min-height:0;display:flex;flex-direction:column;background:#f7f9fc}
.talent-context-header {display:grid;grid-template-columns:auto minmax(0,1fr);align-items:center;gap:9px 10px;padding:12px 14px;background:white;border-bottom:1px solid #e0e7ee}
.talent-initial {display:grid;place-items:center;width:40px;height:40px;border-radius:11px;background:#e9eff9;color:#37577f;font-size:13px;font-weight:700}
.talent-identity {display:flex;align-items:center;flex-wrap:wrap;gap:3px 9px;min-width:0}
.talent-identity h2 {font-size:17px;overflow-wrap:anywhere}
.talent-status {display:inline-block;width:fit-content;max-width:100%;overflow-wrap:anywhere;padding:2px 7px;border-radius:5px;background:color-mix(in srgb,var(--status-color) 9%,white);color:var(--status-color);font-size:11px;font-weight:650}
.talent-context-actions {grid-column:1/-1;display:flex;flex-wrap:wrap;gap:7px}
.talent-context-actions .el-button {margin:0}
.talent-context-scroll {flex:1;min-height:0;overflow:auto;padding:11px;scrollbar-width:thin}
.talent-block {padding:12px;border-radius:10px;background:white;border:1px solid #e0e7ee;margin-bottom:10px}
.talent-block h3 {font-size:14px;margin-bottom:9px}
.talent-process {background:linear-gradient(165deg,#fff,#f4f8fd);border-color:#dce6ef}
.talent-steps {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:5px;padding:0;margin:9px 0;list-style:none}
.talent-steps li {display:grid;grid-template-columns:19px minmax(0,1fr);align-items:center;gap:3px 5px;padding:7px 4px;border:1px solid #e1e7ef;border-radius:7px;color:#4a5e74;background:#fff}
.talent-steps li i {display:grid;place-items:center;width:19px;height:19px;border-radius:50%;font-style:normal;background:#edf1f7;font-size:10px}
.talent-steps li strong {font-size:12px;line-height:1.35}
.talent-steps li>span {grid-column:1/-1;font-size:10px;overflow-wrap:anywhere}
.talent-steps li[data-state=active] {border-color:#93add0;color:#2e5281;background:#eef4fc}
.talent-steps li[data-state=active] i {background:#436b9d;color:#fff}
.talent-steps li[data-state=done] i {background:#e3f1eb;color:#2b6c53}
.talent-rounds,.talent-muted {color:#566b7e;font-size:12px;line-height:1.55;margin-top:8px;overflow-wrap:anywhere}
.talent-rounds>span {font-weight:650;margin-right:6px}
.interview-list,.followup-list {display:grid;gap:7px;padding:0;margin:10px 0 0;list-style:none}
.interview-list li {display:flex;align-items:start;gap:7px;padding:8px;border:1px solid #e0e7ee;border-radius:7px;background:white;font-size:12px}
.interview-list svg {width:14px;height:14px;flex:none;color:#436b9d;margin-top:2px}
.interview-list time {font-weight:650;color:#31557d}
.interview-list p {margin-top:3px;white-space:pre-wrap;overflow-wrap:anywhere}
.talent-basics {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin:0}
.talent-basics dt {font-size:12px;color:var(--hire-muted)}
.talent-basics dd {margin:2px 0 0;font-size:13px;font-weight:650;overflow-wrap:anywhere}
.note-composer,.interview-form {display:grid;gap:8px}
.note-actions {display:flex;align-items:center;gap:6px}
.note-actions .el-select {flex:1;min-width:0}
.followup-list li {padding:8px;border:1px solid #e0e7ee;border-radius:7px;background:#fafbfd}
.followup-meta {display:flex;align-items:center;gap:6px;flex-wrap:wrap;font-size:12px}
.followup-meta time {font-size:10px;color:#596c7c}
.followup-list p {margin:5px 0;font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere}
.followup-delete {display:grid;place-items:center;width:23px;height:23px;margin-left:auto;border:0;border-radius:5px;color:#a32845;background:transparent;cursor:pointer}
.followup-delete:hover {background:#fae9ed}.followup-delete svg {width:15px;height:15px}
.talent-block>.el-button {margin-top:9px}
.talent-reader:not(.resume-fullscreen) {height:100%;min-height:0;border:0;border-left:1px solid #dfe7ed;border-radius:0;box-shadow:none}
.talent-empty {display:grid;align-content:center;justify-items:center;padding:16px;gap:8px;text-align:center;background:#f7f9fc;color:var(--hire-muted)}
.talent-empty svg {width:34px;height:34px;color:#537399}.talent-empty h2 {font-size:16px;color:var(--hire-ink)}.talent-empty p {font-size:13px}
.talent-compact-tabs,.talent-mobile-back {display:none}
@container talent-detail (max-width:600px) {
  .talent-detail-grid {grid-template-columns:minmax(0,1fr);grid-template-rows:auto minmax(0,1fr)}
  .talent-context {display:contents}
  .talent-compact-tabs {display:flex;grid-column:1/-1;gap:5px}
  .talent-compact-tabs button {padding:6px 9px;border:0;border-radius:6px;background:transparent;color:#4b6177;cursor:pointer;font:inherit;font-size:12px}
  .talent-compact-tabs button[aria-pressed=true] {background:#eaf0f8;color:#345b87;font-weight:650}
  .talent-detail-grid:not(.compact-preview) .talent-reader:not(.resume-fullscreen),.compact-preview .talent-context-scroll {display:none}
  .talent-context-scroll {background:#f7f9fc}
}
@media(max-width:710px) {
  .talent-workspace {grid-template-columns:minmax(0,1fr)}
  .talent-workspace .talent-detail,.talent-workspace .talent-empty,.talent-workspace.mobile-details .talent-aside {display:none}
  .talent-workspace.mobile-details .talent-detail {display:block}
  .talent-context-header {grid-template-columns:auto auto minmax(0,1fr)}
  .talent-mobile-back {display:grid;place-items:center;width:29px;height:29px;padding:6px;border:1px solid #dce5ef;border-radius:6px;background:white;color:#355b87;cursor:pointer}
}
</style>
