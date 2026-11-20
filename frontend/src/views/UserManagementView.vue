<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { AddOutline, CameraOutline, ChevronDownOutline, PauseCircleOutline, PeopleOutline, PersonOutline, PlayCircleOutline, SearchOutline, ShieldCheckmarkOutline, TrashOutline, BusinessOutline } from '@vicons/ionicons5'
import { ElButton, ElDialog, ElDropdown, ElDropdownItem, ElDropdownMenu, ElInput, ElMessage, ElOption, ElSelect, ElTable, ElTableColumn } from 'element-plus'
import { api } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

type Department={id:number;name:string;status:'active'|'disabled'}
type Account={id:number;display_name:string;email:string;status:'active'|'disabled';super_admin:boolean;departments:number[];has_avatar:boolean}
type Directory={departments:Department[];users:Account[]}
type AccountAction='active'|'disabled'|'delete'
const route=useRoute()
const router=useRouter()
const auth=useAuthStore()
const directory=ref<Directory>({departments:[],users:[]})
const loading=ref(false)
const error=ref('')
const search=ref('')
const filterDepartment=ref<number>()
const filterStatus=ref<Account['status']>()
const accountOpen=ref(false)
const saving=ref(false)
const saveError=ref('')
const avatarInput=ref<HTMLInputElement>()
const avatarPreview=ref('')
const avatarUrls=ref<Record<number,string>>({})
const account=reactive({display_name:'',email:'',phone:'',office:'',password:'',department_id:undefined as number|undefined,avatar_base64:''})
const actionDialog=ref(false)
const actionTarget=ref<{user:Account;action:AccountAction}>()
const actionError=ref('')
const changing=ref(false)
const actionTitles:Record<AccountAction,string>={active:'启用账号',disabled:'停用账号',delete:'删除账号'}
const activeDepartments=computed(()=>directory.value.departments.filter(item=>item.status==='active'))
const departmentsById=computed(()=>new Map(directory.value.departments.map(item=>[item.id,item])))
const activeCount=computed(()=>directory.value.users.filter(item=>item.status==='active').length)
const filtered=computed(()=>{
  const term=search.value.trim().toLocaleLowerCase()
  return directory.value.users.filter(user=>(!filterDepartment.value || user.departments.includes(filterDepartment.value))
    && (!filterStatus.value || user.status===filterStatus.value)
    && (!term || user.display_name.toLocaleLowerCase().includes(term) || user.email.toLocaleLowerCase().includes(term)))
})

watch(()=>route.query.department,value=>{
  filterDepartment.value=typeof value==='string' && /^\d+$/.test(value)?Number(value):undefined
},{immediate:true})
function changeDepartment(value:number|undefined) {
  void router.replace({name:'users',query:{department:value || undefined}})
}
async function load() {
  loading.value=true
  error.value=''
  try {
    directory.value=await api.get<Directory>('/admin/organization')
    const ids=new Set(directory.value.users.filter(user=>user.has_avatar).map(user=>user.id))
    for(const [key,url] of Object.entries(avatarUrls.value)) {
      if(!ids.has(Number(key))) {URL.revokeObjectURL(url);delete avatarUrls.value[Number(key)]}
    }
    await Promise.allSettled([...ids].filter(id=>!avatarUrls.value[id]).map(async id=>{
      const image=await api.get<Blob>('/admin/users/'+id+'/avatar')
      avatarUrls.value[id]=URL.createObjectURL(image)
    }))
  } catch(cause) {error.value=(cause as Error).message}
  finally {loading.value=false}
}
function openAccount() {
  Object.assign(account,{display_name:'',email:'',phone:'',office:'',password:'',avatar_base64:'',
    department_id:activeDepartments.value.some(dept=>dept.id===filterDepartment.value)?filterDepartment.value:undefined})
  if(avatarPreview.value) URL.revokeObjectURL(avatarPreview.value)
  avatarPreview.value=''
  saveError.value=''
  accountOpen.value=true
}
function closeAccount() {
  accountOpen.value=false
  account.password=''
  account.avatar_base64=''
  saveError.value=''
  if(avatarPreview.value) URL.revokeObjectURL(avatarPreview.value)
  avatarPreview.value=''
}
function pickAvatar(event:Event) {
  const input=event.target as HTMLInputElement
  const file=input.files?.[0]
  input.value=''
  if(!file) return
  if(!['image/jpeg','image/png','image/webp'].includes(file.type) || file.size>2*1024*1024) {
    saveError.value='请选择不超过 2 MB 的 JPG、PNG 或 WebP 头像。'
    return
  }
  saveError.value=''
  const reader=new FileReader()
  reader.onload=()=>{
    const result=String(reader.result || '')
    account.avatar_base64=result.split(',')[1] || ''
    if(avatarPreview.value) URL.revokeObjectURL(avatarPreview.value)
    avatarPreview.value=URL.createObjectURL(file)
  }
  reader.readAsDataURL(file)
}
async function saveAccount() {
  if(saving.value) return
  if(!account.display_name.trim() || !account.email.trim() || !account.password || !account.department_id ||
    !account.phone.trim() || !account.office.trim() || !account.avatar_base64) {
    saveError.value='请填写全部必填信息并上传头像。'
    return
  }
  saving.value=true
  saveError.value=''
  try {
    await api.post('/admin/users',{...account})
    closeAccount()
    ElMessage.success('部门 HR 账号已创建')
    await load()
  } catch(cause) {saveError.value=(cause as Error).message}
  finally {saving.value=false}
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
  changing.value=true
  actionError.value=''
  try {
    const path='/admin/users/'+user.id
    if(action==='delete') await api.delete(path)
    else await api.put(path+'/status',{status:action})
    actionDialog.value=false
    ElMessage.success(action==='delete'?'账号已删除':action==='active'?'账号已启用':'账号已停用')
    await load()
  } catch(cause) {actionError.value=(cause as Error).message}
  finally {changing.value=false}
}
onMounted(load)
onBeforeUnmount(()=>{
  if(avatarPreview.value) URL.revokeObjectURL(avatarPreview.value)
  Object.values(avatarUrls.value).forEach(url=>URL.revokeObjectURL(url))
})
</script>

