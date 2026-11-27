<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { BriefcaseOutline, FolderOpenOutline, SearchOutline, SettingsOutline, AddOutline, SyncOutline, EllipsisHorizontal } from '@vicons/ionicons5'
import { ElButton, ElDialog, ElDrawer, ElDropdown, ElDropdownMenu, ElDropdownItem, ElInput, ElSelect, ElOption, ElTable, ElTableColumn, ElTag, ElPagination, ElPopover, ElCheckbox, ElMessage } from 'element-plus'
import { api } from '@/api/client'
import { dateLabel, jobStatusLabel, jobStatusOptions, type Job } from '@/api/hr'
import { jobDirectory, type JobDirectory, type Recruiter } from '@/api/recruiting'
import { createCatalogFolder, fetchCatalog, fetchSync, startCatalogSync, type CatalogFolder, type FtpCatalog } from '@/api/ftpCatalog'
import JobPostingDetails from '@/components/JobPostingDetails.vue'
import { useAuthStore } from '@/stores/auth'

type Node = { key:string; title:string; kind:'folder'|'job'; parent:string; departmentId?:number; job?:Job; folder?:CatalogFolder }
const catalog = ref<FtpCatalog>({enabled:false,synced_at:null,folders:[]})
const location = ref('')
const creating = ref<'folder'|'job'>()
const newName = ref('')
const newDepartment = ref<number>()
const createError = ref('')
const creatingBusy = ref(false)
const syncing = ref(false)
let syncTimer: ReturnType<typeof setTimeout> | undefined
let refreshTimer: ReturnType<typeof setInterval> | undefined
let mounted = true
onBeforeUnmount(()=>{ mounted=false; clearTimeout(syncTimer); clearInterval(refreshTimer) })
const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const directory = ref<JobDirectory>({items:[],departments:[],companies:[],recruiters:[]})
const search = ref('')
const status = ref('')
const kind = ref('')
const category = ref('')
const categories = computed(()=>[...new Set(directory.value.items.flatMap(job=>job.category ? [job.category] : []))].sort((a,b)=>a.localeCompare(b,'zh-CN')))
const processType = ref('')
const page = ref(1)
const pageSize = ref(50)
const loadError = ref('')
const editorError = ref('')
const loading = ref(false)
const saving = ref(false)
const drawer = ref(false)
const drawerMode = ref<'view'|'edit'>('view')
const active = ref<Job>()
const roundName = ref('')
const experienceOptions=['在校生','应届生','1年以内','1–3年','3–5年','5–10年','10年以上','不限']
const types:Record<string,string> = {social:'社会招聘',campus:'校园招聘',internship:'实习招聘',internal:'内部招聘'}
const columns = ref([
  { id:'category', label:'岗位分类', min:120, visible:true },
  { id:'department', label:'所属部门', min:100, visible:true },
  { id:'headcount', label:'计划人数', min:90, visible:true },
  { id:'status', label:'招聘状态', min:100, visible:true },
  { id:'flow', label:'招聘流程', min:250, visible:true },
  { id:'owner', label:'负责人', min:128, visible:true },
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
    && (!category.value || job.category===category.value)
    && (!processType.value || (processType.value==='interview')===Boolean(job.rounds.length))
    && (!term || [job.title,job.department_name,job.department_recruiter_name || ''].some(value=>value.toLocaleLowerCase().includes(term)))
    && (!route.query.department || String(job.department_id)===route.query.department)
    && (!route.query.job || String(job.id)===route.query.job)
}))
const filteredMode = computed(()=>[search.value,status.value,kind.value,category.value,processType.value,
  route.query.department,route.query.job].some(Boolean))
