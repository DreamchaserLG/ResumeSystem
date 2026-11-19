<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ExpandOutline, ContractOutline } from '@vicons/ionicons5'
import { api } from '@/api/client'
import type { ResumeCard } from '@/api/hr'

const props = defineProps<{ resume: ResumeCard; avatarUrl?: string; embedded?: boolean }>()
const emit = defineEmits<{ close: []; pageRatio: [ratio: number]; fullscreenChange: [active: boolean] }>()
const fullscreen = ref(false)
const reader = ref<HTMLElement>()
const readerBody = ref<HTMLElement>()
const fullscreenButton = ref<HTMLButtonElement>()
/* 嵌入式默认按宽度铺排（比“适合整页”更易读），全屏仍默认整页可见。 */
const fitPage = ref(!props.embedded)
const zoom = ref(1)
const widthFit = computed(()=>(props.embedded || fullscreen.value) && !fitPage.value)
const paperStyle = computed(()=>({
  '--stage-ratio':frontRatio.value,
  ...(widthFit.value ? { width:`${Math.round(zoom.value*100)}%` } : {}),
}))
function setFullscreen(value: boolean) {
  if (value) { fitPage.value=true; zoom.value=1 }
  fullscreen.value = value
  emit('fullscreenChange', value)
}
function toggleFit() { fitPage.value=!fitPage.value; zoom.value=1 }
function zoomBy(step:number) {
  fitPage.value=false
  zoom.value=Math.min(1.8,Math.max(.6,Math.round((zoom.value+step)*10)/10))
}
watch(fullscreen, (value) => {
  if (value && reader.value instanceof HTMLDialogElement) reader.value.showModal()
  fullscreenButton.value?.focus()
}, { flush: 'post' })
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
function imageFailed(back=false) {
  error.value='该页预览未能完整生成，请重新解析简历。'
  if (back) nextImage.value=''
  else active.value=''
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
  fitPage.value=true; zoom.value=1
  if (!count.value || !props.resume.version_id) return
  const epoch = generation
  loading.value = true
  try { active.value = await getPage(1, epoch) }
  catch (e) { if (epoch === generation) error.value = (e as Error).message }
  finally { if (epoch === generation) loading.value = false }
}, { immediate: true })
watch([current, widthFit, ()=>props.resume.id],()=>readerBody.value?.scrollTo({top:0}),{flush:'post'})
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
  <component :is="fullscreen ? 'dialog' : 'aside'" ref="reader"
    class="resume-reader" :class="{ 'resume-fullscreen': fullscreen, 'reader-width-fit':widthFit,
      'reader-inline':embedded && !fullscreen }" aria-label="简历预览" @cancel.prevent="setFullscreen(false)">
    <header>
      <div v-if="embedded && !fullscreen" class="reader-identity"><h2>简历原文</h2></div>
      <div v-else class="reader-identity">
        <img v-if="avatarUrl" class="reader-avatar" :src="avatarUrl" alt="">
        <div><span class="reader-kicker">简历预览</span><h2>{{ resume.name || '申请 #' + resume.id }}</h2></div>
      </div>
      <div class="reader-actions"><RouterLink v-if="!embedded" :to="'/applications/' + resume.id">申请详情</RouterLink>
        <div v-if="active" class="reader-zoom" aria-label="简历缩放">
          <button type="button" aria-label="缩小简历" :disabled="zoom<=.6" @click="zoomBy(-.1)">−</button>
          <span>{{ Math.round(zoom*100) }}%</span>
          <button type="button" aria-label="放大简历" :disabled="zoom>=1.8" @click="zoomBy(.1)">+</button>
        </div>
        <button v-if="embedded && !fullscreen && active" type="button" class="reader-fit" :aria-pressed="fitPage"
          @click="toggleFit">{{ fitPage?'适合宽度':'适合整页' }}</button>
        <button ref="fullscreenButton" type="button" class="reader-close reader-expand" :aria-label="fullscreen ? '退出全屏' : '全屏预览'"
          :title="fullscreen ? '退出全屏' : '全屏预览'" @click="setFullscreen(!fullscreen)">
          <ContractOutline v-if="fullscreen" aria-hidden="true"/><ExpandOutline v-else aria-hidden="true"/>
        </button>
        <button v-if="!embedded || fullscreen" type="button" aria-label="关闭预览" class="reader-close" @click="fullscreen ? setFullscreen(false) : emit('close')">×</button></div>
    </header>
    <div ref="readerBody" class="reader-body">
      <button v-if="active" type="button" class="paper" :class="{ turning, backwards }"
        :style="paperStyle"
        :disabled="count < 2 || loading || turning" :aria-label="count > 1 ? '翻到下一页' : '简历第一页'" @click="turnPage">
        <span class="paper-rotation" @animationend.self="finish">
          <img class="paper-face front" :style="{ '--face-ratio': frontRatio }" :src="active" @load="readRatio($event)" @error="imageFailed()" :alt="'简历第 ' + current + ' 页'">
          <img v-if="nextImage" class="paper-face back" :style="{ '--face-ratio': backRatio }" :src="nextImage" alt="" @load="readRatio($event, true)" @error="imageFailed(true)">
        </span>
      </button>
      <p v-else class="reader-empty" role="status">{{ loading ? '正在载入简历…' : error || (resume.parse_status === 'parsing' ? '简历解析中' : '暂未生成预览，可在操作栏重新解析。') }}</p>
    </div>
    <p v-if="active && error" class="reader-error" role="alert">{{ error }}</p>
    <footer v-if="count">
      <button class="rs-button" type="button" :disabled="current <= 1 || turning || loading" @click="flip(current - 1)">‹ 上一页</button>
      <span aria-live="polite">{{ current }} / {{ count }}<small v-if="count > 1"> · 点击简历翻页</small></span>
      <button class="rs-button" type="button" :disabled="current >= count || turning || loading" @click="flip(current + 1)">下一页 ›</button>
    </footer>
  </component>