<template>
  <main class="hire-page user-page">
    <header class="hire-heading user-heading">
      <div><h1>用户管理</h1><p>新百公司 / 部门账号与权限</p></div>
    </header>

    <section class="user-overview" aria-label="账号概览">
      <div class="company-mark"><BusinessOutline aria-hidden="true"/><div><strong>新百公司</strong><span>统一管理部门 HR 账号</span></div></div>
      <div class="overview-stat"><strong>{{ directory.users.length }}</strong><span>全部账号</span></div>
      <div class="overview-stat"><strong>{{ activeCount }}</strong><span>启用中</span></div>
      <div class="overview-stat"><strong>{{ directory.departments.length }}</strong><span>部门</span></div>
    </section>

    <p v-if="error" class="hire-error" role="alert">{{ error }} <button type="button" class="hire-inline-button" @click="load">重试</button></p>
    <section class="hire-surface account-directory" aria-label="账号列表">
      <header class="directory-head">
        <div><span class="section-eyebrow"><PeopleOutline aria-hidden="true"/>ACCOUNT DIRECTORY</span>
          <h2>账号列表 <small>{{ filtered.length }}</small></h2>
          <p>按部门查看账号，管理登录状态与权限归属。</p>
        </div>
        <ElButton type="primary" :icon="AddOutline" @click="openAccount">新增账号</ElButton>
      </header>
      <div class="directory-filters">
        <ElInput v-model="search" :prefix-icon="SearchOutline" aria-label="搜索账号" placeholder="搜索姓名或邮箱" clearable/>
        <ElSelect :model-value="filterDepartment" filterable clearable placeholder="全部部门" aria-label="按部门筛选账号" @change="changeDepartment">
          <ElOption v-for="dept in directory.departments" :key="dept.id" :value="dept.id" :label="dept.name"/>
        </ElSelect>
        <ElSelect v-model="filterStatus" clearable placeholder="全部状态" aria-label="按状态筛选账号">
          <ElOption value="active" label="已启用"/><ElOption value="disabled" label="已停用"/>
        </ElSelect>
      </div>
      <ElTable :data="filtered" row-key="id" :empty-text="loading?'正在加载账号…':'没有符合条件的账号'">
        <ElTableColumn label="账号" min-width="245"><template #default="{row}">
          <div class="account-identity">
            <span class="directory-avatar" :class="{'admin-avatar':row.super_admin}" aria-hidden="true">
              <img v-if="avatarUrls[row.id]" :src="avatarUrls[row.id]" alt="">
              <PersonOutline v-else/>
            </span>
            <div><strong>{{ row.display_name }}</strong><small>{{ row.email }}</small></div>
          </div>
        </template></ElTableColumn>
        <ElTableColumn label="角色" min-width="120"><template #default="{row}">
          <span class="user-tag" :class="{'admin-tag':row.super_admin}">{{ row.super_admin?'超级管理员':'部门 HR' }}</span>
        </template></ElTableColumn>
        <ElTableColumn label="所属部门" min-width="190"><template #default="{row}">
          <div v-if="row.departments.length" class="department-tags">
            <span v-for="id in row.departments" :key="id" class="user-tag">{{ departmentsById.get(id)?.name || '部门 #'+id }}</span>
          </div>
          <span v-else class="hire-muted">{{ row.super_admin?'全公司':'未分配' }}</span>
        </template></ElTableColumn>
        <ElTableColumn label="状态" width="112"><template #default="{row}">
          <span class="user-status" :class="{'active-status':row.status==='active'}">{{ row.status==='active'?'已启用':'已停用' }}</span>
        </template></ElTableColumn>
        <ElTableColumn label="" width="118" align="right"><template #default="{row}">
          <span v-if="row.id===auth.session?.userId" class="current-account">当前账号</span>
          <ElDropdown v-else trigger="click" @command="action=>openAction(row.id,action)">
            <button type="button" class="manage-account" :aria-label="'管理账号 '+row.display_name">管理<ChevronDownOutline aria-hidden="true"/></button>
            <template #dropdown><ElDropdownMenu>
              <ElDropdownItem v-if="row.status==='active'" command="disabled" class="account-menu-pause"><PauseCircleOutline aria-hidden="true"/>停用账号</ElDropdownItem>
              <ElDropdownItem v-else command="active" class="account-menu-enable"><PlayCircleOutline aria-hidden="true"/>启用账号</ElDropdownItem>
              <ElDropdownItem divided command="delete" class="account-menu-delete"><TrashOutline aria-hidden="true"/>删除账号</ElDropdownItem>
            </ElDropdownMenu></template>
          </ElDropdown>
        </template></ElTableColumn>
      </ElTable>
    </section>

    <ElDialog v-model="accountOpen" width="min(660px, 96vw)" align-center append-to-body
      :close-on-click-modal="!saving" :close-on-press-escape="!saving" :show-close="!saving"
      class="account-create-dialog" @closed="closeAccount">
      <template #header><div class="create-heading"><span class="create-icon"><AddOutline aria-hidden="true"/></span>
        <div><h2>新增部门 HR 账号</h2><p>填写登录与办公资料，创建后即可管理所属部门的招聘工作。</p></div>
      </div></template>
      <form class="account-create-form" @submit.prevent="saveAccount">
        <button type="button" class="avatar-upload" @click="avatarInput?.click()">
          <span class="avatar-preview"><img v-if="avatarPreview" :src="avatarPreview" alt="新账号头像预览"><CameraOutline v-else aria-hidden="true"/></span>
          <span><strong>{{ avatarPreview?'更换头像':'上传头像 *' }}</strong><small>JPG / PNG / WebP，最多 2 MB</small></span>
        </button>
        <input ref="avatarInput" type="file" accept="image/jpeg,image/png,image/webp" hidden @change="pickAvatar">
        <div class="account-fields">
          <label>姓名 <em>*</em><ElInput v-model="account.display_name" maxlength="80" placeholder="填写姓名" required/></label>
          <label>登录邮箱 <em>*</em><ElInput v-model="account.email" type="email" maxlength="160" placeholder="name@company.com" required/></label>
          <label>所属部门 <em>*</em>
            <ElSelect v-model="account.department_id" filterable placeholder="选择部门" aria-label="新账号所属部门">
              <ElOption v-for="dept in activeDepartments" :key="dept.id" :value="dept.id" :label="dept.name"/>
            </ElSelect>
          </label>
          <label>工作电话 <em>*</em><ElInput v-model="account.phone" maxlength="32" placeholder="填写联系电话" required/></label>
          <label>办公地点 <em>*</em><ElInput v-model="account.office" maxlength="80" placeholder="例如：南京总部 8 楼" required/></label>
          <label>初始密码 <em>*</em><ElInput v-model="account.password" type="password" autocomplete="new-password" minlength="8" maxlength="128" show-password placeholder="至少 8 位" required/></label>
        </div>
        <p v-if="saveError" class="hire-error" role="alert">{{ saveError }}</p>
        <footer><span>带 * 的项目为必填</span><div>
          <ElButton :disabled="saving" @click="closeAccount">取消</ElButton>
          <ElButton type="primary" native-type="submit" :loading="saving">创建账号</ElButton>
        </div></footer>
      </form>
    </ElDialog>

    <ElDialog v-model="actionDialog" :title="actionTarget ? actionTitles[actionTarget.action] : '管理账号'"
      width="min(440px, 94vw)" align-center append-to-body :close-on-click-modal="false"
      :close-on-press-escape="!changing" :show-close="!changing">
      <div v-if="actionTarget" class="account-confirm">
        <strong>{{ actionTarget.user.display_name }}</strong><span>{{ actionTarget.user.email }}</span>
        <p v-if="actionTarget.action==='delete'">删除后，该账号无法登录，且会从列表移除。历史跟进和审计记录会保留。</p>
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
.user-page{display:grid;align-content:start;gap:14px}
.user-heading{margin-bottom:2px}
.user-overview{display:grid;grid-template-columns:minmax(220px,1fr) repeat(3,minmax(110px,150px));gap:1px;overflow:hidden;border:1px solid var(--hire-line);border-radius:15px;background:var(--hire-line);box-shadow:var(--rs-shadow-sm)}
.user-overview>*{background:#fff;min-width:0}
.company-mark{display:flex;align-items:center;gap:13px;padding:17px 20px}
.company-mark>svg{width:37px;height:37px;padding:8px;border-radius:11px;background:#e7f5f1;color:var(--hire-accent-dark)}
.company-mark div{display:grid;gap:3px}.company-mark strong{font-size:15px;color:var(--hire-ink)}.company-mark span{font-size:11.5px;color:var(--hire-muted)}
.overview-stat{display:grid;align-content:center;justify-items:start;gap:3px;padding:13px 19px}.overview-stat strong{font-size:23px;color:#1e4750;font-variant-numeric:tabular-nums;line-height:1}.overview-stat span{font-size:11px;color:var(--hire-muted)}
.account-directory{overflow:hidden;min-width:0}
.directory-head{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:22px 23px 16px;border-bottom:1px solid var(--hire-line)}
.directory-head>div{min-width:0}.directory-head h2{display:flex;align-items:center;gap:8px;margin:5px 0 3px;font-size:19px;color:var(--hire-ink)}.directory-head h2 small{font-size:12px;border-radius:20px;padding:2px 8px;background:#e8f5f1;color:var(--hire-accent-dark)}
.directory-head p{font-size:12px;color:var(--hire-muted)}.directory-head .el-button{flex:none}
.section-eyebrow{display:inline-flex;align-items:center;gap:5px;color:var(--hire-accent-dark);font-size:10px;letter-spacing:.12em;font-weight:700}.section-eyebrow svg{width:13px;height:13px}
.directory-filters{display:flex;gap:9px;padding:14px 23px;background:#fbfdfd;border-bottom:1px solid var(--hire-line);flex-wrap:wrap}
.directory-filters :deep(.el-input){width:min(275px,100%)}.directory-filters :deep(.el-select){width:160px}
.account-directory :deep(.el-table){--el-table-border-color:var(--hire-line);--el-table-row-hover-bg-color:#f0f8f6;font-size:13px}
.account-directory :deep(th.el-table__cell){background:#f8fafb;color:#657b83;font-size:11.5px;font-weight:650}
.account-directory :deep(td.el-table__cell){padding-block:11px}
.account-identity{display:flex;align-items:center;gap:11px;min-width:0;text-align:left}.account-identity>div{min-width:0}.account-identity strong,.account-identity small{display:block;line-height:1.5;overflow-wrap:anywhere}.account-identity strong{font-size:13px;color:var(--hire-ink);font-weight:660}.account-identity small{font-size:11.5px;color:#647588}
.directory-avatar{display:grid;place-items:center;flex:none;width:39px;height:39px;overflow:hidden;border-radius:50%;background:#edf4f5;color:#5a8184}.directory-avatar.admin-avatar{background:#e6f5ef;color:#116858}.directory-avatar img{width:100%;height:100%;object-fit:cover}.directory-avatar svg{width:21px;height:21px}
.department-tags{display:flex;flex-wrap:wrap;gap:4px}.user-tag{display:inline-block;padding:3px 8px;border:1px solid #dce7e7;border-radius:6px;background:#f5f9f9;color:#48636b;font-size:11.5px;line-height:1.4}.user-tag.admin-tag{border-color:#eadfbe;background:#fbf5e6;color:#795516}
.user-status{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:20px;background:#f0f3f5;color:#657580;font-size:11.5px;white-space:nowrap}.user-status::before{content:'';width:7px;height:7px;border-radius:50%;background:#9aa9b0}.user-status.active-status{background:#eaf6ed;color:#17624b}.user-status.active-status::before{background:#3a9671}
.manage-account{display:inline-flex;align-items:center;gap:5px;padding:6px 10px;border:1px solid #d5e6e3;border-radius:8px;background:#fff;color:var(--hire-accent-dark);font:inherit;font-size:12px;cursor:pointer}.manage-account:hover{background:#eaf7f3;border-color:#9ccbc4}.manage-account svg{width:13px;height:13px}
.current-account{font-size:11.5px;color:#849398;white-space:nowrap}.account-confirm{display:grid;gap:4px;padding:2px 0 4px}.account-confirm strong{color:var(--hire-ink);font-size:15px}.account-confirm>span{color:#607286;font-size:13px;overflow-wrap:anywhere;padding-bottom:9px;border-bottom:1px solid var(--hire-line);margin-bottom:4px}.account-confirm>p{margin-top:8px;line-height:1.6}
@media(max-width:780px){.user-overview{grid-template-columns:repeat(3,1fr)}.company-mark{grid-column:1/-1}.directory-head{padding:16px}.directory-filters{padding:12px 16px}}
@media(max-width:520px){.directory-head{align-items:flex-start;flex-direction:column}.user-overview{grid-template-columns:repeat(3,minmax(0,1fr))}.overview-stat{padding:12px}}
</style>

<style>
.account-menu-pause{color:#a6721b!important}.account-menu-enable{color:#168064!important}.account-menu-delete{color:#b54650!important}
.account-menu-pause svg,.account-menu-enable svg,.account-menu-delete svg{width:16px;height:16px;margin-right:8px}
.account-create-dialog{border-radius:18px!important;box-shadow:0 26px 70px rgba(17,48,55,.19)!important}
.account-create-dialog .el-dialog__header{padding:23px 26px 12px!important;margin:0}.account-create-dialog .el-dialog__body{padding:8px 26px 23px!important}
.create-heading{display:flex;align-items:flex-start;gap:13px}.create-heading h2{font-size:19px;line-height:1.35;color:#1a3d45}.create-heading p{font-size:12px;color:#71878b;margin-top:5px;line-height:1.5}.create-icon{width:38px;height:38px;display:grid;place-items:center;flex:none;border-radius:11px;background:#e4f5f0;color:#087c70}.create-icon svg{width:21px;height:21px}
.account-create-form{display:grid;gap:16px}.avatar-upload{display:flex;align-items:center;gap:15px;width:100%;padding:15px;border:1.5px dashed #a9cecb;border-radius:13px;background:linear-gradient(135deg,#f6fbf9,#f9fbfd);font:inherit;text-align:left;color:#274a51;cursor:pointer;transition:border-color .2s,box-shadow .2s}.avatar-upload:hover{border-color:#0b8b7b;box-shadow:0 7px 20px rgba(13,137,126,.1)}.avatar-preview{display:grid;place-items:center;width:54px;height:54px;flex:none;overflow:hidden;border-radius:50%;background:#e4f4f0;color:#168678}.avatar-preview img{width:100%;height:100%;object-fit:cover}.avatar-preview svg{width:24px;height:24px}.avatar-upload>span:last-child{display:grid;gap:4px}.avatar-upload strong{font-size:13px}.avatar-upload small{font-size:11px;color:#819295}
.account-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px 16px}.account-fields label{display:grid;grid-template-columns:auto 1fr;align-items:center;gap:6px;min-width:0;color:#405961;font-size:12px;font-weight:600}.account-fields label em{color:#cf5a62;font-style:normal}.account-fields .el-input,.account-fields .el-select{grid-column:1/-1;width:100%}
.account-create-form footer{display:flex;justify-content:space-between;align-items:center;gap:12px;padding-top:15px;border-top:1px solid #e6eeee}.account-create-form footer>span{font-size:11px;color:#8a9b9f}.account-create-form footer>div{display:flex;gap:8px}
@media(max-width:580px){.account-fields{grid-template-columns:1fr}.account-create-dialog .el-dialog__header{padding:18px!important}.account-create-dialog .el-dialog__body{padding:8px 18px 18px!important}}
</style>
