<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '@/api/client'
import { type Application, type Page } from '@/api/hr'

const rows = ref<Application[]>([])
const error = ref('')
const offset = ref(0)
const more = ref(false)
async function load(start = 0) {
  error.value = ''
  try {
    const page = await api.get<Page<Application>>('/hr/review?offset=' + start)
    rows.value = page.items
    more.value = page.has_more
    offset.value = start
  } catch (e) { error.value = (e as Error).message }
}
onMounted(() => load())
</script>
<template>
  <main class="rs-page rs-workspace">
    <header class="rs-heading"><div><h1>复核队列</h1><p>核对低置信、冲突或缺少证据的字段</p></div><button class="rs-button" @click="load(offset)">刷新队列</button></header>
    <p v-if="error" class="rs-error" role="alert">{{ error }}</p>
    <section class="rs-card rs-panel">
      <table class="rs-table"><thead><tr><th>候选人</th><th>岗位</th><th>处理</th></tr></thead><tbody>
        <tr v-for="row in rows" :key="row.id"><td>{{ row.name || '申请 #' + row.id }}</td><td>{{ row.job_title }}</td>
          <td><RouterLink :to="'/applications/' + row.id + '#review'">核对原文</RouterLink></td></tr>
      </tbody></table>
      <p v-if="!rows.length && !error" class="rs-empty">当前没有待复核申请。</p>
      <div class="rs-pagination"><button class="rs-button" :disabled="!offset" @click="load(offset - 25)">上一页</button><button class="rs-button" :disabled="!more" @click="load(offset + 25)">下一页</button></div>
    </section>
  </main>
</template>
