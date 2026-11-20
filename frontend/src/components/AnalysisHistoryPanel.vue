<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
import { useRouter } from 'vue-router'
import { AddOutline, ArchiveOutline, ChevronBackOutline, ChevronForwardOutline, CreateOutline, EllipsisHorizontalOutline, PinOutline, TrashOutline, ArrowUndoOutline, SearchOutline } from '@vicons/ionicons5'
import { useAnalysisHistory, type AnalysisHistoryItem } from '@/stores/analysisHistory'

const props=defineProps<{currentId?:string; collapsed:boolean; mobileOpen:boolean}>()
const emit=defineEmits<{newChat:[]; toggle:[]}>()
const router=useRouter()
const history=useAnalysisHistory()
const search=ref('')
const menuId=ref('')
const renamingId=ref('')
const renameDraft=ref('')
const pendingDelete=ref('')
let deleteTimer:ReturnType<typeof setTimeout>|undefined
onBeforeUnmount(()=>{if(deleteTimer) clearTimeout(deleteTimer)})
const groups=computed(()=>{
  const term=search.value.trim().toLocaleLowerCase()
  const items=history.activeItems.filter(item=>!term || (item.title || item.question).toLocaleLowerCase().includes(term))
  const now=new Date()
  const today=new Date(now.getFullYear(),now.getMonth(),now.getDate()).getTime()
  const buckets=[
    {label:'今天',items:[] as AnalysisHistoryItem[]},
    {label:'昨天',items:[] as AnalysisHistoryItem[]},
    {label:'7 天内',items:[] as AnalysisHistoryItem[]},
    {label:'30 天内',items:[] as AnalysisHistoryItem[]},
    {label:'更早',items:[] as AnalysisHistoryItem[]},
  ]
  for(const item of items){
    const days=Math.floor((today-item.at)/86400000)
    buckets[days<=0?0:days===1?1:days<7?2:days<30?3:4].items.push(item)
  }
  return buckets.filter(group=>group.items.length)
})
function select(id:string) {
  menuId.value=''
  void router.push({name:'analysis',query:{h:id}})
}
function newChat() {
  menuId.value=''
  emit('newChat')
}
async function rename(item:AnalysisHistoryItem) {
  menuId.value=''
  renamingId.value=item.id
  renameDraft.value=item.title || item.question
  await nextTick()
  document.querySelector<HTMLInputElement>('.history-rename')?.focus()
}
function commitRename() {
  if(!renamingId.value) return
  history.rename(renamingId.value,renameDraft.value)
  renamingId.value=''
}
function remove(id:string) {
  if(pendingDelete.value!==id) {
    pendingDelete.value=id
    if(deleteTimer) clearTimeout(deleteTimer)
    deleteTimer=setTimeout(()=>{pendingDelete.value=''},3000)
    return
  }
  history.remove(id)
  menuId.value=''
  pendingDelete.value=''
  if(props.currentId===id) emit('newChat')
}
</script>

<template>
  <aside class="analysis-history" :class="{compact:collapsed}" aria-label="AI 分析历史对话">
    <header class="history-head">
      <div class="history-brand"><span class="brand-spark">✦</span><strong>AI 分析</strong></div>
      <button type="button" class="history-icon" :title="mobileOpen?'关闭历史对话':collapsed?'展开历史对话':'折叠历史对话'"
        :aria-label="mobileOpen?'关闭历史对话':collapsed?'展开历史对话':'折叠历史对话'"
        :aria-expanded="mobileOpen || !collapsed" aria-controls="analysis-history-content" @click="emit('toggle')">
        <ChevronForwardOutline v-if="collapsed && !mobileOpen" aria-hidden="true"/><ChevronBackOutline v-else aria-hidden="true"/>
      </button>
    </header>
    <button type="button" class="new-conversation" title="开启新对话" aria-label="开启新对话" @click="newChat"><AddOutline aria-hidden="true"/><span>开启新对话</span></button>
    <label class="history-search"><SearchOutline aria-hidden="true"/>
      <input v-model="search" type="search" placeholder="搜索对话标题" aria-label="搜索历史对话">
    </label>
    <div id="analysis-history-content" class="history-scroll">
      <p v-if="!groups.length" class="history-empty">{{ search?'没有匹配的对话':'暂无历史对话' }}</p>
      <section v-for="group in groups" :key="group.label" class="history-group">
        <h2>{{ group.label }}</h2>
        <div v-for="item in group.items" :key="item.id" class="history-row" :class="{selected:currentId===item.id}">
          <input v-if="renamingId===item.id" v-model="renameDraft" class="history-rename" maxlength="60"
            aria-label="重命名对话" @keydown.enter.prevent="commitRename" @keydown.esc="renamingId=''" @blur="commitRename">
          <button v-else type="button" class="history-item" :title="item.title || item.question"
            :aria-current="currentId===item.id?'true':undefined" @click="select(item.id)">
            <PinOutline v-if="item.pinned" aria-hidden="true"/><span>{{ item.title || item.question }}</span>
          </button>
          <button type="button" class="history-more" :aria-label="'管理对话 '+(item.title || item.question)"
            :aria-expanded="menuId===item.id" @click.stop="menuId=menuId===item.id?'':item.id"><EllipsisHorizontalOutline aria-hidden="true"/></button>
          <div v-if="menuId===item.id" class="history-menu">
            <button type="button" @click="history.togglePin(item.id);menuId=''"><PinOutline aria-hidden="true"/>{{ item.pinned?'取消置顶':'置顶' }}</button>
            <button type="button" @click="rename(item)"><CreateOutline aria-hidden="true"/>重命名</button>
            <button type="button" @click="history.archive(item.id);menuId=''"><ArchiveOutline aria-hidden="true"/>归档</button>
            <button type="button" class="danger" @click="remove(item.id)"><TrashOutline aria-hidden="true"/>{{ pendingDelete===item.id?'确认删除':'删除' }}</button>
          </div>
        </div>
      </section>
      <section v-if="history.archived.length" class="history-group archived-group">
        <h2>已归档</h2>
        <div v-for="item in history.archived" :key="item.id" class="history-row" :class="{selected:currentId===item.id}">
          <button type="button" class="history-item" :title="item.title || item.question" @click="select(item.id)"><span>{{ item.title || item.question }}</span></button>
          <button type="button" class="history-more" :aria-label="'管理对话 '+(item.title || item.question)" @click.stop="menuId=menuId===item.id?'':item.id"><EllipsisHorizontalOutline aria-hidden="true"/></button>
          <div v-if="menuId===item.id" class="history-menu">
            <button type="button" @click="history.unarchive(item.id);menuId=''"><ArrowUndoOutline aria-hidden="true"/>恢复</button>
            <button type="button" class="danger" @click="remove(item.id)"><TrashOutline aria-hidden="true"/>{{ pendingDelete===item.id?'确认删除':'删除' }}</button>
          </div>
        </div>
      </section>
    </div>
  </aside>
