<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { AddOutline, BusinessOutline, ChevronDownOutline, FolderOpenOutline, PeopleOutline, SearchOutline, ShieldCheckmarkOutline, TrashOutline } from '@vicons/ionicons5'
import { ElButton, ElDialog, ElDropdown, ElDropdownItem, ElDropdownMenu, ElInput, ElMessage, ElOption, ElSelect, ElTable, ElTableColumn } from 'element-plus'
import { api } from '@/api/client'
import type { Company } from '@/api/recruiting'
import { useAuthStore } from '@/stores/auth'

type Department={id:number;name:string;company_id:number;company_name:string;status:'active'|'disabled'}
type Account={id:number;display_name:string;email:string;status:'active'|'disabled';super_admin:boolean;departments:number[]}
type Directory={companies:Company[];departments:Department[];users:Account[]}
const route=useRoute()
const router=useRouter()
const auth=useAuthStore()
const directory=ref<Directory>({companies:[],departments:[],users:[]})
const tab=ref<'accounts'|'organization'>('accounts')
const search=ref('')
const filterDepartment=ref<number>()
const filterStatus=ref<Account['status']>()
const accountOpen=ref(false)
const loading=ref(false)
const error=ref('')
const saveError=ref('')
const saving=ref<'account'|'company'|'department'|'placement'>()
const account=reactive({display_name:'',email:'',password:'',department_id:undefined as number|undefined})
const companyName=ref('')
const departmentName=ref('')
const placementDepartment=ref<number>()
const placementCompany=ref<number>()
type AccountAction='active'|'disabled'|'delete'
const actionDialog=ref(false)
const actionTarget=ref<{user:Account;action:AccountAction}>()
const actionError=ref('')
const changing=ref(false)
const actionTitles:Record<AccountAction,string>={active:'启用账号',disabled:'停用账号',delete:'删除账号'}
const activeCount=computed(()=>directory.value.users.filter(row=>row.status==='active').length)
const stats=computed(()=>[
  {label:'全部账号',value:directory.value.users.length,detail:'包含启用和停用的账号',icon:PeopleOutline,tone:'teal'},
  {label:'启用账号',value:activeCount.value,detail:`另有 ${directory.value.users.length-activeCount.value} 个账号已停用`,icon:ShieldCheckmarkOutline,tone:'green'},
  {label:'部门',value:directory.value.departments.length,detail:`${directory.value.companies.length} 个公司与子公司`,icon:FolderOpenOutline,tone:'blue'},
])
const activeDepartments=computed(()=>directory.value.departments.filter(item=>item.status==='active'))
const departmentsById=computed(()=>new Map(directory.value.departments.map(item=>[item.id,item])))
const filtered=computed(()=>{
  const term=search.value.trim().toLocaleLowerCase()
  return directory.value.users.filter(user=>(!filterDepartment.value || user.departments.includes(filterDepartment.value))
    && (!filterStatus.value || user.status===filterStatus.value)
    && (!term || user.display_name.toLocaleLowerCase().includes(term) || user.email.toLocaleLowerCase().includes(term)))
})
watch(()=>route.query.department,value=>{
  filterDepartment.value=typeof value==='string' && /^\d+$/.test(value)?Number(value):undefined
  if(filterDepartment.value) tab.value='accounts'
},{immediate:true})
function changeDepartment(value:number|undefined) {
  void router.replace({name:'users',query:{department:value || undefined}})
}
function showNewAccount() {
  Object.assign(account,{display_name:'',email:'',password:'',department_id:activeDepartments.value.some(d=>d.id===filterDepartment.value)?filterDepartment.value:undefined})
  accountOpen.value=true
  saveError.value=''
}
async function load() {
  loading.value=true
  error.value=''
  try {directory.value=await api.get<Directory>('/admin/organization')}
  catch(cause){error.value=(cause as Error).message}
  finally {loading.value=false}
}
async function save(kind:NonNullable<typeof saving.value>) {
  if(saving.value) return
  saving.value=kind
  saveError.value=''
  try {
    if(kind==='account') {
      if(!account.department_id) return
      await api.post('/admin/users',{...account})
      account.password=''
      accountOpen.value=false
      ElMessage.success('部门 HR 账号已创建')
    } else if(kind==='company') {
      await api.post('/workspace/companies',{name:companyName.value.trim()})
      companyName.value=''
      ElMessage.success('子公司已创建')
    } else if(kind==='department') {
      await api.post('/admin/departments',{name:departmentName.value.trim()})
      departmentName.value=''
      ElMessage.success('部门已创建')
    } else {
      if(!placementCompany.value || !placementDepartment.value) return
      await api.put('/workspace/departments/'+placementDepartment.value+'/company',{company_id:placementCompany.value})
      ElMessage.success('部门归属已更新')
    }
    await load()
  } catch(cause) {saveError.value=(cause as Error).message}
  finally {saving.value=undefined}
}
function selectPlacement(department:number, company:number) {
  placementDepartment.value=department
  placementCompany.value=company
}
function switchTab(value:typeof tab.value) {
  tab.value=value
  saveError.value=''
}
function openAction(id:number,action:AccountAction) {
  const user=directory.value.users.find(item=>item.id===id)
  if(!user || user.id===auth.session?.userId) return
  actionTarget.value={user,action}
  actionError.value=''
  actionDialog.value=true
}
async function confirmAction() {
  if(changing.value || !actionTarget.value) return
  const {user,action}=actionTarget.value
  const path='/admin/users/'+user.id
  changing.value=true
  actionError.value=''
  try {
    if(action==='delete') await api.delete(path)
    else await api.put(path+'/status',{status:action})
    actionDialog.value=false
    if(action==='delete') directory.value.users=directory.value.users.filter(row=>row.id!==user.id)
    else directory.value.users=directory.value.users.map(row=>row.id===user.id?{...row,status:action}:row)
    ElMessage.success(action==='delete'?'账号已删除':action==='active'?'账号已启用':'账号已停用')
    await load()
  } catch(cause){actionError.value=(cause as Error).message}
  finally {changing.value=false}
}
onMounted(load)
</script>

