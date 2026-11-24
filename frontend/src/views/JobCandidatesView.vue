<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowBackOutline, DocumentTextOutline } from '@vicons/ionicons5'
import { ElButton, ElDialog, ElPagination, ElSelect, ElOption, ElTag, ElTooltip } from 'element-plus'
import { api } from '@/api/client'
import { jobStatusLabel, label, locationText, schoolTagLabels, type JobCandidatePage,
  type JobEvidenceReport, type JobEvidenceSource, type ResumeCard } from '@/api/hr'
import JobPostingDetails from '@/components/JobPostingDetails.vue'
import ResumeBook from '@/components/ResumeBook.vue'

type CandidateRow = JobCandidatePage['items'][number]
const route=useRoute()
const router=useRouter()
const data=ref<JobCandidatePage>()
const page=ref(1)
const pool=ref<'linked'|'unassigned'>('linked')
const size=ref(20)
const loading=ref(false)
const error=ref('')
const selectedId=ref<number>()
const reportVisible=ref(false)
const jobId=computed(()=>String(route.params.jobId || ''))
/* 头像按行加载：解析失败或无头像时回退为名字。 */
const avatars=ref(new Map<number,string>())
let avatarUrls:string[]=[]
function releaseAvatars() {for(const url of avatarUrls) URL.revokeObjectURL(url);avatarUrls=[];avatars.value=new Map()}
async function loadAvatars(items:CandidateRow[], seq:number) {
  const results=await Promise.allSettled(items.filter(item=>item.has_avatar && item.version_id)
    .map(async item=>[item.id,await api.get<Blob>('/hr/applications/'+item.id+'/avatar?version='+item.version_id)] as const))
  if(seq!==requestSequence) return
  const next=new Map<number,string>()
  for(const result of results) if(result.status==='fulfilled') next.set(result.value[0],URL.createObjectURL(result.value[1]))
  avatars.value=next;avatarUrls=[...next.values()]
}
let requestSequence=0
async function load() {
  const seq=++requestSequence
  error.value=''; loading.value=true
  try {
    const result=await api.get<JobCandidatePage>('/jobs/'+encodeURIComponent(jobId.value)+'/candidates?'+new URLSearchParams({page:String(page.value),size:String(size.value),ranking:'hybrid',pool:pool.value}))
    if(seq!==requestSequence) return
    data.value=result
    selectedId.value=undefined; releaseAvatars()
    void loadAvatars(result.items,seq)
  } catch (cause) {
    if(seq===requestSequence) {error.value=(cause as Error).message;data.value=undefined;releaseAvatars()}
  }
  finally {if(seq===requestSequence)loading.value=false}
}
watch([jobId,page,size,pool],([id],previous)=>{
  if(id!==previous?.[0]) {
    data.value=undefined
    if(page.value!==1 || pool.value!=='linked') {page.value=1;pool.value='linked';return}
  }
  void load()
},{immediate:true})
const selected=computed<CandidateRow|undefined>(()=>data.value?.items.find(item=>item.id===selectedId.value))
function degreeLine(tags:ResumeCard['tags']) {
  const degree=tags.find(tag=>tag.type==='degree')
  const tiers=tags.filter(tag=>tag.type==='school' && schoolTagLabels[tag.value]).map(tag=>schoolTagLabels[tag.value])
  const degreeText=degree?label(degree.value):''
  return tiers.length ? (degreeText?degreeText+'（'+tiers.join('/')+'）':tiers.join('/')) : degreeText
}
function experienceText(tags:ResumeCard['tags']) {
  const tag=tags.find(tag=>tag.type==='experience')
  const months=tag?Number(tag.value):NaN
  if(!Number.isFinite(months) || months<0) return ''
  if(months===0) return '应届生'
  if(months<12) return '1 年以内'
  return Math.floor(months/12)+' 年工作经验'
}
function selectCandidate(row:CandidateRow) { selectedId.value=row.id;reportVisible.value=true }
function toggleReport() {
  if(!reportVisible.value && !selected.value) selectedId.value=data.value?.items[0]?.id
  reportVisible.value=!reportVisible.value
}
function openProfile(id:number) {
  const query=pool.value==='linked' ? {job:jobId.value,candidate:String(id)} : {candidate:String(id)}
  void router.push({name:'applications',query})
}
function changePool(value:'linked'|'unassigned') {
  pool.value=value
  page.value=1
  selectedId.value=undefined
  reportVisible.value=false
}

