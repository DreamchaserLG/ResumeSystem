<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { CloudUploadOutline } from '@vicons/ionicons5'
import { ElButton, ElDialog, ElSelect, ElOption, ElProgress, ElTag } from 'element-plus'
import { api } from '@/api/client'
import type { Job } from '@/api/hr'
import FilePickerZone from './FilePickerZone.vue'
type State = 'ready'|'sending'|'parsing'|'published'|'failed'|'unsupported'
type BatchItem = {key:string; file:File; path:string; state:State; message?:string; id?:number;version?:number}
const props=defineProps<{modelValue:boolean; mode:'single'|'batch'; jobs:Job[]; preferredJob?:number}>()
const emit=defineEmits<{ 'update:modelValue':[value:boolean]; updated:[job:number]; uploading:[value:boolean]; view:[job:number,id:number] }>()
const job=ref<number>()
const items=ref<BatchItem[]>([])
const active=ref(false)
const folder=ref<HTMLInputElement>()
const files=ref<HTMLInputElement>()
const fileError=ref('')
const priorApplication=ref('')
const single=computed(()=>props.mode==='single')
const supported=/\.(pdf|docx?|png|jpe?g|webp)$/i
const labels:Record<State,string>={ready:'待导入',sending:'上传中',parsing:'等待解析',published:'已完成',failed:'失败',unsupported:'已跳过'}
const final=computed(()=>items.value.filter(row=>['published','failed','unsupported'].includes(row.state)).length)
const ready=computed(()=>items.value.filter(row=>row.state==='ready').length)
const openJobs=computed(()=>props.jobs.filter(row=>row.status==='open' && row.manageable))
const activeJob=computed(()=>openJobs.value.find(row=>row.id===job.value))
let poller:ReturnType<typeof setTimeout>|undefined
let disposed=false
let nextScan=0
watch([()=>props.modelValue,openJobs],([open,jobs])=>{
  if (open && !active.value && !items.value.some(row=>row.id) && !jobs.some(row=>row.id===job.value))
    job.value=jobs.find(row=>row.id===props.preferredJob)?.id ?? jobs[0]?.id
})
function pick(selection:File[]) {
  fileError.value=''
  if(!selection.length) return
  if(single.value && selection.length!==1){fileError.value='请选择一份简历。';return}
  if(selection.length>500){fileError.value='一次最多选择 500 个文件。请拆分文件夹后重试。';return}
  items.value=selection.map(file=>{
    const valid=supported.test(file.name) && file.size>0 && file.size<=20*1024*1024
    return {file,path:file.webkitRelativePath || file.name,key:crypto.randomUUID(),state:valid?'ready':'unsupported',
      message:valid?undefined:!supported.test(file.name)?'格式不支持':'文件为空或超过 20 MB'}
  })
}
function pickInput(input:HTMLInputElement) {
  pick(Array.from(input.files ?? []))
  input.value=''
}
async function submit() {
  const selected=job.value
  if(disposed || !selected || active.value || !ready.value) return
  const priorId=single.value ? priorApplication.value.trim() : ''
  if(priorId && (!/^\d+$/.test(priorId) || !Number.isSafeInteger(Number(priorId)) || Number(priorId)<1)){
    fileError.value='请输入有效的关联申请编号。'
    return
  }
  fileError.value=''
  active.value=true
  emit('uploading',true)
  const queue=items.value.filter(row=>row.state==='ready')
  let index=0
  async function uploadNext() {
    while(!disposed && index<queue.length) {
      const row=queue[index++]
      row.state='sending';row.message=undefined
      const body = new FormData()
      body.append('file',row.file)
      try {
        const target=new URLSearchParams({job_id:String(selected),import_request:row.key})
        if(priorId) target.set('candidate_application_id',priorId)
        const result=await api.upload<{application_id:number;version_id:number;status:string}>('/applications?'+target,body)
        row.id=result.application_id
        row.version=result.version_id
        row.state=result.status==='published'?'published':result.status==='failed'?'failed':'parsing'
      } catch(error) {
        row.state='failed';row.message=(error as Error).message
      }
    }
  }
  await Promise.all(Array.from({length:Math.min(3,queue.length)},uploadNext))
  active.value=false
  emit('uploading',false)
  if(!disposed) {
    emit('updated',selected)
    if(!poller && items.value.some(row=>row.state==='parsing')) poller=setTimeout(poll,3000)
  }
}
async function poll() {
  poller=undefined
  if(disposed) return
  const pending=items.value.filter(row=>row.state==='parsing')
  if(!pending.length) return
  const start=nextScan%pending.length
  const batch=Array.from({length:Math.min(20,pending.length)},(_,index)=>pending[(start+index)%pending.length])
  nextScan=start+batch.length
  let changed=false
  await Promise.all(batch.map(async row=>{
    try {
      const data=await api.get<{status:string;fail_reason:string|null}>('/applications/'+row.id+'/resumes/'+row.version)
      if(data.status==='published' || data.status==='failed'){
        row.state=data.status
        row.message=data.fail_reason || undefined
        changed=true
      }
    } catch { /* Leave the server-side parse in progress after transient network errors. */ }
  }))
  if(changed && job.value && !disposed) emit('updated',job.value)
  if(!disposed && !poller && items.value.some(row=>row.state==='parsing')) poller=setTimeout(poll,4200)
}
function view(row:BatchItem) {
  if(!row.id || !job.value) return
  emit('update:modelValue',false)
  emit('view',job.value,row.id)
}
function retry(row:BatchItem) {
  if(active.value || row.state!=='failed' || row.id) return
  row.state='ready'
}
const locked=computed(()=>active.value || items.value.some(row=>row.state==='parsing'))
onBeforeUnmount(()=>{disposed=true;if(poller)clearTimeout(poller)})
</script>
<template>
  <ElDialog :model-value="modelValue" :title="single?'单份导入简历':'批量导入简历'" width="min(640px, 95vw)" append-to-body align-center
    :close-on-click-modal="false" @update:model-value="emit('update:modelValue',$event)">
    <div class="batch-controls">
      <label>导入岗位 <ElSelect v-model="job" placeholder="选择招聘中岗位" aria-label="导入岗位"
        :disabled="locked || items.some(row=>row.id && row.state!=='unsupported')">
        <ElOption v-for="row in openJobs" :key="row.id" :value="row.id" :label="row.title"/>
      </ElSelect></label>
      <FilePickerZone v-if="single" :title="items.length?'更换文件':'选择或拖入一份简历'"
        hint="PDF、Word、图片，单份不超过 20 MB" :disabled="locked" @browse="files?.click()" @drop="pick"/>
      <FilePickerZone v-else folder title="选择文件夹" hint="同时读取文件夹及子文件夹中的简历"
        :disabled="locked" @browse="folder?.click()"/>
      <input v-if="!single" ref="folder" type="file" webkitdirectory multiple hidden @change="pickInput($event.target as HTMLInputElement)">
      <input ref="files" type="file" accept=".pdf,.doc,.docx,.png,.jpg,.jpeg,.webp" :multiple="!single" hidden
        @change="pickInput($event.target as HTMLInputElement)">
      <div v-if="!single" class="batch-help"><span>PDF、Word、图片，单份不超过 20 MB</span>
        <ElButton text type="primary" :disabled="locked" @click="files?.click()">也可选择多个文件</ElButton></div>
      <details v-if="single" class="link-application">
        <summary>关联已有申请（可选）</summary>
        <label>申请编号 <input v-model="priorApplication" type="text" inputmode="numeric" :disabled="locked || items.some(row=>!!row.id)"
          placeholder="同一候选人已有的申请编号"></label>
      </details>
      <p v-if="fileError" class="hire-error" role="alert">{{ fileError }}</p>
      <p v-if="!openJobs.length" class="hire-muted">当前无可导入的招聘中岗位。</p>
      <template v-if="items.length">
        <div v-if="!single" class="batch-progress"><span>已处理 {{ final }} / {{ items.length }}</span><ElProgress :percentage="Math.round(final/items.length*100)" /></div>
        <ul class="batch-list">
          <li v-for="row in items" :key="row.key">
            <div><strong :title="row.path">{{ row.path }}</strong><small v-if="row.message">{{ row.message }}</small></div>
            <ElTag size="small" :type="row.state==='failed'?'danger':row.state==='published'?'success':'info'">{{ labels[row.state] }}</ElTag>
            <ElButton v-if="single && row.id" text type="primary" @click="view(row)">查看简历</ElButton>
            <ElButton v-else-if="row.state==='failed' && !row.id" text type="primary" @click="retry(row)">重试</ElButton>
          </li>
        </ul>
      </template>
    </div>
    <template #footer><ElButton @click="emit('update:modelValue',false)">{{ locked?'在后台处理':'关闭' }}</ElButton>
      <ElButton type="primary" :icon="CloudUploadOutline" :loading="active" :disabled="!activeJob || !ready" @click="submit">导入{{ ready?' '+ready+' 份':'' }}</ElButton></template>
  </ElDialog>
