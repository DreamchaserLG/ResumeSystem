<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ElButton, ElInput, ElPopover } from 'element-plus'
import { FunnelOutline, SearchOutline } from '@vicons/ionicons5'
import type { Job } from '@/api/hr'
import ResumeFilters, { filterSize, type FilterSelection } from './ResumeFilters.vue'

const props = defineProps<{ title:string; selected:FilterSelection; available:string[]; jobs:Job[];
  departments:{id:number;name:string}[]; requiresDepartment:boolean; searchQuery:string }>()
const emit = defineEmits<{ apply:[filters:FilterSelection]; search:[query:string] }>()
const open = ref(false)
const button = ref<InstanceType<typeof ElButton>>()
const count = computed(() => filterSize(props.selected))
const searchDraft = ref(props.searchQuery)
let searchTimer: ReturnType<typeof setTimeout> | undefined

function submitSearch() {
  clearTimeout(searchTimer)
  const query = searchDraft.value.trim()
  if (query !== props.searchQuery) emit('search', query)
}
watch(searchDraft, () => {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(submitSearch, 350)
}, { flush:'sync' })
onBeforeUnmount(() => clearTimeout(searchTimer))

function close() {
  open.value = false
  void nextTick(() => button.value?.$el.focus())
}
function apply(filters:FilterSelection) {
  emit('apply', filters)
  close()
}
</script>

<template>
  <div class="candidate-filter-tools">
    <ElInput v-model="searchDraft" class="candidate-search" placeholder="搜索姓名或岗位" aria-label="搜索姓名或岗位"
      :prefix-icon="SearchOutline" :maxlength="80" clearable @keyup.enter="submitSearch" @clear="submitSearch"/>
    <ElPopover v-model:visible="open" placement="bottom-end" trigger="click" :persistent="false"
      width="min(384px, calc(100vw - 22px))" :show-arrow="false" popper-class="candidate-filter-popover">
      <template #reference>
        <ElButton ref="button" class="candidate-filter-button" :icon="FunnelOutline"
          aria-haspopup="dialog" :aria-expanded="open">
          筛选 <span v-if="count" class="selection-count">{{ count }}</span>
        </ElButton>
      </template>
      <ResumeFilters v-if="open" :title="title" :selected="selected" :available="available"
        :jobs="jobs" :departments="departments" :requires-department="requiresDepartment" @apply="apply" @close="close"/>
    </ElPopover>
  </div>
</template>

<style scoped>
.candidate-filter-tools {display:flex;align-items:center;justify-content:flex-end;gap:6px;flex:1;min-width:0;margin-inline-start:auto}
.candidate-search {width:158px;min-width:0}
.candidate-search :deep(.el-input__wrapper) {padding-inline:7px}
.candidate-filter-button {flex:none;padding-inline:8px}
.selection-count {margin-inline-start:5px;min-width:17px;padding:1px 5px;border-radius:10px;background:var(--hire-accent);color:white;font-size:11px}
:global(.el-popper.candidate-filter-popover) {
  padding:0;height:min(590px,calc(100dvh - 156px));display:grid;min-height:180px;overflow:hidden;
  border:1px solid #d6e2e6;border-radius:14px;box-shadow:0 15px 45px rgba(33,55,68,.18)
}
</style>
