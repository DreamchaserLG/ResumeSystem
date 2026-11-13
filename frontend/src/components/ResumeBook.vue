<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '@/api/client'
import type { ResumeCard } from '@/api/hr'

const props = defineProps<{ resume: ResumeCard }>()
const emit = defineEmits<{ close: []; pageRatio: [ratio: number] }>()
const current = ref(1), target = ref(1)
const active = ref(''), nextImage = ref(''), error = ref('')
const frontRatio = ref(Math.SQRT1_2), backRatio = ref(Math.SQRT1_2)
function readRatio(event: Event, back = false) {
  const image = event.currentTarget as HTMLImageElement
  if (!image.naturalHeight) return
  const ratio = image.naturalWidth / image.naturalHeight
  if (back) backRatio.value = ratio
  else {
    frontRatio.value = ratio
    emit('pageRatio', ratio)
  }
}
const turning = ref(false), backwards = ref(false), loading = ref(false)
const images = new Map<number, string>()
let generation = 0
let timer: ReturnType<typeof setTimeout> | undefined
const count = computed(() => props.resume.page_count)
const reduced = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches

function clear() {
  generation++
  if (timer) clearTimeout(timer)
  images.forEach(url => URL.revokeObjectURL(url))
  images.clear()
  active.value = ''; nextImage.value = ''; error.value = ''; turning.value = false; loading.value = false
}
async function getPage(page: number, epoch: number) {
  const cached = images.get(page)
  if (cached) return cached
  const { id, version_id } = props.resume
  const blob = await api.get<Blob>(`/hr/applications/${id}/preview?page=${page}&version=${version_id}`)
  if (epoch !== generation) return ''
  const url = URL.createObjectURL(blob)
  images.set(page, url)
  return url
}
watch(() => [props.resume.id, props.resume.version_id, count.value], async () => {
  clear(); current.value = 1; target.value = 1
  if (!count.value || !props.resume.version_id) return
  const epoch = generation
  loading.value = true
  try { active.value = await getPage(1, epoch) }
  catch (e) { if (epoch === generation) error.value = (e as Error).message }
  finally { if (epoch === generation) loading.value = false }
}, { immediate: true })
onBeforeUnmount(clear)

function finish() {
  if (!nextImage.value) return
  if (timer) clearTimeout(timer)
  active.value = nextImage.value
  frontRatio.value = backRatio.value
  emit('pageRatio', frontRatio.value)
  current.value = target.value
  turning.value = false
  nextImage.value = ''
}
async function flip(page: number) {
  if (loading.value || turning.value || page < 1 || page > count.value || page === current.value || !active.value) return
  const epoch = generation
  error.value = ''; loading.value = true
  try {
    const url = await getPage(page, epoch)
    if (!url || epoch !== generation) return
    target.value = page
    backwards.value = page < current.value
    backRatio.value = frontRatio.value
    nextImage.value = url
    if (reduced()) finish()
    else {
      turning.value = true
      timer = setTimeout(finish, 560)
    }
  } catch (e) { if (epoch === generation) error.value = (e as Error).message }
  finally { if (epoch === generation) loading.value = false }
}
function turnPage() { return flip(current.value < count.value ? current.value + 1 : 1) }
</script>

<template>
  <aside class="resume-reader" aria-label="简历预览">
    <header>
      <div><span class="reader-kicker">简历预览</span><h2>{{ resume.name || '申请 #' + resume.id }}</h2></div>
      <div class="reader-actions"><RouterLink :to="'/applications/' + resume.id">申请详情</RouterLink>
        <button type="button" aria-label="关闭预览" class="reader-close" @click="emit('close')">×</button></div>
    </header>
    <div class="reader-body">
      <button v-if="active" type="button" class="paper" :class="{ turning, backwards }"
        :disabled="count < 2 || loading || turning" :aria-label="count > 1 ? '翻到下一页' : '简历第一页'" @click="turnPage">
        <span class="paper-rotation" @animationend.self="finish">
          <img class="paper-face front" :style="{ '--face-ratio': frontRatio }" :src="active" @load="readRatio($event)" :alt="'简历第 ' + current + ' 页'">
          <img v-if="nextImage" class="paper-face back" :style="{ '--face-ratio': backRatio }" :src="nextImage" alt="" @load="readRatio($event, true)">
        </span>
      </button>
      <p v-else class="reader-empty" role="status">{{ loading ? '正在载入简历…' : error || (resume.parse_status === 'parsing' ? '简历解析中' : '暂未生成预览，可在申请详情重新解析。') }}</p>
    </div>
    <p v-if="active && error" class="reader-error" role="alert">{{ error }}</p>
    <footer v-if="count">
      <button class="rs-button" type="button" :disabled="current <= 1 || turning || loading" @click="flip(current - 1)">‹ 上一页</button>
      <span aria-live="polite">{{ current }} / {{ count }}<small v-if="count > 1"> · 点击简历翻页</small></span>
      <button class="rs-button" type="button" :disabled="current >= count || turning || loading" @click="flip(current + 1)">下一页 ›</button>
    </footer>
  </aside>
</template>

<style scoped>
.resume-reader { display:grid; grid-template-rows:auto minmax(0,1fr) auto auto; min-width:0; min-height:0; background:#edf1f8; border:1px solid #dde5f1; border-radius:20px; overflow:hidden; box-shadow:0 12px 30px #162d5b0f; }
header, footer { display:flex; justify-content:space-between; align-items:center; gap:10px; padding:12px 15px; }
header { background:#fff; border-bottom:1px solid #e4ebf5; }
header h2 { margin:1px 0 0; font-size:16px; }
.reader-kicker { color:#65748f; font-size:11px; font-weight:700; }
.reader-actions { display:flex; align-items:center; gap:12px; white-space:nowrap; font-size:13px; }
.reader-close { border:none; border-radius:9px; width:33px; height:33px; font-size:22px; background:#f0f3fa; color:#334567; cursor:pointer; }
.reader-body { display:grid; place-items:center; min-height:0; padding:12px; overflow:hidden; }
.paper { border:0; background:none; color:inherit; padding:0; width:100%; height:100%; min-height:0; min-width:0; container-type:size; perspective:1700px; cursor:pointer; }
.paper:disabled { cursor:default; }
.paper-rotation { position:relative; display:block; width:100%; height:100%; transform-style:preserve-3d; }
.paper.turning .paper-rotation { animation:page-next .48s cubic-bezier(.24,.7,.38,1) forwards; }
.paper.turning.backwards .paper-rotation { animation-name:page-prev; }
.paper-face { position:absolute; inset:0; margin:auto; width:min(100cqw, calc(100cqh * var(--face-ratio))); height:auto; aspect-ratio:var(--face-ratio); display:block; object-fit:contain; border-radius:9px; box-shadow:0 7px 22px #1d34582c; backface-visibility:hidden; background:white; }
.paper-face.back { transform:rotateY(180deg); }
.reader-empty,.reader-error { color:#64748b; margin:0 15px; text-align:center; font-size:13px; }
.reader-error { color:#a02626; }
footer { padding-top:4px; font-size:13px; color:#475569; }
footer small { color:#64748b; }
@media(max-width:540px) { footer small { display:none; } }
@keyframes page-next { to { transform:rotateY(-180deg); } }
@keyframes page-prev { to { transform:rotateY(180deg); } }
@media(prefers-reduced-motion:reduce) { .paper.turning .paper-rotation { animation-duration:.01ms; } }
</style>