<template>
  <main class="hire-page user-page">
    <header class="hire-heading">
      <div><h1>用户管理</h1><p>维护企业账号及其组织归属</p></div>
      <ElButton v-if="tab==='accounts' && !accountOpen" type="primary" :icon="AddOutline" @click="showNewAccount">新增账号</ElButton>
    </header>
    <section class="user-metrics" aria-label="用户管理概览">
      <div v-for="metric in stats" :key="metric.label" class="hire-surface user-metric">
        <div><span>{{ metric.label }}</span><strong>{{ loading && !directory.companies.length?'—':metric.value }}</strong>
          <small>{{ metric.detail }}</small></div>
        <span class="metric-icon" :data-tone="metric.tone"><component :is="metric.icon" aria-hidden="true"/></span>
      </div>
    </section>
    <p v-if="error" class="hire-error" role="alert">{{ error }} <button type="button" class="hire-inline-button" @click="load">重试</button></p>
    <div class="user-nav" aria-label="用户管理内容">
      <button type="button" :aria-pressed="tab==='accounts'" @click="switchTab('accounts')"><PeopleOutline aria-hidden="true"/>账号列表</button>
      <button type="button" :aria-pressed="tab==='organization'" @click="switchTab('organization')"><BusinessOutline aria-hidden="true"/>公司与部门</button>
    </div>
    <p v-if="saveError" class="hire-error" role="alert">{{ saveError }}</p>

    <template v-if="tab==='accounts'">
      <section v-if="accountOpen" class="hire-surface user-editor" aria-labelledby="new-account-title">
        <h2 id="new-account-title">新增部门 HR 账号</h2>
        <form class="user-account-form" @submit.prevent="save('account')">
          <label>姓名<ElInput v-model="account.display_name" autocomplete="off" required maxlength="80"/></label>
          <label>登录邮箱<ElInput v-model="account.email" autocomplete="off" type="email" required maxlength="160"/></label>
          <label>初始密码<ElInput v-model="account.password" autocomplete="new-password" type="password" required minlength="8" maxlength="128"/></label>
          <label>所属部门
            <ElSelect v-model="account.department_id" filterable placeholder="选择部门" aria-label="新账号所属部门">
              <ElOption v-for="dept in activeDepartments" :key="dept.id" :value="dept.id" :label="dept.company_name+' / '+dept.name"/>
            </ElSelect>
          </label>
          <footer><small>账号创建后可以管理所选部门的岗位和简历。</small>
            <div><ElButton :disabled="!!saving" @click="accountOpen=false;account.password='';saveError=''">取消</ElButton>
              <ElButton type="primary" native-type="submit" :disabled="!account.department_id" :loading="saving==='account'">创建账号</ElButton></div>
          </footer>
        </form>
      </section>
      <section class="hire-surface directory-table account-directory" aria-label="企业账号">
        <div class="directory-intro"><h2>企业账号</h2><span>{{ filtered.length }} 个账号</span></div>
        <header class="directory-toolbar hire-toolbar">
          <ElInput v-model="search" :prefix-icon="SearchOutline" aria-label="搜索账号" placeholder="搜索姓名或邮箱" clearable/>
          <ElSelect :model-value="filterDepartment" filterable clearable placeholder="全部部门" aria-label="按部门筛选账号" @change="changeDepartment">
            <ElOption v-for="dept in directory.departments" :key="dept.id" :value="dept.id" :label="dept.company_name+' / '+dept.name"/>
          </ElSelect>
          <ElSelect v-model="filterStatus" clearable placeholder="全部状态" aria-label="按状态筛选账号" class="status-filter">
            <ElOption value="active" label="已启用"/><ElOption value="disabled" label="已停用"/>
          </ElSelect>
        </header>
        <ElTable :data="filtered" row-key="id" :empty-text="loading?'正在加载账号…':'没有符合条件的账号'">
          <ElTableColumn label="账号" min-width="230"><template #default="{row}">
            <div class="account-identity">
              <span class="directory-avatar" :class="{'admin-avatar':row.super_admin}" aria-hidden="true">{{ row.display_name.slice(0,2) }}</span>
              <div><strong>{{ row.display_name }}</strong><small>{{ row.email }}</small></div>
            </div>
          </template></ElTableColumn>
          <ElTableColumn label="角色" min-width="114"><template #default="{row}">
            <span class="user-tag" :class="{ 'admin-tag':row.super_admin }">{{ row.super_admin?'超级管理员':'部门 HR' }}</span>
          </template></ElTableColumn>
          <ElTableColumn label="所属部门" min-width="180"><template #default="{row}">
            <div v-if="row.departments.length" class="department-tags">
              <span v-for="id in row.departments" :key="id" class="user-tag">{{ departmentsById.get(id)?.name || '部门 #'+id }}</span>
            </div>
            <span v-else class="hire-muted">{{ row.super_admin?'全公司':'未分配' }}</span>
          </template></ElTableColumn>
          <ElTableColumn label="状态" width="110"><template #default="{row}">
            <span class="user-status" :class="{ 'active-status':row.status==='active' }">{{ row.status==='active'?'已启用':'已停用' }}</span>
          </template></ElTableColumn>
          <ElTableColumn label="操作" width="122" align="right"><template #default="{row}">
            <span v-if="row.id===auth.session?.userId" class="current-account">当前账号</span>
            <ElDropdown v-else trigger="click" @command="action=>openAction(row.id,action)">
              <button type="button" class="manage-account" :aria-label="'管理账号 '+row.display_name">
                管理<ChevronDownOutline aria-hidden="true"/>
              </button>
              <template #dropdown><ElDropdownMenu>
                <ElDropdownItem v-if="row.status==='active'" command="disabled">停用账号</ElDropdownItem>
                <ElDropdownItem v-else command="active">启用账号</ElDropdownItem>
                <ElDropdownItem divided command="delete" class="remove-account"><TrashOutline aria-hidden="true"/>删除账号</ElDropdownItem>
              </ElDropdownMenu></template>
            </ElDropdown>
          </template></ElTableColumn>
        </ElTable>
      </section>
    </template>
    <template v-else>
      <div class="organization-forms">
        <form class="hire-surface user-editor" @submit.prevent="save('company')"><h2>新增子公司</h2>
          <label>子公司名称<ElInput v-model="companyName" maxlength="80" required/></label>
          <ElButton type="primary" native-type="submit" :disabled="!companyName.trim()" :loading="saving==='company'">添加子公司</ElButton>
        </form>
        <form class="hire-surface user-editor" @submit.prevent="save('department')"><h2>新增部门</h2>
          <label>部门名称<ElInput v-model="departmentName" maxlength="80" required/></label>
          <ElButton type="primary" native-type="submit" :disabled="!departmentName.trim()" :loading="saving==='department'">添加部门</ElButton>
        </form>
        <form class="hire-surface user-editor" @submit.prevent="save('placement')"><h2>调整部门归属</h2>
          <label>选择部门<ElSelect v-model="placementDepartment" aria-label="选择部门" filterable>
            <ElOption v-for="dept in activeDepartments" :key="dept.id" :value="dept.id" :label="dept.name"/>
          </ElSelect></label>
          <label>归属公司<ElSelect v-model="placementCompany" aria-label="归属公司" filterable>
            <ElOption v-for="company in directory.companies" :key="company.id" :value="company.id" :label="company.name"/>
          </ElSelect></label>
          <ElButton type="primary" native-type="submit" :disabled="!placementCompany || !placementDepartment" :loading="saving==='placement'">保存归属</ElButton>
        </form>
      </div>
      <section class="hire-surface directory-table" aria-label="部门归属">
        <header class="directory-toolbar"><h2>部门归属</h2></header>
        <ElTable :data="directory.departments" row-key="id" :empty-text="loading?'正在加载部门…':'暂无部门'">
          <ElTableColumn prop="name" label="部门" min-width="130"/>
          <ElTableColumn prop="company_name" label="归属公司" min-width="165"/>
          <ElTableColumn label="状态" width="80"><template #default="{row}">{{ row.status==='active'?'启用':'停用' }}</template></ElTableColumn>
          <ElTableColumn label="操作" width="105"><template #default="{row}">
            <ElButton v-if="row.status==='active'" text type="primary" @click="selectPlacement(row.id,row.company_id)">调整归属</ElButton>
          </template></ElTableColumn>
        </ElTable>
      </section>
    </template>
    <ElDialog v-model="actionDialog" :title="actionTarget ? actionTitles[actionTarget.action] : '管理账号'"
      width="min(440px, 94vw)" align-center append-to-body :close-on-click-modal="false"
      :close-on-press-escape="!changing" :show-close="!changing">
      <div v-if="actionTarget" class="account-confirm">
        <strong>{{ actionTarget.user.display_name }}</strong><span>{{ actionTarget.user.email }}</span>
        <p v-if="actionTarget.action==='delete'">删除后，该账号无法登录，且会从账号列表移除。历史跟进及审计记录会保留。</p>
        <p v-else-if="actionTarget.action==='disabled'">停用后，该账号将退出当前会话，重新启用前无法登录。</p>
        <p v-else>启用后，该账号可以使用原来的密码重新登录。</p>
        <p v-if="actionError" class="hire-error" role="alert">{{ actionError }}</p>
      </div>
      <template #footer>
        <ElButton :disabled="changing" @click="actionDialog=false">取消</ElButton>
        <ElButton :type="actionTarget?.action==='delete'?'danger':'primary'" :loading="changing" @click="confirmAction">
          确认{{ actionTarget ? actionTitles[actionTarget.action].slice(0,2) : '' }}
        </ElButton>
      </template>
    </ElDialog>
  </main>
