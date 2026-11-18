<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { BriefcaseOutline, FolderOpenOutline, SearchOutline, SettingsOutline, AddOutline, CloudUploadOutline, EllipsisHorizontal } from '@vicons/ionicons5'
import { ElAlert, ElButton, ElDialog, ElDrawer, ElDropdown, ElDropdownMenu, ElDropdownItem, ElInput, ElSelect, ElOption, ElTable, ElTableColumn, ElTag, ElPagination, ElPopover, ElCheckbox, ElMessage } from 'element-plus'
import { api } from '@/api/client'
import { dateLabel, jobStatusLabel, jobStatusOptions, type Job } from '@/api/hr'
import { jobDirectory, type JobDirectory, type JobImportStatus } from '@/api/recruiting'
import FilePickerZone from '@/components/FilePickerZone.vue'
import { useAuthStore } from '@/stores/auth'

type Node = { key:string; title:string; kind:'company'|'department'|'job'; departmentId?:number; job?:Job; children?:Node[] }
const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const directory = ref<JobDirectory>({items:[],departments:[],companies:[]})
const search = ref('')
const status = ref('')
const kind = ref('')
const processType = ref('')
const page = ref(1)
const pageSize = ref(12)
const loadError = ref('')
const editorError = ref('')
const loading = ref(false)
const saving = ref(false)
const drawer = ref(false)
const drawerMode = ref<'view'|'edit'|'create'>('create')
const active = ref<Job>()
const roundName = ref('')
const experienceOptions=['在校生','应届生','1年以内','1–3年','3–5年','5–10年','10年以上','不限']
const types:Record<string,string> = {social:'社会招聘',campus:'校园招聘',internship:'实习招聘',internal:'内部招聘'}
const importing = ref(false)
const selectedFile = ref<File>()
const jobFile = ref<HTMLInputElement>()
const importBusy = ref(false)
const importError = ref('')
const imported = ref(false)
const importWarnings = ref<string[]>([])
let importSequence = 0
let importTimer: ReturnType<typeof setTimeout> | undefined

const columns = ref([
  { id:'department', label:'所属部门', min:100, visible:true },
  { id:'headcount', label:'计划人数', min:90, visible:true },
  { id:'status', label:'招聘状态', min:100, visible:true },
  { id:'flow', label:'招聘流程', min:132, visible:true },
  { id:'owner', label:'负责人', min:88, visible:true },
  { id:'resumes', label:'简历', min:80, visible:true },
  { id:'updated', label:'更新时间', min:155, visible:true },
])
const prefs = localStorage.getItem('recruiting-job-columns')
if (prefs) {
  try {
    const saved = JSON.parse(prefs) as {id:string;visible:boolean}[]
    const ordered = saved.flatMap(preference => {
      const column = columns.value.find(item=>item.id===preference.id)
      return column ? [{...column,visible:preference.visible!==false}] : []
    })
    columns.value = [...ordered,...columns.value.filter(column=>!ordered.some(item=>item.id===column.id))]
  } catch { /* Use the default columns if the preference is invalid. */ }
}
watch(columns,value=>localStorage.setItem('recruiting-job-columns',JSON.stringify(value)),{deep:true})
function moveColumn(index:number,direction:number) {
  const target=index+direction
  if(target<0 || target>=columns.value.length) return
  const [column]=columns.value.splice(index,1)
  columns.value.splice(target,0,column)
}

