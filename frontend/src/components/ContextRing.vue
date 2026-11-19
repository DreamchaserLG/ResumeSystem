<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

type Context = {
  phase:'querying'|'generating'|'checking'|'repairing'|'done'
  turn_no:number; window_used:number; window_keep:number
  tokens_used:number; tokens_budget:number; summarized:boolean
  scope_echo:string; scope_source:'memory'|'request'
}
const props = defineProps<{ context?:Context }>()
const emit = defineEmits<{(event:'clear-scope'):void}>()

const RADIUS = 9
const CIRC = 2 * Math.PI * RADIUS
const PHASE_TEXT:Record<string,string> = {
  querying:'正在查询数据', generating:'正在生成', checking:'校验中', repairing:'自动修正中', done:'',
}
const busy = computed(() => !!props.context && props.context.phase !== 'done')
const ratio = computed(() => {
  const value = props.context
  if (!value || !value.tokens_budget) return 0
  return Math.min(1, Math.max(0, value.tokens_used / value.tokens_budget))
})
const color = computed(() => {
  if (props.context?.phase === 'repairing') return '#BA7517'
  return ratio.value > 0.9 ? '#A32D2D' : ratio.value > 0.6 ? '#BA7517' : 'var(--hire-accent)'
})
const offset = computed(() => CIRC * (1 - ratio.value))
const percent = computed(() => Math.round(ratio.value * 100))
const k = (n:number) => (n / 1000).toFixed(1) + 'k'
const label = computed(() => {
  const value = props.context
  if (!value) return ''
  const text = `上下文 ${value.window_used}/${value.window_keep} 轮 · ${k(value.tokens_used)}/${k(value.tokens_budget)} tokens · ${value.summarized ? '已压缩' : '未压缩'} · 点击查看详情`
  return PHASE_TEXT[value.phase] ? PHASE_TEXT[value.phase] + ' · ' + text : text
})

/* 点击圆环弹出用量详情；点击面板外部自动收起。 */
const open = ref(false)
const root = ref<HTMLElement>()
function toggle() {open.value=!open.value}
function onDocClick(event:MouseEvent) {
  if (open.value && root.value && !root.value.contains(event.target as Node)) open.value=false
}
onMounted(()=>document.addEventListener('click',onDocClick))
onBeforeUnmount(()=>document.removeEventListener('click',onDocClick))
function discardScope() {emit('clear-scope');open.value=false}
</script>

<template>
  <span v-if="context" ref="root" class="context-ring">
    <button type="button" class="ring-button" :aria-expanded="open?'true':'false'"
      :aria-label="label" :title="label" @click="toggle">
      <svg viewBox="0 0 24 24" aria-hidden="true"
        :class="{pulse:context?.summarized && !busy, spin:busy}">
        <circle cx="12" cy="12" :r="RADIUS" fill="none" stroke="var(--hire-line)" stroke-width="2.5"/>
        <circle cx="12" cy="12" :r="RADIUS" fill="none" :stroke="color" stroke-width="2.5"
          stroke-linecap="round" :stroke-dasharray="CIRC" :stroke-dashoffset="offset"
          transform="rotate(-90 12 12)"/>
      </svg>
    </button>
    <span v-if="busy && !open" class="context-phase-text">{{ PHASE_TEXT[context.phase] }}</span>
    <span v-if="open" class="ring-panel" role="dialog" aria-label="上下文用量详情">
      <div class="panel-row"><span>上下文占用</span>
        <strong>{{ k(context.tokens_used) }} / {{ k(context.tokens_budget) }} tokens</strong></div>
      <div class="panel-bar" aria-hidden="true"><i :style="{width:percent+'%',background:color}"/></div>
      <div class="panel-row"><span>对话记忆</span>
        <strong>{{ context.window_used }}/{{ context.window_keep }} 轮 · {{ context.summarized?'已压缩':'未压缩' }}</strong></div>
      <div class="panel-row"><span>查询口径</span>
        <strong>{{ context.scope_echo || '本轮手动选择' }}</strong></div>
      <button v-if="context.scope_source==='memory'" type="button" class="panel-action"
        @click="discardScope">丢弃记忆口径，改用手动选择</button>
    </span>
  </span>
</template>

<style scoped>
.context-ring{display:inline-flex;align-items:center;gap:6px;color:var(--hire-muted);position:relative}
.ring-button{display:grid;place-items:center;border:0;background:transparent;padding:2px;cursor:pointer;border-radius:50%}
.ring-button:hover svg{transform:scale(1.12)}
.ring-button svg{width:20px;height:20px;transition:transform .18s var(--rs-ease,ease)}
.ring-button svg circle:last-child{transition:stroke-dashoffset .3s var(--rs-ease,ease),stroke .3s}
.ring-button svg.pulse{animation:ring-pulse 1.2s ease-in-out infinite}
.ring-button svg.spin{animation:ring-spin 1s linear infinite}
.context-phase-text{font-size:11px;color:var(--hire-muted);white-space:nowrap}
.ring-panel{position:absolute;right:0;bottom:calc(100% + 10px);z-index:30;width:252px;display:grid;gap:7px;
  padding:12px 13px;border:1px solid var(--hire-line);border-radius:11px;background:#fff;
  box-shadow:0 12px 34px rgba(18,50,61,.16);text-align:left}
.panel-row{display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:12px;color:var(--hire-muted)}
.panel-row strong{font-size:12px;font-variant-numeric:tabular-nums;color:var(--hire-ink);font-weight:600;text-align:right}
.panel-bar{height:4px;border-radius:3px;background:var(--hire-line);overflow:hidden}
.panel-bar i{display:block;height:100%;border-radius:3px;transition:width .3s}
.panel-action{border:1px solid #cfe5e1;background:#f4faf9;border-radius:7px;padding:5px 8px;
  font:inherit;font-size:11.5px;color:var(--hire-accent-dark);cursor:pointer;transition:border-color .15s,background-color .15s}
.panel-action:hover{border-color:var(--hire-accent);background:#e4f4ef}
@keyframes ring-pulse{0%,100%{opacity:1}50%{opacity:.45}}
@keyframes ring-spin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion: reduce){
  .ring-button svg.pulse,.ring-button svg.spin{animation:none}
}
</style>
