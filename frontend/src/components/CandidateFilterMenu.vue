<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { ElButton, ElPopover } from 'element-plus'
import { FunnelOutline } from '@vicons/ionicons5'
import type { Job } from '@/api/hr'
import ResumeFilters, { filterSize, type FilterSelection } from './ResumeFilters.vue'

const props = defineProps<{ title:string; selected:FilterSelection; available:string[]; jobs:Job[];
  departments:{id:number;name:string}[]; requiresDepartment:boolean; capabilities:{code:string;name:string}[] }>()
const emit = defineEmits<{ apply:[filters:FilterSelection] }>()
const open = ref(false)
const button = ref<InstanceType<typeof ElButton>>()
const count = computed(() => filterSize(props.selected))

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
  <ElPopover v-model:visible="open" placement="bottom-end" trigger="click" :persistent="false"
    width="min(384px, calc(100vw - 22px))" :show-arrow="false" popper-class="candidate-filter-popover">
    <template #reference>
      <ElButton ref="button" :icon="FunnelOutline" :type="count ? 'primary' : 'default'" :plain="Boolean(count)"
        aria-haspopup="dialog" :aria-expanded="open">
        筛选 <span v-if="count" class="selection-count">{{ count }}</span>
      </ElButton>
    </template>
    <ResumeFilters v-if="open" v-bind="props" @apply="apply" @close="close" />
  </ElPopover>
</template>

<style scoped>
.selection-count {margin-inline-start:5px;min-width:17px;padding:1px 5px;border-radius:10px;background:var(--hire-accent);color:white;font-size:11px}
:global(.el-popper.candidate-filter-popover) {
  padding:0;height:min(590px,calc(100dvh - 156px));display:grid;min-height:180px;overflow:hidden;
  border:1px solid #d6e2e6;border-radius:14px;box-shadow:0 15px 45px rgba(33,55,68,.18)
}
</style>