const filtered = computed(()=>directory.value.items.filter(job=>{
  const term=search.value.trim().toLocaleLowerCase()
  return (!status.value || job.status===status.value) && (!kind.value || job.recruitment_type===kind.value)
    && (!processType.value || (processType.value==='interview')===Boolean(job.rounds.length))
    && (!term || [job.title,job.department_name,job.recruiter_name].some(value=>value.toLocaleLowerCase().includes(term)))
    && (!route.query.department || String(job.department_id)===route.query.department)
    && (!route.query.job || String(job.id)===route.query.job)
}))
watch([search,status,kind,processType,pageSize,()=>route.query],()=>{page.value=1})
watch(()=>filtered.value.length,total=>{page.value=Math.min(page.value,Math.max(1,Math.ceil(total/pageSize.value)))})
const board = computed<Node[]>(()=>{
  const rows=filtered.value.slice((page.value-1)*pageSize.value,page.value*pageSize.value)
  const toNode=(job:Job):Node=>({key:'job-'+job.id,title:job.title,kind:'job',job})
  if(!auth.isSuperAdmin) return rows.map(toNode)
  const companies=new Map<number,Node>()
  const departments=new Map<number,Node>()
  for(const job of rows) {
    let company=companies.get(job.company_id)
    if(!company) {
      company={key:'company-'+job.company_id,title:job.company_name,kind:'company',children:[]}
      companies.set(job.company_id,company)
    }
    let department=departments.get(job.department_id)
    if(!department) {
      department={key:'department-'+job.department_id,title:job.department_name,kind:'department',departmentId:job.department_id,children:[]}
      departments.set(job.department_id,department)
      company.children?.push(department)
    }
    department.children?.push(toNode(job))
  }
  return [...companies.values()]
})
const counts = computed(()=>[
  { title:'全部岗位', value:directory.value.items.length, filter:'' },
  { title:'进行中', value:directory.value.items.filter(job=>job.status==='open').length, filter:'open' },
  { title:'未开始', value:directory.value.items.filter(job=>job.status==='paused').length, filter:'paused' },
  { title:'已结束', value:directory.value.items.filter(job=>job.status==='closed').length, filter:'closed' },
])
const form = reactive({
  title:'', jd:'', status:'paused', department_id:undefined as number|undefined,
  recruitment_type:'social', recruiter_name:'', planned_headcount:null as number|null,
  target_date:'', requirements:'', required_skills:[] as string[], experience_tags:[] as string[],
  recruitment_start:'', recruitment_end:'', rounds:[] as {id?:number;name:string}[],
})
const incomplete = computed(()=>[
  !form.title.trim() && '岗位名称', !form.department_id && '所属部门',
  !form.recruitment_type && '岗位属性', !form.planned_headcount && '计划招聘人数',
  !form.recruiter_name.trim() && '负责人', !form.target_date && '目标日期',
  !form.jd.trim() && '岗位描述', !form.requirements.trim() && '任职要求',
  !form.required_skills.length && '必备技能', !form.experience_tags.length && '经验要求',
  (!form.recruitment_start || !form.recruitment_end) && '招聘期限', !form.rounds.length && '面试轮次',
].filter(Boolean))
async function load() {
  loading.value=true
  try { directory.value=await jobDirectory();loadError.value='' }
  catch(error){loadError.value=(error as Error).message}
  finally{loading.value=false}
}
onMounted(load)
function openJob(mode:typeof drawerMode.value,job?:Job,copy=false) {
  drawerMode.value=mode
  active.value=job
  editorError.value=''
  imported.value=false
  importWarnings.value=[]
  roundName.value=''
  Object.assign(form,{
    title:copy ? (job?.title+'（副本）').slice(0,120) : job?.title || '',
    jd:job?.jd || '', status:copy ? 'paused' : job?.status || 'paused',
    department_id:job?.department_id || directory.value.departments[0]?.id,
    recruitment_type:job?.recruitment_type || 'social', recruiter_name:job?.recruiter_name || '',
    target_date:job?.target_date || '', requirements:job?.requirements || '',
    required_skills:[...(job?.required_skills ?? [])],experience_tags:[...(job?.experience_tags ?? [])],
    recruitment_start:job?.recruitment_start || '',recruitment_end:job?.recruitment_end || '', planned_headcount:job?.planned_headcount ?? null,
    rounds:job?.rounds.map(r=>copy?{name:r.name}:{id:r.id,name:r.name}) ?? [],
  })
  drawer.value=true
}
async function saveJob() {
  if(!form.department_id || !form.title.trim()) {editorError.value='请填写岗位名称并选择所属部门。';return}
  if(!form.recruitment_type) {editorError.value='请选择岗位属性。';return}
  if(form.recruitment_start && form.recruitment_end && form.recruitment_start>form.recruitment_end) {editorError.value='招聘截止日期不能早于开始日期。';return}
  saving.value=true
  editorError.value=''
  try {
    const payload={...form,target_date:form.target_date || null,recruitment_start:form.recruitment_start || null,
      recruitment_end:form.recruitment_end || null,planned_headcount:form.planned_headcount || null}
    if(drawerMode.value==='edit' && active.value) await api.put('/hr/jobs/'+active.value.id,payload)
    else await api.post('/hr/jobs',payload)
    drawer.value=false
    ElMessage.success('岗位已保存')
    await load()
  } catch(error){editorError.value=(error as Error).message}
  finally{saving.value=false}
}
function clearImport() {
  ++importSequence
  clearTimeout(importTimer)
  selectedFile.value=undefined
  importBusy.value=false
  importError.value=''
}
watch(importing,value=>{if(!value) clearImport()})
onBeforeUnmount(clearImport)
function selectJobFile(files:File[]) {
  if(!files.length || importBusy.value) return
  importError.value=''
  selectedFile.value=undefined
  if(files.length!==1) {importError.value='每次只能导入一份岗位文件。';return}
  const file=files[0]
  if(!/\.(pdf|docx?|png|jpe?g|webp|txt)$/i.test(file.name) || !file.size || file.size>5*1024*1024){
    importError.value='请选择支持的格式，文件不能为空或超过 5 MB。';return
  }
  selectedFile.value=file
}
function fileChanged(input:HTMLInputElement) {
  selectJobFile(Array.from(input.files ?? []))
  input.value=''
}
async function uploadJob() {
  if(!selectedFile.value || importBusy.value) return
  const attempt=++importSequence
  const began=Date.now()
  const path='/workspace/job-imports'
  const data=new FormData()
  data.append('file',selectedFile.value)
  importBusy.value=true
  importError.value=''
  const fail=(error:unknown)=>{
    if(attempt!==importSequence) return
    importError.value=(error as Error).message
    importBusy.value=false
  }
  async function check(id:string) {
    if(attempt!==importSequence) return
    try {
      const result=await api.get<JobImportStatus>(path+'/'+id)
      if(attempt!==importSequence) return
      if(result.status==='failed') throw new Error(result.error)
      if(result.status==='pending') {
        if(Date.now()-began>225000) throw new Error('尚未收到解析结果，请稍后重试或手动新建。')
        importTimer=setTimeout(()=>void check(id),1500)
        return
      }
      const {draft}=result
      const named=draft.department_name
      const department = directory.value.departments.find(row=>row.name===named || row.company_name+' / '+row.name===named)
      importing.value=false
      openJob('create')
      imported.value=true
      importWarnings.value=[...draft.warnings,...(named && !department?['未在可管理的部门中找到「'+named+'」，请选择归属部门。']:[])]
      Object.assign(form,{
        title:draft.title,department_id:department?.id,jd:draft.jd,recruitment_type:draft.recruitment_type || '',
        recruiter_name:draft.recruiter_name,planned_headcount:draft.planned_headcount,
        target_date:draft.target_date || '',requirements:draft.requirements || '',
        required_skills:draft.required_skills || [],experience_tags:draft.experience_tags || [],
        recruitment_start:draft.recruitment_start || '',recruitment_end:draft.recruitment_end || '',rounds:draft.rounds.map(name=>({name})),
      })
    } catch(error){fail(error)}
  }
  try {
    const start=await api.upload<{id:string}>(path,data)
    if(attempt===importSequence) await check(start.id)
  } catch(error){fail(error)}
}
function addRound() {
  const name=roundName.value.trim()
  if(!name || form.rounds.length>=12) return
  const final=form.rounds.findIndex(round=>round.name==='终面')
  form.rounds.splice(final<0?form.rounds.length:final,0,{name})
  roundName.value=''
}
async function command(action:string,job:Job) {
  if(action==='view') {openJob('view',job);return}
  if(action==='edit') {openJob('edit',job);return}
  if(action==='copy') {openJob('create',job,true);return}
  try {
    await api.put('/hr/jobs/'+job.id,{title:job.title,department_id:job.department_id,jd:job.jd || '',status:action})
    ElMessage.success('招聘状态已更新')
    await load()
  } catch(error){ElMessage.error((error as Error).message)}
}
function dateText(value:string) {
  if(!value) return '—'
  return dateLabel(value).slice(0,16)
}
</script>