</template>
<style scoped>
.batch-controls {display:grid;gap:12px}.batch-controls label {display:grid;gap:6px}
.link-application {font-size:13px;color:var(--hire-muted)}
.link-application summary {cursor:pointer}
.link-application label {margin-top:8px}
.link-application input {padding:8px 10px;border:1px solid var(--rs-border-strong);border-radius:7px;color:var(--hire-ink);transition:border-color var(--rs-motion-fast),box-shadow var(--rs-motion-fast)}
.link-application input:focus {outline:0;border-color:var(--hire-accent);box-shadow:0 0 0 3px rgba(13,137,126,.1)}
.batch-help,.batch-progress {display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:12px;color:var(--hire-muted)}
.batch-progress :deep(.el-progress) {width:45%}
.batch-list {max-height:210px;overflow:auto;list-style:none; border:1px solid var(--hire-line);border-radius:9px;background:#fbfdfd}
.batch-list li {display:flex;align-items:center;gap:8px;padding:9px 11px;border-bottom:1px solid var(--hire-line);transition:background-color var(--rs-motion-fast)}
.batch-list li:hover {background:var(--rs-primary-softer)}
.batch-list li:last-child{border-bottom:0}
.batch-list li>div {flex:1;min-width:0}.batch-list strong,.batch-list small{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.batch-list small{color:#926166}
</style>