const profileName=computed(()=>selected.value?.name || '申请 #'+(selected.value?.id ?? '—'))
const report=ref<JobEvidenceReport>()
const reportError=ref('')
const reportLoading=ref(false)
const previewVisible=ref(false)
const previewPage=ref(1)
let reportSequence=0

const retrievalLabels = {
  ranked:'有原文线索', no_evidence:'暂无相关证据', unindexed:'等待向量索引',
  needs_review:'门槛待核实', outside_requirements:'有门槛未满足',
}
const statusLabels = {
  related:'相关线索', supported:'有证据', unverified:'待核实', not_met:'未满足', not_checked:'待查验',
}
const evidenceKinds:Record<string,string> = {
  work:'工作经历', internship:'实习经历', project:'项目经历', campus_project:'校园项目',skill:'技能原文',
}
const methodLabels = {keyword:'原文词项', vector:'向量语义', structured:'结构化证据'}
const formatPoints=(value:number)=>value.toLocaleString('zh-CN',{maximumFractionDigits:2,minimumFractionDigits:2})
const blockIds=computed(()=>new Set(report.value?.evidence_blocks.map(block=>block.record_no)))
function showBlock(recordNo:number) {
  const element=document.getElementById('job-evidence-'+recordNo)
  element?.focus({preventScroll:true})
  element?.scrollIntoView({block:'nearest'})
}
function uniqueLocations(sources:JobEvidenceSource[]) {
  return [...new Map(sources.map(source=>[locationText(source.locator),source])).values()]
}
const reportGroups=computed(()=>[
  {label:'岗位职责',kinds:['duty','role']},
  {label:'必备与加分技能',kinds:['skill']},
  {label:'任职要求',kinds:['qualification','qualification_check']},
  {label:'已确认筛选门槛',kinds:['degree','months']},
].map(group=>({...group,items:report.value?.requirements.filter(item=>group.kinds.includes(item.kind)) || []}))
  .filter(group=>group.items.length))

async function loadReport() {
  const seq=++reportSequence
  const candidate=selected.value
  report.value=undefined
  reportError.value=''
  reportLoading.value=false
  if(!candidate || !reportVisible.value) return
  reportLoading.value=true
  try {
    const result=await api.get<JobEvidenceReport>('/jobs/'+encodeURIComponent(jobId.value)+'/candidates/'+candidate.id+'/evidence?'+new URLSearchParams({pool:pool.value}))
    if(seq!==reportSequence) return
    if(result.version_id!==candidate.version_id) {
      reportError.value='简历版本已更新，请刷新候选人列表。'
      return
    }
    report.value=result
  } catch(cause) {
    if(seq===reportSequence) reportError.value=(cause as Error).message
  } finally { if(seq===reportSequence) reportLoading.value=false }
}
watch([selected,reportVisible],()=>{previewVisible.value=false;void loadReport()})
function openEvidence(source:JobEvidenceSource) {
  previewPage.value=source.locator.page || 1
  previewVisible.value=true
}
onBeforeUnmount(()=>{requestSequence++;reportSequence++;releaseAvatars()})
</script>