<template>
  <main class="hire-page jobs-page">
    <header class="hire-heading">
      <div><h1>岗位管理</h1><p>管理招聘岗位、所属组织与招聘计划</p></div>
      <div class="hire-toolbar">
        <ElButton :icon="CloudUploadOutline" @click="importing=true">导入岗位</ElButton>
        <ElButton type="primary" :icon="AddOutline" @click="openJob('create')">新建岗位</ElButton>
      </div>
    </header>

    <div class="job-metrics hire-surface" aria-label="按招聘状态查看岗位">
      <button v-for="metric in counts" :key="metric.filter" type="button" :aria-pressed="status===metric.filter"
        @click="status=metric.filter">
        <span>{{ metric.title }}</span><strong>{{ metric.value }}</strong>
      </button>
    </div>

    <p v-if="loadError" class="hire-error" role="alert">{{ loadError }} <button class="hire-inline-button" @click="load">重试</button></p>
    <section class="hire-surface job-board" aria-label="招聘岗位">
      <div class="hire-toolbar job-toolbar">
        <ElSelect v-model="processType" placeholder="招聘流程" clearable aria-label="招聘流程">
          <ElOption label="含面试流程" value="interview"/><ElOption label="不含面试流程" value="none"/>
        </ElSelect>
        <ElSelect v-model="kind" placeholder="岗位属性" clearable aria-label="岗位属性">
          <ElOption v-for="(name,code) in types" :key="code" :label="name" :value="code"/>
        </ElSelect>
        <ElSelect v-model="status" placeholder="招聘状态" clearable aria-label="招聘状态">
          <ElOption v-for="option in jobStatusOptions" :key="option.value" :label="option.label" :value="option.value"/>
        </ElSelect>
        <ElInput v-model="search" :prefix-icon="SearchOutline" clearable placeholder="岗位、部门或负责人" aria-label="查找岗位"/>
        <RouterLink v-if="route.query.job || route.query.department" class="hire-inline-button" to="/jobs">查看全部</RouterLink>
        <ElPopover placement="bottom-end" trigger="click" :width="250">
          <template #reference><ElButton class="column-trigger" :icon="SettingsOutline">表头设置</ElButton></template>
          <div v-for="(column,index) in columns" :key="column.id" class="column-edit">
            <ElCheckbox v-model="column.visible">{{ column.label }}</ElCheckbox>
            <button type="button" class="hire-inline-button" :aria-label="'上移'+column.label" :disabled="index===0" @click="moveColumn(index,-1)">↑</button>
            <button type="button" class="hire-inline-button" :aria-label="'下移'+column.label" :disabled="index===columns.length-1" @click="moveColumn(index,1)">↓</button>
          </div>
        </ElPopover>
      </div>
      <ElTable :data="board" row-key="key" size="default" default-expand-all
        :row-class-name="({row}:{row:Node})=>row.kind==='job'?'job-row':'folder-row '+row.kind"
        :empty-text="loading?'正在加载岗位…':'没有符合条件的岗位'" @row-click="(row:Node)=>row.job && router.push({name:'job-candidates',params:{jobId:row.job.id}})">
        <ElTableColumn prop="title" label="岗位名称" min-width="220">
          <template #default="{row}">
            <RouterLink v-if="row.job" class="job-title hire-inline-button"
              :to="{name:'job-candidates',params:{jobId:row.job.id}}" @click.stop>{{ row.title }}</RouterLink>
            <span v-else class="folder-title"><FolderOpenOutline/>{{ row.title }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn v-for="column in columns.filter(item=>item.visible)" :key="column.id" :label="column.label" :min-width="column.min">
          <template #default="{row}">
            <template v-if="row.job">
              <span v-if="column.id==='department'">{{ row.job.department_name }}</span>
              <span v-else-if="column.id==='headcount'">{{ row.job.planned_headcount===null ? '—' : row.job.planned_headcount+' 人' }}</span>
              <ElTag v-else-if="column.id==='status'" effect="light" :type="row.job.status==='open'?'success':row.job.status==='paused'?'warning':'info'">
                {{ jobStatusLabel(row.job.status) }}
              </ElTag>
              <span v-else-if="column.id==='flow'" class="flow-text">{{ row.job.rounds.map((r:{name:string})=>r.name).join(' · ') || '无面试流程' }}</span>
              <span v-else-if="column.id==='owner'">{{ row.job.recruiter_name || '—' }}</span>
              <span v-else-if="column.id==='updated'" class="job-numbers">{{ dateText(row.job.updated_at) }}</span>
              <RouterLink v-else-if="column.id==='resumes'"  :to="{name:'job-candidates',params:{jobId:row.job.id}}"
                class="hire-inline-button" @click.stop>{{ row.job.application_count }} 份</RouterLink>
            </template>
            <RouterLink v-else-if="column.id==='resumes' && row.departmentId" :to="{name:'applications',query:{department:row.departmentId}}"
              class="hire-inline-button" @click.stop>查看简历</RouterLink>
          </template>
        </ElTableColumn>
        <ElTableColumn label="操作" width="90" align="center">
          <template #default="{row}">
            <div v-if="row.job?.manageable" @click.stop>
              <ElDropdown trigger="click" @command="command($event,row.job)">
                <ElButton aria-label="更多岗位操作" :icon="EllipsisHorizontal" text/>
                <template #dropdown><ElDropdownMenu>
                  <ElDropdownItem command="view">查看岗位资料</ElDropdownItem>
                  <ElDropdownItem command="edit">编辑岗位</ElDropdownItem>
                  <ElDropdownItem command="copy">复制岗位</ElDropdownItem>
                  <ElDropdownItem v-if="row.job.status!=='open'" command="open">开始招聘</ElDropdownItem>
                  <ElDropdownItem v-if="row.job.status==='open'" command="paused">暂停招聘</ElDropdownItem>
                  <ElDropdownItem v-if="row.job.status!=='closed'" command="closed" divided>结束招聘</ElDropdownItem>
                </ElDropdownMenu></template>
              </ElDropdown>
            </div>
            <RouterLink v-else-if="auth.isSuperAdmin && row.departmentId" class="hire-inline-button"
              :to="{name:'users',query:{department:row.departmentId}}" :aria-label="row.title+'账号'" @click.stop>账号</RouterLink>
          </template>
        </ElTableColumn>
      </ElTable>
      <footer class="job-footer">
        <span class="hire-muted">共 {{ filtered.length }} 个岗位</span>
        <ElPagination v-model:current-page="page" v-model:page-size="pageSize" :page-sizes="[12,24,48]"
          :total="filtered.length" layout="sizes, prev, pager, next" :pager-count="5" background small/>
      </footer>
    </section>

    <ElDrawer v-model="drawer" class="hire-drawer job-drawer" :size="'min(580px, 100vw)'"
      :title="drawerMode==='view'?'岗位详情':drawerMode==='edit'?'编辑岗位':'新建岗位'">
      <template v-if="drawerMode==='view' && active">
        <div class="job-detail-title"><BriefcaseOutline/><div><h2>{{ active.title }}</h2>
          <p class="hire-muted">{{ active.company_name }} / {{ active.department_name }} · {{ types[active.recruitment_type] }}</p></div></div>
        <dl class="job-facts"><div><dt>状态</dt><dd>{{ jobStatusLabel(active.status) }}</dd></div>
          <div><dt>负责人</dt><dd>{{ active.recruiter_name || '未填写' }}</dd></div>
          <div><dt>招聘计划</dt><dd>{{ active.planned_headcount ?? '未设置' }}</dd></div>
          <div><dt>更新时间</dt><dd>{{ dateText(active.updated_at) }}</dd></div>
          <div><dt>招聘期限</dt><dd>{{ active.recruitment_start || '未设置' }} 至 {{ active.recruitment_end || '未设置' }}</dd></div>
          <div><dt>计划完成日期</dt><dd>{{ active.target_date || '未设置' }}</dd></div>
          <div><dt>面试流程</dt><dd>{{ active.rounds.map(round=>round.name).join(' → ') || '无面试流程' }}</dd></div></dl>
        <h3>岗位描述</h3><p class="job-description">{{ active.jd || '尚未填写岗位描述。' }}</p>
        <h3 class="job-requirement-heading">任职要求</h3><p class="job-description">{{ active.requirements || '尚未填写任职要求。' }}</p>
        <div class="job-tag-details">
          <strong>必备技能</strong><ElTag v-for="tag in active.required_skills" :key="tag" effect="plain">{{ tag }}</ElTag>
          <small v-if="!active.required_skills?.length">未设置</small>
        </div>
        <div class="job-tag-details">
          <strong>工作经验</strong><ElTag v-for="tag in active.experience_tags" :key="tag" effect="plain">{{ tag }}</ElTag>
          <small v-if="!active.experience_tags?.length">未设置</small>
        </div>
      </template>
      <form v-else id="job-workspace-editor" class="job-editor" @submit.prevent="saveJob">
        <p v-if="editorError" class="hire-error" role="alert">{{ editorError }}</p>
        <ElAlert v-if="imported" class="wide" title="导入草稿 · 保存后才会新增岗位" type="info" :closable="false" show-icon>
          <p>请与原文件核对。每个岗位只能选择一个所属部门。</p>
          <p v-if="incomplete.length">待补充：{{ incomplete.join('、') }}</p>
          <p v-for="warning in importWarnings" :key="warning">{{ warning }}</p>
        </ElAlert>
        <label class="wide">岗位名称<ElInput v-model="form.title" maxlength="120" required/></label>
        <label>所属部门<ElSelect v-model="form.department_id" :disabled="drawerMode==='edit'" aria-label="所属部门">
          <ElOption v-for="department in directory.departments" :key="department.id" :value="department.id" :label="department.company_name+' / '+department.name"/>
        </ElSelect></label>
        <label>岗位属性<ElSelect v-model="form.recruitment_type" aria-label="岗位属性">
          <ElOption v-for="(title,code) in types" :key="code" :value="code" :label="title"/>
        </ElSelect></label>
        <label>招聘状态<ElSelect v-model="form.status" aria-label="招聘状态">
          <ElOption v-for="item in jobStatusOptions" :key="item.value" :value="item.value" :label="item.label"/>
        </ElSelect></label>
        <label>负责人<ElInput v-model="form.recruiter_name" maxlength="80"/></label>
        <label>计划招聘人数<input v-model.number="form.planned_headcount" type="number" min="1" max="100000"></label>
        <label>计划完成日期<input v-model="form.target_date" type="date"></label>
        <label>招聘开始日期<input v-model="form.recruitment_start" type="date" :max="form.recruitment_end || undefined"></label>
        <label>招聘截止日期<input v-model="form.recruitment_end" type="date" :min="form.recruitment_start || undefined"></label>
        <label class="wide">岗位描述<ElInput v-model="form.jd" type="textarea" :rows="4" maxlength="4000" placeholder="填写日常职责、工作范围及目标"/></label>
        <label class="wide">任职要求<ElInput v-model="form.requirements" type="textarea" :rows="4" maxlength="4000" placeholder="填写专业背景、资格、能力及其他任职条件"/></label>
        <label class="wide">必备技能
          <ElSelect v-model="form.required_skills" multiple filterable allow-create default-first-option :reserve-keyword="false"
            :multiple-limit="20" aria-label="必备技能" placeholder="输入技能名称后按回车添加"/>
        </label>
        <label class="wide">工作经验要求
          <ElSelect v-model="form.experience_tags" multiple filterable allow-create default-first-option :reserve-keyword="false"
            :multiple-limit="12" aria-label="工作经验要求" placeholder="选择标签，或输入后按回车自定义">
            <ElOption v-for="value in experienceOptions" :key="value" :label="value" :value="value"/>
          </ElSelect>
        </label>
        <div class="wide"><h3>面试流程</h3>
          <div v-for="(round,index) in form.rounds" :key="round.id ?? index" class="round-edit">
            <span>{{ index+1 }}</span><ElInput v-model="round.name" maxlength="40" :aria-label="'第 '+(index+1)+' 轮'"/>
            <ElButton text @click="form.rounds.splice(index,1)">移除</ElButton>
          </div>
          <div v-if="form.rounds.length<12" class="round-edit">
            <ElInput v-model="roundName" maxlength="40" placeholder="新增轮次名称" @keydown.enter.prevent="addRound"/>
            <ElButton @click="addRound">添加轮次</ElButton>
          </div>
        </div>
      </form>
      <template #footer>
        <ElButton @click="drawer=false">关闭</ElButton>
        <template v-if="drawerMode==='view' && active">
          <RouterLink class="el-button"  :to="{name:'job-candidates',params:{jobId:active.id}}">查看简历</RouterLink>
          <ElButton v-if="active.manageable" type="primary" @click="openJob('edit',active)">编辑岗位</ElButton>
        </template>
        <ElButton v-else type="primary" native-type="submit" form="job-workspace-editor" :loading="saving">保存岗位</ElButton>
      </template>
    </ElDrawer>

    <ElDialog v-model="importing" width="min(640px, 95vw)" title="导入单个岗位"
      append-to-body align-center destroy-on-close :close-on-click-modal="false">
      <form id="job-import-form" class="job-import-form" @submit.prevent="uploadJob">
        <p>上传一份岗位说明，在草稿中核对并补充解析字段。</p>
        <FilePickerZone :title="selectedFile?'更换岗位文件':'选择或拖入一份岗位文件'"
          hint="PDF、Word、图片、TXT，单份不超过 5 MB" :disabled="importBusy"
          @browse="jobFile?.click()" @drop="selectJobFile"/>
        <input id="job-file" ref="jobFile" type="file" accept=".pdf,.doc,.docx,.png,.jpg,.jpeg,.webp,.txt" hidden
          @change="fileChanged($event.target as HTMLInputElement)">
        <div v-if="selectedFile" class="job-selected-file">
          <strong :title="selectedFile.name">{{ selectedFile.name }}</strong>
          <ElTag size="small" effect="light" :type="importBusy?'warning':'info'" role="status">{{ importBusy?'正在解析':'待解析' }}</ElTag>
        </div>
        <p v-if="importError" class="hire-error" role="alert">{{ importError }}</p>
      </form>
      <template #footer>
        <ElButton @click="importing=false">关闭</ElButton>
        <ElButton type="primary" :icon="CloudUploadOutline" native-type="submit" form="job-import-form"
          :loading="importBusy" :disabled="!selectedFile">解析文件</ElButton>
      </template>
    </ElDialog>

  </main>
</template>

<style scoped>
.job-metrics {display:grid;grid-template-columns:repeat(4,minmax(0,1fr));margin-bottom:16px;padding:3px 8px}
.job-metrics button {text-align:left;display:grid;gap:3px;background:transparent;border:0;border-bottom:2px solid transparent;padding:13px 18px;cursor:pointer;color:var(--hire-ink)}
.job-metrics button[aria-pressed=true] {border-color:var(--hire-accent);color:var(--hire-accent-dark)}
.job-metrics span {font-size:13px;color:var(--hire-muted)} .job-metrics strong {font-size:25px;font-variant-numeric:tabular-nums}
.job-toolbar {padding:15px 16px;min-height:64px}
.job-toolbar :deep(.el-select) {width:128px} .job-toolbar :deep(.el-input) {width:220px}
.job-toolbar .column-trigger {margin-inline-start:auto}
.job-board :deep(th.el-table__cell) {background:#f6f8fa;font-size:12px;color:#526478}
.job-board :deep(.folder-row td) {background:#f8fafb;font-weight:650}
.job-board :deep(.department td) {background:#fff;font-weight:550}
.job-board :deep(.job-row) {cursor:pointer}
.folder-title {display:inline-flex;vertical-align:middle;gap:7px;align-items:center}
.folder-title svg,.job-detail-title svg {width:17px;height:17px;color:#6579a2;flex:none}
.job-title {font-weight:630;text-align:left;text-decoration:none}
.job-title:hover {color:var(--hire-accent)}
.flow-text {display:block;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.job-numbers {font-variant-numeric:tabular-nums;white-space:nowrap}
.job-footer {display:flex;gap:10px;justify-content:space-between;align-items:center;padding:13px 16px;flex-wrap:wrap}
.column-edit {display:grid;grid-template-columns:1fr 23px 23px;gap:4px;align-items:center}
.column-edit button:disabled {opacity:.33;cursor:default}
.job-detail-title {display:flex;gap:10px;align-items:flex-start}
.job-detail-title h2 {font-size:21px;line-height:1.25;margin-bottom:5px}
.job-detail-title svg {margin-top:4px;width:25px;height:25px}
.job-facts {display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:18px;background:#f6f8fa;margin:18px 0;border-radius:8px;font-size:13px}
.job-facts dt {color:var(--hire-muted)} .job-facts dd {margin:3px 0 0}
.job-requirement-heading {margin-top:17px}
.job-tag-details {display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-top:14px;font-size:13px}
.job-tag-details small {color:var(--hire-muted)}
.job-description {white-space:pre-wrap;margin-top:8px;line-height:1.75}
.job-editor {display:grid;grid-template-columns:1fr 1fr;gap:15px}
.job-editor .wide,.job-editor .hire-error {grid-column:1/-1}
.job-editor label {display:grid;gap:5px;font-size:13px}
.job-editor :is(input[type=number],input[type=date]) {min-width:0;width:100%;padding:6px 8px;border:1px solid var(--hire-line);border-radius:6px;color:inherit;background:white}
.round-edit {margin-top:8px;display:flex;align-items:center;gap:8px;font-size:13px}
.round-edit button {flex:none}
.job-import-form {display:grid;gap:10px}
.job-selected-file {display:flex;align-items:center;justify-content:space-between;gap:10px;
  border:1px solid var(--hire-line);border-radius:6px;padding:8px 10px}
.job-selected-file strong {min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.job-import-form p {color:var(--hire-muted)}
@media (max-width:620px) {
  .jobs-page .hire-heading {align-items:flex-start;flex-wrap:wrap}
  .job-metrics button {padding:10px 5px}.job-metrics span{font-size:12px}
  .job-toolbar :deep(.el-input),.job-toolbar :deep(.el-select){max-width:100%;width:116px}
  .job-toolbar>.el-input {width:100%}
}
</style>
