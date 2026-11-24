<script setup lang="ts">
import { computed } from 'vue'
import { postingSourceLabel, type Job, type JobPostingReference } from '@/api/hr'

const props = defineProps<{ job: JobPostingReference & Pick<Job,'jd'|'requirements'> }>()

interface PostingPoint { text:string; children:string[] }

function postingPoints(value: string | null | undefined): PostingPoint[] {
  const points: PostingPoint[] = []
  for (const line of (value || '').replace(/[ \t]{2,}-(?=\S)/g, '\n  - ').split(/\r?\n/)) {
    const trimmed = line.trim()
    if (!trimmed) continue
    const nested = (line.match(/^[ \t]+[-*•●▪◦]\s*(\S.*)$/)
      || trimmed.match(/^o\s+(\S.*)$/))?.[1]
    const parent = points.at(-1)
    if (nested && parent) {
      parent.children.push(nested)
      continue
    }
    const text = trimmed.replace(/^(?:(?:[0-9０-９一二三四五六七八九十]+[、．.）)])|[-*•●▪◦])\s*/, '')
    if (text) points.push({ text, children:[] })
  }
  return points
}

const sections = computed(() => [
  { title:'岗位职责', items:postingPoints(props.job.jd) },
  { title:'任职要求', items:postingPoints(props.job.requirements) },
])
</script>

<template>
  <div class="posting-details">
    <p v-if="job.jd_sources.length" class="hire-muted">
      {{ job.reference_current ? '招聘网站参考稿 · 待 HR 确认' : '岗位文字已编辑 · 原参考来源' }}
      <span v-if="job.jd_researched_on"> · {{ job.jd_researched_on }}</span>
    </p>
    <section v-for="section in sections" :key="section.title">
      <h3>{{ section.title }}</h3>
      <ol v-if="section.items.length" class="posting-points">
        <li v-for="(point, index) in section.items" :key="index">
          <span :class="{'point-heading':point.children.length}">{{ point.text }}</span>
          <ul v-if="point.children.length" class="posting-subpoints">
            <li v-for="(item, subIndex) in point.children" :key="subIndex">{{ item }}</li>
          </ul>
        </li>
      </ol>
      <p v-else class="hire-muted">尚未填写{{ section.title }}。</p>
    </section>
    <div v-if="job.jd_sources.length">
      <h3>招聘来源</h3>
      <ul class="posting-sources">
        <li v-for="source in job.jd_sources" :key="source.url">
          <a :href="source.url" target="_blank" rel="noopener noreferrer">{{ source.platform }} · {{ source.posting }}</a>
          <span>{{ postingSourceLabel[source.match] }}</span>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.posting-details { display:grid;gap:13px; }
.posting-details h3 { margin:0 0 5px;font-size:14px; }
.posting-points { list-style:decimal outside;margin:0;padding-inline-start:1.5em; }
.posting-points > li { padding-inline-start:.22em;margin-block:5px;font-size:14px;line-height:1.6;color:#171717;overflow-wrap:anywhere; }
.point-heading { font-weight:600; }
.posting-subpoints { list-style:disc outside;margin:4px 0 7px;padding-inline-start:1.28em; }
.posting-subpoints li { margin-block:3px;font-size:13px;font-weight:400;line-height:1.55;color:#171717; }
.posting-subpoints li::marker { font-size:.72em;color:#171717; }
.posting-sources { display:grid;gap:5px;list-style:none;margin:0;padding:0;font-size:12px; }
.posting-sources li { display:flex;align-items:baseline;gap:8px;flex-wrap:wrap; }
.posting-sources a { color:var(--hire-accent-dark); }
.posting-sources span { color:var(--hire-muted); }
</style>