<template>
  <main class="hire-page job-candidates-page">
    <header class="hire-heading job-candidates-heading">
      <div><h1>{{ data?.job.title || '岗位候选人' }}</h1><p v-if="data">{{ data.job.department_name }} · {{ data.job.category || '关联候选人' }}</p></div>
      <div class="job-heading-actions">
        <ElTag v-if="data" :type="data.job.status==='open'?'success':data.job.status==='paused'?'warning':'info'" effect="light">{{ jobStatusLabel(data.job.status) }}</ElTag>
        <ElTooltip content="返回岗位管理" placement="bottom"><ElButton circle aria-label="返回岗位管理" @click="router.push({name:'jobs'})"><ArrowBackOutline aria-hidden="true"/></ElButton></ElTooltip>
      </div>
    </header>
    <p v-if="error" class="hire-error" role="alert">{{ error }} <ElButton text type="primary" @click="load">重试</ElButton></p>
    <p v-else-if="!data" class="hire-muted" role="status">正在加载候选人…</p>
    <template v-if="data">
      <section class="hire-surface candidate-job-summary" aria-label="岗位要求概览">
        <div><span>{{ data.job.recruitment_window_is_reference ? '招聘期限 · 参考排期' : '招聘期限' }}</span><strong>{{ data.job.recruitment_start || '未设置' }} 至 {{ data.job.recruitment_end || '未设置' }}</strong></div>
        <div><span>必备技能</span><div class="job-tags"><ElTag v-for="tag in data.job.required_skills" :key="tag" effect="plain" size="small">{{ tag }}</ElTag><strong v-if="!data.job.required_skills.length">未设置</strong></div></div>
        <div><span>工作经验要求</span><div class="job-tags"><ElTag v-for="tag in data.job.experience_tags" :key="tag" effect="plain" size="small">{{ tag }}</ElTag><strong v-if="!data.job.experience_tags.length">未设置</strong></div></div>
      </section>
      <details v-if="data.job.jd || data.job.requirements" class="hire-surface candidate-posting">
        <summary>岗位职责、任职要求与来源</summary>
        <JobPostingDetails :job="data.job"/>
      </details>
      <section class="candidate-workbench" :class="{ 'report-collapsed':!reportVisible }" :aria-busy="loading">
        <section class="hire-surface candidate-job-list">
          <header class="candidates-title">
            <div>
              <h2>{{ pool==='linked' ? '关联简历' : '待关联简历' }} <span>{{ data.total }} 份</span></h2>
              <p v-if="data.retrieval?.source==='pending'">岗位索引待更新。</p>
              <p v-else>按匹配度从高到低排序 · 已索引 {{ data.retrieval?.indexed || 0 }} 份。</p>
              <p v-if="pool==='unassigned'">按本岗位试算匹配度，供关联岗位时参考。</p>
            </div>
            <div class="candidate-list-actions">
              <ElTooltip content="打开候选人工作台" placement="bottom"><ElButton circle aria-label="打开候选人工作台" :disabled="!selected" @click="selected && openProfile(selected.id)"><DocumentTextOutline aria-hidden="true"/></ElButton></ElTooltip>
              <button type="button" class="report-toggle" :aria-pressed="reportVisible"
                :title="reportVisible?'收起匹配报告':'展开匹配报告'" :aria-label="reportVisible?'收起匹配报告':'展开匹配报告'" @click="toggleReport">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true" focusable="false">
                  <rect x="3" y="4.5" width="18" height="15" rx="3"/>
                  <path v-if="!reportVisible" d="M9.5 4.5v15"/>
                  <path v-else d="M14.5 4.5v15"/>
                </svg>
              </button>
            </div>
          </header>
          <nav v-if="data.unassigned_total" class="candidate-pools" aria-label="简历范围">
            <button type="button" :aria-pressed="pool==='linked'" @click="changePool('linked')">关联简历</button>
            <button type="button" :aria-pressed="pool==='unassigned'" @click="changePool('unassigned')">待关联 {{ data.unassigned_total }}</button>
          </nav>
          <div class="candidate-grid-head" aria-hidden="true"><span>候选人</span><span>岗位匹配度</span></div>
          <div v-if="data.items.length" class="candidate-rows" role="list" aria-label="关联候选人列表">
            <article v-for="row in data.items" :key="row.id" class="candidate-row" :class="{selected:row.id===selected?.id}" role="listitem" tabindex="0" @click="selectCandidate(row)" @keydown.enter="selectCandidate(row)">
              <div class="candidate-person">
                <span class="candidate-avatar" aria-hidden="true"><img v-if="avatars.get(row.id)" :src="avatars.get(row.id)" alt=""><i v-else>{{ (row.name || '申').slice(0,2) }}</i></span>
                <div class="candidate-person-info">
                  <div class="candidate-name-line">
                    <button type="button" class="candidate-name" @click.stop="openProfile(row.id)">{{ row.name || '申请 #'+row.id }}</button>
                    <span v-if="degreeLine(row.tags)" class="degree-inline">{{ degreeLine(row.tags) }}</span>
                  </div>
                  <small>{{ [row.target_role || row.job_title, experienceText(row.tags)].filter(Boolean).join(' · ') || '—' }}</small>
                </div>
              </div>
              <div class="candidate-progress">
                <template v-if="row.match_score">
                  <strong class="candidate-score">{{ formatPoints(row.match_score.total) }}<span>%</span></strong>
                  <small v-if="row.match_score.missing_required_skills.length">待核实技能 {{ row.match_score.missing_required_skills.length }} 项</small>
                </template>
                <span v-else-if="row.retrieval_status">{{ retrievalLabels[row.retrieval_status] }}</span>
                <span v-else class="progress-empty">待评分</span>
              </div>
            </article>
          </div>
          <p v-else class="hire-empty">{{ loading ? '正在加载…' : pool==='linked' ? '该岗位暂未关联简历' : '该部门暂无待关联简历' }}</p>
          <footer class="candidate-job-pagination"><span>每页</span><ElSelect v-model="size" size="small" aria-label="每页候选人数"><ElOption v-for="option in [10,20,30,50]" :key="option" :label="option+' 条'" :value="option"/></ElSelect><ElPagination v-model:current-page="page" :page-size="size" :total="data.total" :pager-count="5" layout="total, prev, pager, next"/></footer>
        </section>
        <aside v-if="reportVisible && selected" class="hire-surface explain-report" aria-label="可解释匹配报告">
          <header class="report-title">
            <div><span class="report-eyebrow">可解释匹配报告</span><h2>{{ profileName }}</h2></div>
            <span v-if="report?.status" class="report-status">{{ retrievalLabels[report.status] }}</span>
          </header>
          <div class="report-scroll" :aria-busy="reportLoading">
            <p v-if="reportError" class="hire-error" role="alert">{{ reportError }} <ElButton text @click="loadReport">重试</ElButton></p>
            <p v-else-if="reportLoading" class="hire-muted" role="status">正在查找原文证据…</p>
            <template v-else-if="report">
              <section v-if="report.score" class="match-summary" aria-label="匹配得分">
                <div class="match-total"><span>岗位匹配度</span><strong>{{ formatPoints(report.score.total) }}%</strong></div>
                <dl>
                  <div><dt>职责分 · 权重 {{ report.score.duty_weight*100 }}%</dt><dd>{{ formatPoints(report.score.duty) }}</dd></div>
                  <div><dt>技能分 · 权重 {{ report.score.skill_weight*100 }}%</dt><dd>{{ formatPoints(report.score.skill) }}</dd></div>
                </dl>
                <p v-if="report.score.basis==='uncalibrated_positive_cosine'">职责分使用未校准的向量相似度。缺少可核实的技能证据计 0 分。</p>
                <p v-if="report.score.missing_required_skills.length">待核实的必备技能 · {{ report.score.missing_required_skills.join('、') }}</p>
              </section>
              <p v-if="report.source==='reference'" class="report-disclaimer">岗位要求来自招聘参考稿，待 HR 确认。</p>
              <p v-if="!report.requirements.length" class="hire-muted">当前暂无可核对的岗位证据。</p>
              <section v-for="group in reportGroups" :key="group.label" class="evidence-group">
                <h3>{{ group.label }}</h3>
                <article v-for="item in group.items" :key="item.id" class="evidence-item">
                  <div class="evidence-heading">
                    <p>{{ item.text }}</p>
                    <ElTag size="small" effect="plain" :type="item.status==='supported'?'success':item.status==='not_met'?'danger':item.status==='related'?'primary':'info'">
                      {{ statusLabels[item.status] }}
                    </ElTag>
                  </div>
                  <div class="evidence-meta">
                    <small v-if="item.method">{{ methodLabels[item.method] }}<template v-if="item.evidence_kind"> · {{ evidenceKinds[item.evidence_kind] }}</template><template v-if="item.matched_skill"> · {{ item.matched_skill }}</template></small>
                    <small v-if="item.points!=null">{{ formatPoints(item.points) }} / 100</small>
                    <button v-if="item.record_no!==null && blockIds.has(item.record_no)" type="button"
                      class="evidence-jump" @click="showBlock(item.record_no)">查看完整经历</button>
                  </div>
                </article>
              </section>
              <section v-if="report.evidence_blocks.length" class="evidence-group" aria-label="关联经历原文">
                <h3>关联经历原文</h3>
                <article v-for="block in report.evidence_blocks" :id="'job-evidence-'+block.record_no"
                  :key="block.record_no" class="evidence-item evidence-block" tabindex="-1">
                  <h4>{{ evidenceKinds[block.kind] || '简历原文' }}</h4>
                  <p>{{ block.text }}</p>
                  <div class="evidence-meta">
                    <template v-for="location in uniqueLocations(block.locations)" :key="location.span_no">
                      <ElButton v-if="selected.page_count && location.locator.page" link type="primary" @click="openEvidence(location)">打开{{ locationText(location.locator) }}</ElButton>
                      <small v-else>{{ locationText(location.locator) }}</small>
                    </template>
                  </div>
                </article>
              </section>
            </template>
          </div>
        </aside>
      </section>
      <ElDialog v-model="previewVisible" title="证据原文" width="min(840px, 96vw)" destroy-on-close>
        <ResumeBook v-if="selected && previewVisible" class="evidence-preview" :resume="selected" :initial-page="previewPage" embedded/>
      </ElDialog>
    </template>
  </main>