</template>

<style scoped>
.analysis-history{display:grid;grid-template-rows:auto auto auto minmax(0,1fr);gap:9px;min-width:0;min-height:0;background:#f7f9f9;border-right:1px solid #e4ebec;color:#344e55}
.history-head{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:20px 18px 9px}.history-brand{display:flex;align-items:center;gap:8px;color:#0d897e;font-size:19px;letter-spacing:-.03em}.brand-spark{font-size:23px;line-height:1}
.history-icon{display:grid;place-items:center;width:28px;height:28px;border:0;border-radius:7px;background:transparent;color:#7e9196;cursor:pointer}.history-icon svg{width:17px;height:17px}.history-icon:hover{background:#e6f0ee;color:#087f73}
.new-conversation{display:flex;align-items:center;justify-content:center;gap:7px;margin:0 12px;padding:10px;border:1px solid #e4ebec;border-radius:24px;background:#fff;color:#29434a;box-shadow:0 2px 6px rgba(26,66,71,.05);font:inherit;font-size:13px;cursor:pointer}.new-conversation:hover{border-color:#9bcfc6;color:#087e72}.new-conversation svg{width:15px;height:15px}
.history-search{display:flex;align-items:center;gap:7px;margin:0 12px;padding:6px 10px;border:1px solid transparent;border-radius:8px;color:#8da0a3}.history-search:focus-within{border-color:#cae2dd;background:#fff}.history-search svg{width:13px;height:13px;flex:none}.history-search input{width:100%;min-width:0;border:0;outline:0;background:transparent;color:#334f57;font:inherit;font-size:11.5px}.history-search input::placeholder{color:#9aa9ac}
@media(min-width:761px){
  .compact{grid-template-rows:auto auto;align-content:start;justify-items:center}
  .compact .history-head{padding:14px 0 2px}
  .compact .history-brand,.compact .history-search,.compact .history-scroll,.compact .new-conversation span{display:none}
  .compact .new-conversation{margin:0;padding:8px}
}
.history-scroll{min-height:0;overflow:auto;padding:0 9px 24px;scrollbar-width:thin}.history-group{margin-top:17px}.history-group h2{padding:0 11px 7px;color:#8b9b9e;font-size:11px;font-weight:600}.history-row{position:relative;display:flex;align-items:center;border-radius:9px;min-width:0}.history-row:hover{background:#edf3f3}.history-row.selected{background:#e2f1ee;color:#087c70}.history-item{display:flex;align-items:center;gap:5px;min-width:0;flex:1;padding:9px 9px 9px 11px;border:0;background:transparent;color:inherit;text-align:left;font:inherit;font-size:12.5px;cursor:pointer}.history-item span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.history-item svg{width:12px;height:12px;flex:none}.history-more{display:grid;place-items:center;width:27px;height:27px;flex:none;margin-right:3px;border:0;border-radius:6px;background:transparent;color:#839a9b;opacity:0;cursor:pointer}.history-row:hover .history-more,.history-row.selected .history-more,.history-more:focus-visible{opacity:1}.history-more svg{width:16px;height:16px}.history-menu{position:absolute;z-index:8;top:31px;right:2px;display:grid;min-width:126px;padding:4px;border:1px solid #dce7e6;border-radius:9px;background:white;box-shadow:0 12px 30px rgba(20,54,60,.15)}.history-menu button{display:flex;align-items:center;gap:8px;padding:7px 8px;border:0;border-radius:5px;background:transparent;color:#40595e;text-align:left;font:inherit;font-size:11.5px;cursor:pointer}.history-menu button:hover{background:#f0f7f5}.history-menu button.danger{color:#b54751}.history-menu svg{width:14px;height:14px}.history-rename{width:100%;padding:7px 8px;border:1px solid #a7d2ca;border-radius:6px;outline:0;font:inherit;font-size:12px}.history-empty{padding:16px 12px;color:#98a8aa;font-size:12px}.archived-group{padding-top:11px;border-top:1px solid #e4ebec}
</style>
