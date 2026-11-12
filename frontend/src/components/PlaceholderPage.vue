<script setup lang="ts">
import { NEmpty, NIcon } from 'naive-ui'

defineProps<{
  title: string
  milestone: string
  desc: string
}>()
</script>

<template>
  <div class="rs-page">
    <div class="rs-card placeholder">
      <NEmpty :description="`「${title}」将在 ${milestone} 交付`">
        <template #icon>
          <NIcon :size="0" />
        </template>
        <template #extra>
          <span class="ph-desc">{{ desc }}</span>
        </template>
      </NEmpty>
    </div>
  </div>
</template>

<style scoped>
.placeholder {
  min-height: 420px;
  display: grid;
  place-items: center;
  padding: 40px;
}
.ph-desc {
  color: var(--rs-text-tertiary);
  font-size: 13px;
}
</style>