</template>

<style scoped>
.job-candidates-heading { margin-bottom:9px; }
.job-heading-actions,.candidate-list-actions { display:flex;align-items:center;gap:8px; }
.job-heading-actions :deep(.el-button),.candidate-list-actions :deep(.el-button),.report-toggle { width:31px;height:31px;padding:0; }
.job-heading-actions :deep(svg),.candidate-list-actions :deep(svg) { width:16px;height:16px; }
.report-toggle { display:grid;place-items:center;border:1px solid var(--hire-line);border-radius:50%;background:var(--hire-paper);color:#54707f;cursor:pointer; }
.report-toggle:hover,.report-toggle[aria-pressed=true] { border-color:var(--hire-accent);background:var(--hire-soft);color:var(--hire-accent-dark); }
.candidate-job-summary { display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:15px;padding:15px 17px;margin-bottom:12px; }
.candidate-job-summary>div { display:grid;gap:6px;align-content:start; }
.candidate-job-summary span { color:var(--hire-muted);font-size:12px; }
.candidate-job-summary strong { font-size:13px; }
.job-tags { display:flex;gap:5px;flex-wrap:wrap; }
.candidate-posting { margin-bottom:14px;padding:12px 16px; }
.candidate-posting>summary { cursor:pointer;color:var(--hire-accent-dark);font-weight:650; }
.candidate-posting[open]>summary { margin-bottom:12px; }
.candidate-workbench { display:grid;grid-template-columns:minmax(0,1fr) clamp(370px,35vw,500px);gap:16px;align-items:start; }
.candidate-workbench.report-collapsed { grid-template-columns:minmax(0,1fr); }
.candidate-job-list { overflow:hidden; }
.candidates-title { display:flex;justify-content:space-between;align-items:center;gap:14px;padding:14px 17px;border-bottom:1px solid var(--hire-line); }
.candidates-title h2 { font-size:16px; }
.candidates-title h2 span,.candidates-title p { color:var(--hire-muted);font-size:12px;font-weight:400; }
.candidates-title p { margin-top:5px; }
.candidate-grid-head,.candidate-row { display:grid;grid-template-columns:minmax(0,1fr) minmax(94px,.35fr);gap:10px;align-items:center; }
.candidate-grid-head { padding:9px 17px;background:#f7f9fa;color:var(--hire-muted);font-size:12px; }
.candidate-row { padding:12px 17px;border-bottom:1px solid #edf1f3;background:white;cursor:pointer; }
.candidate-row:hover,.candidate-row:focus-visible { background:#f5fbf9; }
.candidate-row.selected { background:#eff8f6;box-shadow:inset 3px 0 var(--hire-accent); }
.candidate-person,.candidate-name-line { display:flex;align-items:center;gap:7px;min-width:0;flex-wrap:wrap; }
.candidate-avatar { display:grid;place-items:center;width:38px;height:38px;flex:none;border-radius:50%;overflow:hidden;background:var(--hire-soft);color:var(--hire-accent-dark);font-size:13px; }
.candidate-avatar img { width:100%;height:100%;object-fit:cover; }
.candidate-name { border:0;padding:0;background:none;color:var(--hire-ink);font-weight:650;cursor:pointer; }
.candidate-name:hover { color:var(--hire-accent-dark); }
.degree-inline { padding:2px 7px;border-radius:20px;background:#f0f7f5;color:var(--hire-accent-dark);font-size:11px; }
.candidate-person-info small,.candidate-progress small { display:block;margin-top:3px;color:var(--hire-muted);font-size:11px; }
.candidate-progress { font-size:12px; }
.progress-empty { color:var(--hire-muted); }
.candidate-job-pagination { display:flex;align-items:center;justify-content:flex-end;gap:7px;padding:11px 15px;font-size:12px;color:var(--hire-muted); }
.candidate-job-pagination :deep(.el-select) { width:76px; }
.explain-report { position:sticky;top:12px;display:flex;flex-direction:column;max-height:calc(100dvh - var(--rs-header-height) - 24px);overflow:hidden; }
.report-title { display:flex;justify-content:space-between;gap:9px;padding:14px 16px;border-bottom:1px solid var(--hire-line); }
.report-eyebrow,.report-status { color:var(--hire-accent-dark);font-size:12px; }
.report-title h2 { margin-top:2px;font-size:16px; }
.report-scroll { overflow:auto;padding:14px 16px 17px; }
.report-disclaimer { margin-bottom:12px;padding:8px 10px;border-radius:8px;background:var(--hire-soft);font-size:12px;color:var(--hire-muted); }
.evidence-group { display:grid;gap:7px;margin-bottom:14px; }
.evidence-group h3 { font-size:14px; }
.evidence-item { display:grid;gap:4px;padding:9px 10px;border:1px solid var(--hire-line);border-radius:8px;font-size:13px;line-height:1.55; }
.evidence-heading { display:flex;align-items:flex-start;justify-content:space-between;gap:8px; }
.evidence-heading p { margin:0; }
.evidence-item small { color:var(--hire-muted); }
.candidate-pools { display:flex;gap:6px;padding:7px 14px; }
.candidate-pools button,.evidence-jump { background:white;border:1px solid var(--hire-line);border-radius:6px;padding:4px 8px;color:var(--hire-accent-dark);font:inherit;cursor:pointer; }
.candidate-pools button[aria-pressed=true] { background:var(--hire-soft);border-color:var(--hire-accent); }
.candidate-score { color:var(--hire-accent-dark);font-size:18px;font-variant-numeric:tabular-nums; }
.candidate-score span { margin-left:2px;font-size:12px; }
.match-summary { margin-bottom:12px;padding:10px 12px;border:1px solid var(--hire-line);border-radius:8px;background:var(--hire-soft); }
.match-summary p { margin:5px 0 0;color:var(--hire-muted);font-size:12px; }
.match-total,.match-summary dl>div { display:flex;justify-content:space-between;align-items:baseline;gap:8px; }
.match-total strong { color:var(--hire-accent-dark);font-size:24px;font-variant-numeric:tabular-nums; }
.match-summary dl { margin:4px 0;font-size:12px; }
.match-summary dd { margin:0;font-weight:650; }
.evidence-meta { display:flex;align-items:center;gap:8px;flex-wrap:wrap; }
.evidence-jump { margin-left:auto;font-size:11px; }
.evidence-block { scroll-margin:8px; }
.evidence-block:focus { outline:2px solid var(--hire-accent);outline-offset:1px; }
.evidence-block h4,.evidence-block p { margin:0; }
.evidence-block p { white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.7; }
.evidence-preview:not(.resume-fullscreen) { height:min(66vh,700px); }
@media(max-width:930px) {
  .candidate-workbench,.candidate-workbench.report-collapsed { grid-template-columns:1fr; }
  .explain-report { position:static;max-height:none; }
}
@media(max-width:560px) {
  .candidate-job-summary { grid-template-columns:1fr;gap:11px; }
  .candidate-job-pagination { justify-content:flex-start;flex-wrap:wrap; }
}
</style>