</template>

<style scoped>
.user-metrics,.organization-forms {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:13px}
.user-metric {display:flex;justify-content:space-between;align-items:flex-start;gap:12px;padding:15px 17px;border-radius:12px;min-width:0}
.user-metric>div {display:grid;gap:5px;color:var(--hire-muted);font-size:13px}
.user-metric strong {color:var(--hire-ink);font-size:28px;font-variant-numeric:tabular-nums;line-height:1.12}
.user-metric small {font-size:11px;color:#647588}
.metric-icon {display:grid;place-items:center;width:42px;height:42px;border-radius:10px;background:#eaf7f3;color:#126559;flex:none}
.metric-icon[data-tone=green] {background:#eaf6ed;color:#2c6d4d}
.metric-icon[data-tone=blue] {background:#edf3fb;color:#3d608f}
.metric-icon svg {width:21px;height:21px}
.user-nav {display:inline-flex;gap:4px;margin:1px 0 11px;padding:3px;border:1px solid #e0e8ed;background:#eaf0f3;border-radius:9px}
.user-nav button {display:inline-flex;align-items:center;gap:6px;font:inherit;font-size:13px;padding:6px 11px;border-radius:6px;border:1px solid transparent;background:transparent;cursor:pointer;color:#4e6475}
.user-nav button svg {width:15px;height:15px}
.user-nav button[aria-pressed=true] {background:white;border-color:#dce7e9;color:var(--hire-accent-dark);font-weight:650;box-shadow:0 1px 2px #203c4c14}
.directory-table {overflow:hidden}
.directory-table :deep(.el-table) {--el-table-border-color:var(--hire-line);--el-table-row-hover-bg-color:#eff7f4;font-size:13px}
.directory-intro {display:flex;align-items:center;justify-content:space-between;gap:10px;padding:15px 17px 0}
.directory-intro h2 {font-size:16px}
.directory-intro span {border-radius:20px;background:#eef3f6;color:#4b6172;padding:3px 10px;font-size:12px}
.directory-toolbar {padding:11px 17px 15px}
.directory-toolbar :deep(.el-input),.directory-toolbar :deep(.el-select) {width:min(235px,100%)}
.directory-toolbar .status-filter {width:123px}
.directory-table :deep(th.el-table__cell) {background:#f6f8fa;color:var(--hire-muted);font-size:12px;font-weight:600}
.account-directory :deep(td.el-table__cell) {padding-block:8px}
.account-identity {display:flex;align-items:center;gap:10px;text-align:left}
.account-identity>div {min-width:0}
.account-identity strong,.account-identity small {display:block;line-height:1.5;overflow-wrap:anywhere}
.account-identity strong {font-size:13px;color:var(--hire-ink);font-weight:650}
.account-identity small {font-size:12px;color:#647588}
.directory-avatar {display:grid;place-items:center;flex:none;width:34px;height:34px;border-radius:8px;background:#edf3fa;color:#476388;font-size:12px;font-weight:650}
.directory-avatar.admin-avatar {background:#e7f5ef;color:#116858}
.department-tags {display:flex;flex-wrap:wrap;gap:4px}
.user-tag {display:inline-block;padding:2px 7px;border:1px solid #dce5ea;border-radius:4px;
  background:#f5f8fa;color:#415568;font-size:12px;line-height:1.4}
.user-tag.admin-tag {border-color:#eadfbe;background:#fbf5e6;color:#795516}
.user-status {display:inline-flex;align-items:center;gap:6px;font-size:12px;color:#596c7e;white-space:nowrap;padding:3px 10px;border-radius:20px;background:#f1f4f6}
.user-status::before {content:"";width:7px;height:7px;border-radius:50%;background:#8a99a7}
.user-status.active-status {color:#17624b;background:#eaf6ed}
.user-status.active-status::before {background:#3a9671}
.manage-account {display:inline-flex;align-items:center;gap:5px;padding:5px 8px;border:1px solid #dce7eb;border-radius:6px;
  background:white;color:var(--hire-accent-dark);font:inherit;font-size:12px;cursor:pointer;white-space:nowrap}
.manage-account svg,.remove-account svg {height:13px;width:13px}
.manage-account:hover {background:#eaf7f3;border-color:#b9d9d0}
.current-account {color:#647588;font-size:12px;white-space:nowrap}
.remove-account {color:#9a3440;gap:5px}
:deep(.el-dialog) {border-radius:14px}
:deep(.el-dialog__title) {font-size:15px;font-weight:650}
:deep(.el-dialog__header) {padding-bottom:6px}
.account-confirm {display:grid;gap:4px;padding:2px 0 4px}
.account-confirm strong {color:var(--hire-ink);font-size:15px}
.account-confirm>span {color:#607286;font-size:13px;overflow-wrap:anywhere;padding-bottom:9px;border-bottom:1px solid var(--hire-line);margin-bottom:4px}
.account-confirm>p {margin-top:8px;line-height:1.6}
.user-editor {padding:13px;display:grid;gap:10px;align-content:start}
.user-editor h2,.directory-toolbar h2 {font-size:15px}
.user-editor label {display:grid;gap:5px;font-size:13px;min-width:0}
.user-editor>.el-button {justify-self:start;margin-top:2px}
section.user-editor {margin-bottom:13px}
.user-account-form {display:grid;gap:12px;grid-template-columns:repeat(4,minmax(0,1fr))}
.user-account-form footer {grid-column:1/-1;display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}
.user-account-form footer small {color:var(--hire-muted)}
@media(max-width:890px){.user-account-form{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:660px){.organization-forms{grid-template-columns:1fr}.user-metrics{gap:6px}.user-metric{padding:9px 7px;gap:4px}.user-metric strong{font-size:22px}.user-metric small{display:none}.metric-icon{width:24px;height:24px}.metric-icon svg{width:14px;height:14px}}
</style>
