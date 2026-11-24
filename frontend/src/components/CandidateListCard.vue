<script setup lang="ts">
import { computed } from 'vue'
import { label, type ResumeCard } from '@/api/hr'

const props = defineProps<{ row:ResumeCard; active:boolean; statusLabel:string; statusColor:string;
  appearance:'resume'|'talent' }>()
const emit = defineEmits<{ select:[] }>()
const name = computed(() => props.row.name || '申请 #'+props.row.id)
const initial = computed(() => props.row.name?.trim().slice(0, 2) || '简历')
const position = computed(() => props.row.job_id ? props.row.job_title : props.row.target_role || '来自简历库')
const tags = computed(() => props.row.tags.filter(tag=>['degree','capability','language'].includes(tag.type)).slice(0,3))
const score = computed(() => props.appearance === 'resume' ? props.row.match_percent : null)
const parseStatus = computed(() => ['failed','parsed','parsing'].includes(props.row.parse_status) ? label(props.row.parse_status) : '')
</script>

<template>
  <button type="button" class="workbench-card" :class="`workbench-card--${appearance}`"
    :aria-current="active || undefined" :style="{'--status-color': statusColor}" @click="emit('select')">
    <span v-if="score!=null" class="workbench-card-track" role="progressbar" :aria-label="name+' 岗位匹配度'"
      :aria-valuenow="score" aria-valuemin="0" aria-valuemax="100"><i :style="{width:score+'%'}"></i></span>
    <span class="workbench-card-initial" aria-hidden="true">{{ initial }}</span>
    <span class="workbench-card-name"><strong :title="name">{{ name }}</strong><span :title="position">{{ position }}</span></span>
    <strong v-if="score!=null" class="workbench-card-score" title="岗位匹配度">{{ score.toFixed(2) }}<small>%</small></strong>
    <span class="workbench-card-footer">
      <span class="workbench-card-tags">
        <span v-if="parseStatus">{{ parseStatus }}</span>
        <span v-for="tag in tags" :key="tag.id" :title="label(tag.value)">{{ label(tag.value) }}</span>
      </span>
      <span class="workbench-card-status" :title="statusLabel">{{ statusLabel }}</span>
    </span>
  </button>
</template>

<style scoped>
.workbench-card {
  --card-accent:#177d72;--card-tint:#eaf6f2;position:relative;isolation:isolate;overflow:hidden;
  display:grid;grid-template-columns:36px minmax(0,1fr) auto;align-items:center;gap:8px 9px;
  width:100%;min-height:94px;padding:13px 11px 10px;border:1px solid #dce7e8;border-radius:11px;
  text-align:start;background:#fff;color:var(--hire-ink);font:inherit;cursor:pointer;
  transition:background var(--rs-motion-fast),border-color var(--rs-motion-fast),box-shadow var(--rs-motion-fast)
}
.workbench-card--talent {--card-accent:#3e618a;--card-tint:#edf3fa;padding-left:14px}
.workbench-card--talent::before {content:'';position:absolute;inset:0 auto 0 0;width:3px;background:var(--status-color)}
.workbench-card:hover,.workbench-card[aria-current=true] {border-color:color-mix(in srgb,var(--card-accent) 52%,white)}
.workbench-card[aria-current=true] {background:color-mix(in srgb,var(--card-tint) 55%,white);box-shadow:0 2px 7px color-mix(in srgb,var(--card-accent) 12%,transparent)}
.workbench-card:focus-visible {outline:2px solid var(--card-accent);outline-offset:1px}
.workbench-card-track {position:absolute;inset:0 0 auto;height:3px;background:var(--card-tint)}
.workbench-card-track i {display:block;height:100%;background:var(--card-accent);border-radius:0 2px 2px 0}
.workbench-card-initial {display:grid;place-items:center;width:36px;height:36px;border-radius:9px;background:var(--card-tint);color:var(--card-accent);font-size:12px;font-weight:700}
.workbench-card-name {min-width:0;display:grid;gap:2px}
.workbench-card-name strong,.workbench-card-name>span {overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.workbench-card-name strong {font-size:14px;line-height:1.45}
.workbench-card-name>span {color:var(--hire-muted);font-size:12px}
.workbench-card-score {align-self:start;color:var(--card-accent);font-size:17px;font-variant-numeric:tabular-nums;line-height:1.3;white-space:nowrap}
.workbench-card-score small {font-size:11px;margin-left:1px}
.workbench-card-footer {grid-column:1/-1;min-width:0;display:flex;justify-content:space-between;align-items:flex-end;gap:5px}
.workbench-card-tags {min-width:0;display:flex;flex-wrap:wrap;gap:4px;overflow:hidden}
.workbench-card-tags>span,.workbench-card-status {max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:2px 6px;font-size:10.5px;line-height:1.55;font-weight:600;border-radius:5px}
.workbench-card-tags>span {background:#f0f4f6;color:#4c6170}
.workbench-card-status {flex:none;max-width:48%;margin-left:auto;color:var(--status-color);background:color-mix(in srgb,var(--status-color) 9%,white)}
</style>