const nodes = computed(()=>{
  const jobs = new Map(directory.value.items.map(job=>[job.id,job]))
  const placed = new Set<number>()
  const rows = new Map<string,Node>()
  for (const folder of catalog.value.folders) {
    const parentPath = folder.path.slice(0,Math.max(0,folder.path.lastIndexOf('/')))
    const parent = parentPath ? 'ftp:'+parentPath : ''
    const title = folder.path.split('/').at(-1) ?? folder.path
    if (folder.job_id) {
      const job = jobs.get(folder.job_id)
      if (!job) continue
      rows.set('job:'+job.id,{key:'job:'+job.id,title:job.title,kind:'job',parent,job,folder})
      placed.add(job.id)
    } else {
      const key='ftp:'+folder.path
      rows.set(key,{key,title,kind:'folder',parent,folder,departmentId:folder.department_id ?? undefined})
    }
  }
  const legacyRoot = catalog.value.enabled ? 'legacy' : ''
  for (const job of directory.value.items) {
    if (placed.has(job.id)) continue
    if (legacyRoot && !rows.has(legacyRoot)) rows.set(legacyRoot,{key:legacyRoot,title:'系统岗位',kind:'folder',parent:''})
    const company='company:'+job.company_id
    const dept='department:'+job.department_id
    if (!rows.has(company)) rows.set(company,{key:company,title:job.company_name,kind:'folder',parent:legacyRoot})
    if (!rows.has(dept)) rows.set(dept,{key:dept,title:job.department_name,kind:'folder',parent:company,departmentId:job.department_id})
    rows.set('job:'+job.id,{key:'job:'+job.id,title:job.title,kind:'job',parent:dept,job})
  }
  return rows
})
const current = computed(()=>nodes.value.get(location.value))
const breadcrumbs = computed(()=>{
  const trail:Node[] = []
  let node=current.value
  while (node) {
    trail.unshift(node)
    node=nodes.value.get(node.parent)
  }
  return trail
})
const canCreate = computed(()=>catalog.value.enabled && !filteredMode.value &&
  (location.value ? current.value?.folder?.writable : auth.isSuperAdmin))
const visibleRows = computed(()=>{
  const allowed=new Set(filtered.value.map(job=>job.id))
  return [...nodes.value.values()].filter(node=>filteredMode.value
    ? node.job && allowed.has(node.job.id) : node.parent===location.value)
})
const visibleJobs = computed(()=>visibleRows.value.filter(row=>row.job))
const board = computed(()=>[
  ...visibleRows.value.filter(row=>!row.job).sort((a,b)=>a.title.localeCompare(b.title,'zh-CN')),
  ...visibleJobs.value.slice((page.value-1)*pageSize.value,page.value*pageSize.value),
])
watch([search,status,kind,category,processType,pageSize,location,()=>route.query],()=>{page.value=1})
watch(()=>visibleJobs.value.length,total=>{page.value=Math.min(page.value,Math.max(1,Math.ceil(total/pageSize.value)))})
function openFolder(key:string) {
  location.value=key
  search.value=status.value=kind.value=category.value=processType.value=''
}
const counts = computed(()=>[
  { title:'全部岗位', value:directory.value.items.length, filter:'' },
  { title:'进行中', value:directory.value.items.filter(job=>job.status==='open').length, filter:'open' },
  { title:'未开始', value:directory.value.items.filter(job=>job.status==='paused').length, filter:'paused' },
  { title:'已结束', value:directory.value.items.filter(job=>job.status==='closed').length, filter:'closed' },
])
const form = reactive({
  title:'', jd:'', status:'paused', department_id:undefined as number|undefined,
  recruitment_type:'social', recruiter_user_id:null as number|null, planned_headcount:null as number|null,
  target_date:'', requirements:'', required_skills:[] as string[], experience_tags:[] as string[],
  recruitment_start:'', recruitment_end:'', rounds:[] as {id?:number;name:string}[],
})
function recruiterOptions(departmentId:number|undefined):Recruiter[] {
  const department=directory.value.departments.find(item=>item.id===departmentId)
  if(!auth.isSuperAdmin) return (department?.managers || []).map(manager=>({...manager,department_names:department?[department.name]:[]}))
  const byDepartment=(recruiter:Recruiter)=>Number(recruiter.department_names.includes(department?.name || ''))
  return [...(directory.value.recruiters || [])].sort((left,right)=>byDepartment(right)-byDepartment(left))
}
const recruiters=computed(()=>recruiterOptions(form.department_id))
const externalRecruiter=computed(()=>drawerMode.value==='edit' && active.value?.recruiter_user_id
  && !recruiters.value.some(item=>item.id===active.value?.recruiter_user_id) ? active.value : undefined)
