<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Counts } from '@/api/atlas'
const props = defineProps<{ rows:(Counts & {day:string})[]; unit:string }>()
const emit = defineEmits<{ drill:[day:string] }>()
const active = ref<number>()
const top = computed(() => {
  const peak = Math.max(1,...props.rows.flatMap(r => [r.applications,r.hired]))
  return Math.ceil(peak / 4)*4
})
const x = (i:number) => 29 + (props.rows.length > 1 ? i*259/(props.rows.length-1) : 129.5)
const y = (v:number) => 126-v*109/top.value
const line = (key:keyof Counts) => props.rows.map((r,i)=>x(i)+','+y(r[key])).join(' ')
const ticks = computed(()=>[...new Set([0,Math.round((props.rows.length-1)/2),props.rows.length-1])].filter(i=>i>=0))
const dateText=(value:string)=>props.unit==='month' ? Number(value.slice(5,7))+'月' : value.slice(5).replace('-','/')
</script>
<template>
  <div class="trend-wrap">
    <div class="line-legend"><span><i></i>投递</span><span><i></i>录取</span></div>
    <svg viewBox="0 0 300 153" class="line-chart" aria-label="投递与录取趋势图">
      <g v-for="tick in 5" :key="tick" aria-hidden="true">
        <line x1="27" x2="291" :y1="y((tick-1)*top/4)" :y2="y((tick-1)*top/4)" stroke="#d9e6f0"/>
        <text x="23" :y="y((tick-1)*top/4)+3" text-anchor="end">{{ (tick-1)*top/4 }}</text>
      </g>
      <polyline :points="line('applications')" fill="none" stroke="#2b68b1" stroke-width="2.4"/>
      <polyline :points="line('hired')" fill="none" stroke="#c56b51" stroke-width="2.4" stroke-dasharray="5 3"/>
      <g v-for="(row,i) in rows" :key="row.day">
        <line v-if="i===active" :x1="x(i)" :x2="x(i)" y1="12" y2="129" stroke="#7598be" stroke-dasharray="3 3"/>
        <circle :cx="x(i)" :cy="y(row.applications)" r="2.6" fill="#2b68b1" stroke="white"/>
        <circle :cx="x(i)" :cy="y(row.hired)" r="2.6" fill="#c56b51" stroke="white"/>
        <rect :x="x(i)-(rows.length===1 ? 30 : 129.5/Math.max(1,rows.length-1))" y="8" height="123"
          :width="rows.length===1 ? 60 : 259/Math.max(1,rows.length-1)" fill="transparent" tabindex="0" role="button"
          :aria-label="row.day+'，投递 '+row.applications+'，录取 '+row.hired"
          @mouseenter="active=i" @mouseleave="active=undefined" @focus="active=i" @blur="active=undefined"
          @click="emit('drill',row.day)" @keydown.enter="emit('drill',row.day)" @keydown.space.prevent="emit('drill',row.day)" />
      </g>
      <text v-for="i in ticks" :key="i" :x="x(i)" y="145" text-anchor="middle">{{ dateText(rows[i].day) }}</text>
    </svg>
    <div class="trend-preview" aria-live="polite">
      <template v-if="active!==undefined && rows[active]">{{ rows[active].day }}　投递 {{ rows[active].applications }} 份 · 录取 {{ rows[active].hired }} 人</template>
      <template v-else>悬停或聚焦看明细</template>
    </div>
  </div>
</template>
<style scoped>
.line-legend { display:flex; gap:14px; font-size:12px; margin:7px 0 2px; color:#465d77; }
.line-legend span { display:inline-flex; align-items:center; gap:6px; }
.line-legend i { width:15px; height:2px; background:#2b68b1; }
.line-legend span+span i { background:repeating-linear-gradient(90deg,#c56b51 0 4px,transparent 4px 6px); }
.line-chart { width:100%; display:block; overflow:visible; }
.line-chart text { font-size:9.5px; fill:#516981; }
.line-chart rect { cursor:pointer; }
.line-chart rect:focus-visible { outline:none; stroke:#3f72a9; stroke-width:1; }
.trend-preview { min-height:19px; font-size:11px; color:#435c77; }
</style>
