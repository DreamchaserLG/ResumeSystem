<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import type { RouteLocationRaw } from 'vue-router'
const props = defineProps<{ to: RouteLocationRaw; preview: string; name?: string }>()
defineOptions({ inheritAttrs:false })
const hovering = ref(false), focused = ref(false), dismissed = ref(false)
const position = ref({left:0,top:0})
const destination = ref<HTMLElement>()
function locate(event: MouseEvent | FocusEvent) {
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  const maxLeft = Math.max(12, window.innerWidth-310)
  position.value = {left:Math.min(maxLeft,Math.max(12,rect.left)),
    top:rect.bottom+80 > window.innerHeight ? Math.max(8,rect.top-68) : rect.bottom+7}
  destination.value = document.fullscreenElement instanceof HTMLElement ? document.fullscreenElement : document.body
  dismissed.value = false
}
function close() { dismissed.value=true }
onBeforeUnmount(close)
</script>
<template>
  <RouterLink v-bind="$attrs" :to="props.to" :aria-label="name || preview" :title="undefined"
    @mouseenter="hovering=true; locate($event)" @mouseleave="hovering=false"
    @focus="focused=true; locate($event)" @blur="focused=false" @keydown.esc="close" @click="close">
    <slot />
  </RouterLink>
  <Teleport v-if="destination && (hovering || focused) && !dismissed" :to="destination">
    <div class="stat-preview" role="tooltip" aria-hidden="true" :style="{left:position.left+'px',top:position.top+'px'}">{{ preview }}</div>
  </Teleport>
</template>
<style scoped>
.stat-preview { position:fixed; z-index:3000; max-width:min(300px,calc(100vw - 24px)); pointer-events:none;
  padding:7px 10px; border-radius:6px; background:#253b57; color:#fff; font-size:12px; line-height:1.55;
  box-shadow:0 4px 13px #1b29493d; }
</style>