async function load() {
  loading.value=true
  try {
    const previous = breadcrumbs.value.map(node=>node.key).reverse()
    const [jobs, folders] = await Promise.all([jobDirectory(),fetchCatalog()])
    directory.value=jobs
    catalog.value=folders
    if (location.value && !current.value) location.value=previous.find(key=>nodes.value.has(key)) ?? ''
    loadError.value=''
  }
  catch(error){loadError.value=(error as Error).message}
  finally{loading.value=false}
}
onMounted(()=>{
  void load()
  refreshTimer=setInterval(async()=>{
    if (document.hidden || !catalog.value.enabled || loading.value || syncing.value) return
    try {
      const latest=await fetchCatalog()
      if (mounted && latest.synced_at!==catalog.value.synced_at) await load()
    } catch {
      // 页面操作和手动同步会显示请求错误。
    }
  },30000)
})
function openJob(mode:typeof drawerMode.value,job:Job) {
  drawerMode.value=mode
  active.value=job
  editorError.value=''
  roundName.value=''
  Object.assign(form,{
    title:job.title,jd:job.jd || '',status:job.status,department_id:job.department_id,
    recruitment_type:job.recruitment_type || 'social',recruiter_user_id:job.recruiter_user_id ?? null,
    target_date:job.target_date || '',requirements:job.requirements || '',
    required_skills:[...job.required_skills],experience_tags:[...job.experience_tags],
    recruitment_start:job.recruitment_start || '',recruitment_end:job.recruitment_end || '',
    planned_headcount:job.planned_headcount ?? null,rounds:job.rounds.map(r=>({id:r.id,name:r.name})),
  })
  drawer.value=true
}
async function saveJob() {
  if(!active.value) return
  if(!form.department_id || !form.title.trim()) {editorError.value='请填写岗位名称并选择所属部门。';return}
  if(!form.recruitment_type) {editorError.value='请选择岗位属性。';return}
  if(form.recruitment_start && form.recruitment_end && form.recruitment_start>form.recruitment_end) {editorError.value='招聘截止日期不能早于开始日期。';return}
  saving.value=true
  editorError.value=''
  try {
    const payload={...form,target_date:form.target_date || null,recruitment_start:form.recruitment_start || null,
      recruitment_end:form.recruitment_end || null,planned_headcount:form.planned_headcount || null}
    await api.put('/hr/jobs/'+active.value.id,payload)
    drawer.value=false
    ElMessage.success('岗位已保存')
    await load()
  } catch(error){editorError.value=(error as Error).message}
  finally{saving.value=false}
}
function openCreate(kind:'folder'|'job') {
  newName.value=''
  newDepartment.value=current.value?.departmentId
  createError.value=''
  creating.value=kind
}
async function saveFolder() {
  const isJob=creating.value==='job'
  if (!newName.value.trim() || (isJob && !newDepartment.value)) {
    createError.value=isJob ? '请填写岗位名称并选择部门。' : '请填写文件夹名称。'
    return
  }
  creatingBusy.value=true
  createError.value=''
  try {
    const created=await createCatalogFolder(current.value?.folder?.path ?? '',newName.value.trim(),
      isJob ? newDepartment.value ?? null : null)
    creating.value=undefined
    await load()
    ElMessage.success(isJob ? '岗位已创建' : '文件夹已创建')
    const job=directory.value.items.find(row=>row.id===created.job_id)
    if (job) openJob('edit',job)
  } catch (error) { createError.value=(error as Error).message }
  finally { creatingBusy.value=false }
}
async function synchronize() {
  syncing.value=true
  try {
    const {id}=await startCatalogSync()
    const cutoff=Date.now()+20*60*1000
    const poll=async()=>{
      if (!mounted) return
      try {
        const state=await fetchSync(id)
        if (!mounted) return
        if (state.status==='pending' || state.status==='running') {
          if (Date.now()>cutoff) throw new Error('同步仍在后台处理，请稍后查看目录。')
          syncTimer=setTimeout(()=>void poll(),1500)
          return
        }
        if (state.status==='failed') throw new Error(state.message)
        if (state.status==='complete') {
          await load()
          const result=`目录变更 ${state.folders} 项，简历新增 ${state.added}、更新 ${state.updated}、删除 ${state.deleted}。`
          if (state.skipped || state.message) ElMessage.warning(result+(state.message ?? `跳过 ${state.skipped} 份无效文件。`))
          else ElMessage.success(result)
        } else ElMessage.info('目录正在同步，请稍后刷新。')
      } catch (error) { ElMessage.error((error as Error).message) }
      syncing.value=false
    }
    await poll()
  } catch (error) {
    syncing.value=false
    ElMessage.error((error as Error).message)
  }
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
        <ElButton v-if="auth.isSuperAdmin" type="primary" :icon="SyncOutline" :loading="syncing"
          :disabled="!catalog.enabled" @click="synchronize">同步数据</ElButton>
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
      <div class="job-location">
        <nav aria-label="岗位目录">
          <button type="button" class="hire-inline-button" @click="openFolder('')">岗位目录</button>
          <template v-for="folder in breadcrumbs" :key="folder.key">
            <span aria-hidden="true">›</span>
            <button type="button" class="hire-inline-button" @click="openFolder(folder.key)">{{ folder.title }}</button>
          </template>
          <span v-if="filteredMode" class="hire-muted">搜索结果</span>
        </nav>
        <span v-if="catalog.synced_at" class="hire-muted job-sync-time">最近同步 {{ dateText(catalog.synced_at) }}</span>
        <ElDropdown v-if="canCreate" trigger="click" @command="openCreate">
          <ElButton :icon="AddOutline">新建</ElButton>
          <template #dropdown><ElDropdownMenu>
            <ElDropdownItem command="folder">文件夹</ElDropdownItem>
            <ElDropdownItem v-if="location && directory.departments.length" command="job">岗位</ElDropdownItem>
          </ElDropdownMenu></template>
        </ElDropdown>
      </div>
      <div class="hire-toolbar job-toolbar">
        <ElSelect v-model="processType" placeholder="招聘流程" clearable aria-label="招聘流程">
          <ElOption label="含面试流程" value="interview"/><ElOption label="不含面试流程" value="none"/>
        </ElSelect>
        <ElSelect v-model="kind" placeholder="岗位属性" clearable aria-label="岗位属性">
          <ElOption v-for="(name,code) in types" :key="code" :label="name" :value="code"/>
        </ElSelect>
        <ElSelect v-model="category" placeholder="岗位分类" clearable aria-label="岗位分类">
          <ElOption v-for="value in categories" :key="value" :label="value" :value="value"/>
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
      <ElTable :data="board" row-key="key" size="default"
        :row-class-name="({row}:{row:Node})=>row.kind==='job'?'job-row':'folder-row'"
        :empty-text="loading?'正在加载岗位…':filteredMode?'没有符合条件的岗位':'此文件夹为空'"
        @row-dblclick="(row:Node)=>!row.job && openFolder(row.key)" @row-click="(row:Node)=>row.job && router.push({name:'job-candidates',params:{jobId:row.job.id}})">
        <ElTableColumn prop="title" label="岗位名称" min-width="220">
          <template #default="{row}">
            <RouterLink v-if="row.job" class="job-title hire-inline-button"
              :to="{name:'job-candidates',params:{jobId:row.job.id}}" @click.stop>{{ row.title }}</RouterLink>
            <button v-else type="button" class="folder-title hire-inline-button" @click.stop="openFolder(row.key)">
              <FolderOpenOutline/>{{ row.title }}
            </button>
          </template>
        </ElTableColumn>
        <ElTableColumn v-for="column in columns.filter(item=>item.visible)" :key="column.id" :label="column.label" :min-width="column.min">
          <template #default="{row}">
            <template v-if="row.job">
              <span v-if="column.id==='category'">{{ row.job.category || '—' }}</span>
              <span v-else-if="column.id==='department'">{{ row.job.department_name }}</span>
              <span v-else-if="column.id==='headcount'">{{ row.job.planned_headcount===null ? '—' : row.job.planned_headcount+' 人' }}</span>
              <ElTag v-else-if="column.id==='status'" effect="light" :type="row.job.status==='open'?'success':row.job.status==='paused'?'warning':'info'">
                {{ jobStatusLabel(row.job.status) }}
              </ElTag>
              <span v-else-if="column.id==='flow'" class="recruitment-flow">
                {{ ['简历审核', ...row.job.rounds.map((r:{name:string})=>r.name), '录取'].join(' → ') }}
              </span>
              <span v-else-if="column.id==='owner'">{{ row.job.department_recruiter_name || '待分配' }}</span>
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
        <span class="hire-muted">共 {{ visibleJobs.length }} 个岗位</span>
        <ElPagination v-if="visibleJobs.length" v-model:current-page="page" v-model:page-size="pageSize" :page-sizes="[12,24,48]"
          :total="visibleJobs.length" layout="sizes, prev, pager, next" :pager-count="5" background small/>
      </footer>
    </section>

    <ElDrawer v-model="drawer" class="hire-drawer job-drawer" :size="'min(580px, 100vw)'"
      :title="drawerMode==='view'?'岗位详情':'编辑岗位'">
      <template v-if="drawerMode==='view' && active">
        <div class="job-detail-title"><BriefcaseOutline/><div><h2>{{ active.title }}</h2>
          <p class="hire-muted">{{ active.company_name }} / {{ active.department_name }} · {{ types[active.recruitment_type] }}</p></div></div>
        <dl class="job-facts"><div><dt>状态</dt><dd>{{ jobStatusLabel(active.status) }}</dd></div>
          <div><dt>岗位分类</dt><dd>{{ active.category || '未分类' }}</dd></div>
          <div><dt>负责人</dt><dd>{{ active.department_recruiter_name || '待分配' }}</dd></div>
          <div><dt>招聘计划</dt><dd>{{ active.planned_headcount ?? '未设置' }}</dd></div>
          <div><dt>更新时间</dt><dd>{{ dateText(active.updated_at) }}</dd></div>
          <div><dt>{{ active.recruitment_window_is_reference ? '招聘期限 · 参考排期' : '招聘期限' }}</dt><dd>{{ active.recruitment_start || '未设置' }} 至 {{ active.recruitment_end || '未设置' }}</dd></div>
          <div><dt>计划完成日期</dt><dd>{{ active.target_date || '未设置' }}</dd></div>
          <div><dt>面试流程</dt><dd>{{ active.rounds.map(round=>round.name).join(' → ') || '无面试流程' }}</dd></div></dl>
        <JobPostingDetails :job="active"/>
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
        <label class="wide">岗位名称<ElInput v-model="form.title" maxlength="120" required/></label>
        <label>所属部门<ElSelect v-model="form.department_id" disabled aria-label="所属部门">
          <ElOption v-for="department in directory.departments" :key="department.id" :value="department.id" :label="department.company_name+' / '+department.name"/>
        </ElSelect></label>
        <label>岗位属性<ElSelect v-model="form.recruitment_type" aria-label="岗位属性">
          <ElOption v-for="(title,code) in types" :key="code" :value="code" :label="title"/>
        </ElSelect></label>
        <label>招聘状态<ElSelect v-model="form.status" aria-label="招聘状态">
          <ElOption v-for="item in jobStatusOptions" :key="item.value" :value="item.value" :label="item.label"/>
        </ElSelect></label>
        <label>负责人
          <ElSelect v-model="form.recruiter_user_id" aria-label="负责人" clearable :value-on-clear="null"
            filterable :disabled="!recruiters.length && !externalRecruiter"
            :placeholder="recruiters.length ? '选择现有招聘人' : '暂无可选招聘人'">
            <ElOption v-if="externalRecruiter" :label="externalRecruiter.department_recruiter_name || '当前负责人'"
              :value="externalRecruiter.recruiter_user_id!" disabled/>
            <ElOption v-for="recruiter in recruiters" :key="recruiter.id" :value="recruiter.id"
              :label="recruiter.display_name === recruiter.department_names.join('、')+' HR'
                ? recruiter.display_name : [recruiter.display_name,recruiter.department_names.join('、')].filter(Boolean).join(' · ')"/>

          </ElSelect>
        </label>
        <label>计划招聘人数<input v-model.number="form.planned_headcount" type="number" min="1" max="100000"></label>
        <label>计划完成日期<input v-model="form.target_date" type="date"></label>
        <label>招聘开始日期<input v-model="form.recruitment_start" type="date" :max="form.recruitment_end || undefined"></label>
        <label>招聘截止日期<input v-model="form.recruitment_end" type="date" :min="form.recruitment_start || undefined"></label>
        <label class="wide">岗位职责<ElInput v-model="form.jd" type="textarea" :rows="4" maxlength="4000" placeholder="填写日常职责、工作范围及目标"/></label>
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

    <ElDialog :model-value="Boolean(creating)" width="min(480px, 95vw)"
      :title="creating==='job'?'新建岗位':'新建文件夹'" append-to-body
      @update:model-value="value=>{ if (!value) creating=undefined }">
      <form id="ftp-folder-editor" class="folder-editor" @submit.prevent="saveFolder">
        <label>{{ creating==='job'?'岗位名称':'文件夹名称' }}
          <ElInput v-model="newName" :maxlength="creating==='job'?120:255" autofocus/>
        </label>
        <label v-if="creating==='job'">所属部门
          <ElSelect v-model="newDepartment" :disabled="Boolean(current?.departmentId)" placeholder="选择已有部门">
            <ElOption v-for="department in directory.departments" :key="department.id"
              :value="department.id" :label="department.company_name+' / '+department.name"/>
          </ElSelect>
        </label>
        <p v-if="createError" class="hire-error" role="alert">{{ createError }}</p>
      </form>
      <template #footer>
        <ElButton @click="creating=undefined">取消</ElButton>
        <ElButton type="primary" native-type="submit" form="ftp-folder-editor" :loading="creatingBusy">创建</ElButton>
      </template>
    </ElDialog>

  </main>
</template>

<style scoped>
.job-metrics {display:grid;grid-template-columns:repeat(4,minmax(0,1fr));margin-bottom:16px;padding:4px 8px;overflow:hidden}
.job-metrics button {text-align:left;display:grid;gap:4px;background:transparent;border:0;border-bottom:2px solid transparent;padding:14px 18px;cursor:pointer;color:var(--hire-ink);transition:background-color var(--rs-motion-fast),color var(--rs-motion-fast),transform var(--rs-motion-fast) var(--rs-ease)}
.job-metrics button:hover {background:var(--rs-primary-softer);transform:translateY(-1px)}
.job-metrics button[aria-pressed=true] {border-color:var(--hire-accent);color:var(--hire-accent-dark);background:linear-gradient(180deg,transparent,var(--rs-primary-softer))}
.job-metrics span {font-size:13px;color:var(--hire-muted)} .job-metrics strong {font-size:25px;font-variant-numeric:tabular-nums}
.job-toolbar {padding:15px 16px;min-height:64px}
.job-toolbar :deep(.el-select) {width:128px} .job-toolbar :deep(.el-input) {width:220px}
.job-toolbar .column-trigger {margin-inline-start:auto}
.job-board :deep(th.el-table__cell) {background:#f6f8fa;font-size:12px;color:#526478}
.job-board :deep(.folder-row td) {background:#f8fafb;font-weight:650}
.job-board :deep(.department td) {background:#fff;font-weight:550}
.job-board :deep(.job-row) {cursor:pointer}
.folder-title {display:inline-flex;vertical-align:middle;gap:7px;align-items:center}
.folder-title svg,.job-detail-title svg {width:17px;height:17px;color:var(--hire-accent);flex:none}
.job-title {font-weight:630;text-align:left;text-decoration:none}
.job-title:hover {color:var(--hire-accent)}
.recruitment-flow {display:block;line-height:1.6;white-space:normal}
.job-numbers {font-variant-numeric:tabular-nums;white-space:nowrap}
.job-footer {display:flex;gap:10px;justify-content:space-between;align-items:center;padding:13px 16px;flex-wrap:wrap}
.column-edit {display:grid;grid-template-columns:1fr 23px 23px;gap:4px;align-items:center}
.column-edit button:disabled {opacity:.33;cursor:default}
.job-detail-title {display:flex;gap:10px;align-items:flex-start}
.job-detail-title h2 {font-size:21px;line-height:1.25;margin-bottom:5px}
.job-detail-title svg {margin-top:4px;width:25px;height:25px}
.job-facts {display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:18px;border:1px solid #dcebe8;background:linear-gradient(135deg,#f3faf8,#f7f8fc);margin:18px 0;border-radius:11px;font-size:13px}
.job-facts dt {color:var(--hire-muted)} .job-facts dd {margin:3px 0 0}
.job-tag-details {display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-top:14px;font-size:13px}
.job-tag-details small {color:var(--hire-muted)}
.job-editor {display:grid;grid-template-columns:1fr 1fr;gap:15px}
.job-editor .wide,.job-editor .hire-error {grid-column:1/-1}
.job-editor label {display:grid;gap:5px;font-size:13px}
.job-editor :is(input[type=number],input[type=date]) {min-width:0;width:100%;padding:7px 9px;border:1px solid var(--rs-border-strong);border-radius:8px;color:inherit;background:white;transition:border-color var(--rs-motion-fast),box-shadow var(--rs-motion-fast)}
.job-editor :is(input[type=number],input[type=date]):focus {outline:0;border-color:var(--hire-accent);box-shadow:0 0 0 3px rgba(13,137,126,.1)}
.round-edit {margin-top:8px;display:flex;align-items:center;gap:8px;font-size:13px}
.round-edit button {flex:none}
.job-location {display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:12px 16px;border-bottom:1px solid var(--hire-line)}
.job-location nav {display:flex;align-items:center;gap:8px;flex:1;flex-wrap:wrap}
.job-location nav span {color:var(--hire-muted)}
.job-sync-time {font-size:12px}
.folder-title {text-align:left;white-space:normal;overflow-wrap:anywhere}
.folder-title svg {color:#a67322}
.folder-editor,.folder-editor label {display:grid;gap:9px}
.folder-editor {gap:16px}
@media (max-width:620px) {
  .jobs-page .hire-heading {align-items:flex-start;flex-wrap:wrap}
  .job-metrics button {padding:10px 5px}.job-metrics span{font-size:12px}
  .job-toolbar :deep(.el-input),.job-toolbar :deep(.el-select){max-width:100%;width:116px}
  .job-toolbar>.el-input {width:100%}
}
</style>