</template>

<style scoped>
.resume-reader { display:grid; grid-template-rows:auto minmax(0,1fr) auto auto; min-width:0; min-height:0; background:#edf3f3; border:1px solid #dbe6e6; border-radius:18px; overflow:hidden; box-shadow:0 14px 34px rgba(18,50,61,.08); }
header, footer { display:flex; justify-content:space-between; align-items:center; gap:10px; padding:12px 15px; }
header { background:rgba(255,255,255,.97); border-bottom:1px solid var(--rs-border); }
.reader-identity { display:flex; align-items:center; gap:9px; min-width:0; }
.reader-avatar { width:36px; height:36px; object-fit:cover; border-radius:8px; flex:none; }
header h2 { margin:1px 0 0; font-size:16px; overflow-wrap:anywhere; }
.reader-expand { display:grid; place-items:center; }
.reader-expand svg { width:17px; height:17px; }
.resume-fullscreen {
  position:fixed; inset:0; width:100%; height:100vh; height:100dvh;
  max-width:none; max-height:none; margin:0; padding:0; border:0; border-radius:0;
  color:var(--rs-text); overscroll-behavior:contain;
}
.resume-fullscreen:not([open]) { display:none; }
.resume-fullscreen .reader-body { padding:0; }
.resume-fullscreen .paper-face { border-radius:0; }
.resume-fullscreen footer { padding-top:8px; }
.reader-kicker { color:var(--rs-primary-hover); font-size:10px; font-weight:720; letter-spacing:.06em; }
.reader-actions { display:flex; align-items:center; gap:12px; white-space:nowrap; font-size:13px; }
.reader-zoom { display:flex; align-items:center; height:29px; overflow:hidden; border:1px solid #cfe0dd; border-radius:8px; background:white; }
.reader-zoom button { width:29px; height:27px; border:0; background:transparent; color:var(--rs-primary-hover); cursor:pointer; font-size:17px; line-height:1; }
.reader-zoom button:hover:not(:disabled) { background:var(--rs-primary-soft); }
.reader-zoom button:disabled { color:#aebcbe; cursor:not-allowed; }
.reader-zoom span { min-width:43px; padding:0 5px; color:#52686c; border-inline:1px solid #e1e9e8; text-align:center; font-size:11px; font-variant-numeric:tabular-nums; }
.reader-close { border:none; border-radius:9px; width:33px; height:33px; font-size:22px; background:var(--rs-primary-soft); color:var(--rs-primary-hover); cursor:pointer; transition:transform var(--rs-motion-fast),background-color var(--rs-motion-fast); }
.reader-close:hover { background:#d7efea;transform:scale(1.04); }
.reader-body { display:grid; place-items:center; min-height:0; padding:12px; overflow:hidden; }
.paper { border:0; background:none; color:inherit; padding:0; width:100%; height:100%; min-height:0; min-width:0; container-type:size; perspective:1700px; cursor:pointer; }
.paper:disabled { cursor:default; }
.paper-rotation { position:relative; display:block; width:100%; height:100%; transform-style:preserve-3d; }
.paper.turning .paper-rotation { animation:page-next .48s cubic-bezier(.24,.7,.38,1) forwards; }
.paper.turning.backwards .paper-rotation { animation-name:page-prev; }
.paper-face { position:absolute; inset:0; margin:auto; width:min(100cqw, calc(100cqh * var(--face-ratio))); height:auto; aspect-ratio:var(--face-ratio); display:block; object-fit:contain; border-radius:9px; box-shadow:0 9px 28px rgba(18,44,54,.2); backface-visibility:hidden; background:white; }
.paper-face.back { transform:rotateY(180deg); }
.reader-empty,.reader-error { color:#64748b; margin:0 15px; text-align:center; font-size:13px; }
.reader-error { color:#a02626; }
.reader-inline {background:#f0f4f4}
.reader-inline header,.reader-inline footer {background:#f8faf9;border-color:#e7eded}
.reader-inline header {padding:7px 11px}
.reader-inline header h2 {font-size:14px}
.reader-inline footer {padding:5px 10px;border-top:1px solid #e7eded}
.reader-fit {border:1px solid #cfe0dd;background:white;color:var(--rs-primary-hover);padding:5px 8px;
  border-radius:7px;cursor:pointer;font:inherit;transition:background-color var(--rs-motion-fast),border-color var(--rs-motion-fast)}
.reader-fit:hover {background:var(--rs-primary-soft);border-color:#9ac7c1}
.reader-width-fit .reader-body:has(.paper) {display:block;overflow:auto;scrollbar-width:thin;padding:10px 6px}
/* 宽度模式限制单页最大宽度，避免超宽屏把一页 A4 拉成整屏巨幅。 */
.reader-width-fit .paper {display:block;height:auto;aspect-ratio:var(--stage-ratio);
  max-width:min(860px,100%);margin-inline:auto;container-type:normal}
.reader-width-fit .paper-face {width:100%;height:100%;aspect-ratio:auto;border-radius:5px;box-shadow:0 4px 14px #273e3c22}
footer { padding-top:4px; font-size:13px; color:#475569; }
footer small { color:#64748b; }
@media(max-width:560px) { .reader-zoom span { display:none; } .reader-zoom button:first-child { border-inline-end:1px solid #e1e9e8; } }
@media(max-width:420px) { .reader-actions { gap:6px; } header,footer { padding-inline:8px; } }
@media(max-height:450px) { .resume-fullscreen :is(header,footer) { padding-block:4px; } }
@media(max-width:540px) { footer small { display:none; } }
@keyframes page-next { to { transform:rotateY(-180deg); } }
@keyframes page-prev { to { transform:rotateY(180deg); } }
@media(prefers-reduced-motion:reduce) { .paper.turning .paper-rotation { animation-duration:.01ms; } }
</style>
