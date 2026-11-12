<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '@/api/client'
import { dateLabel } from '@/api/hr'
interface Event { id: number; action: string; actor: string | null; target_type: string; target_id: string; result: string; created_at: string }
const items = ref<Event[]>([])
const total = ref(0), offset = ref(0)
const error = ref('')
async function load(start = 0) {
  try {
    const result = await api.get<{ items: Event[]; total: number }>('/admin/audit?limit=25&offset=' + start)
    items.value = result.items; total.value = result.total; offset.value = start; error.value = ''
  } catch (e) { error.value = (e as Error).message }
}
onMounted(() => load())
</script>
<template>
  <main class="rs-page rs-workspace">
    <header class="rs-heading"><div><h1>审计日志</h1><p>访问、授权变更与招聘操作记录</p></div><button class="rs-button" @click="load(offset)">刷新日志</button></header>
    <p v-if="error" class="rs-error" role="alert">{{ error }}</p>
    <section class="rs-card rs-panel"><table class="rs-table"><thead><tr><th>时间</th><th>操作人</th><th>事件</th><th>对象</th><th>结果</th></tr></thead><tbody>
      <tr v-for="item in items" :key="item.id"><td>{{ dateLabel(item.created_at) }}</td><td>{{ item.actor || '系统 / 集成账号' }}</td><td>{{ item.action }}</td><td>{{ item.target_type }} · {{ item.target_id }}</td><td>{{ item.result }}</td></tr>
    </tbody></table><p v-if="!items.length" class="rs-empty">暂无日志。</p>
    <div class="rs-pagination"><span>{{ total }} 条</span><button class="rs-button" :disabled="!offset" @click="load(offset - 25)">上一页</button><button class="rs-button" :disabled="offset + 25 >= total" @click="load(offset + 25)">下一页</button></div></section>
  </main>
</template>
