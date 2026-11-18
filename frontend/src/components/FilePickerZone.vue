<script setup lang="ts">
import { ref } from 'vue'
import { DocumentTextOutline, FolderOpenOutline } from '@vicons/ionicons5'

const props=defineProps<{ title:string; hint:string; disabled?:boolean; folder?:boolean }>()
const emit=defineEmits<{ browse:[]; drop:[files:File[]] }>()
const hovering=ref(false)
function drag(event:DragEvent) {
  if(props.folder || props.disabled || !event.dataTransfer?.types.includes('Files')) return
  event.preventDefault()
  event.dataTransfer.dropEffect='copy'
  hovering.value=true
}
function drop(event:DragEvent) {
  hovering.value=false
  if(props.folder || props.disabled || !event.dataTransfer) return
  const files=Array.from(event.dataTransfer.files)
  if(files.length) emit('drop',files)
}
</script>

<template>
  <button type="button" class="file-picker" :class="{ hovering }" :disabled="disabled" @click="emit('browse')"
    @dragover="drag" @dragleave="hovering=false" @drop.prevent="drop">
    <FolderOpenOutline v-if="folder" aria-hidden="true"/>
    <DocumentTextOutline v-else aria-hidden="true"/>
    <strong>{{ title }}</strong>
    <span>{{ hint }}</span>
  </button>
</template>

<style scoped>
.file-picker {width:100%;display:grid;place-content:center;justify-items:center;gap:8px;border:1px dashed #73b6ae;
  background:linear-gradient(135deg,#f5fcfa,#f7fafc);min-height:132px;padding:16px;border-radius:12px;cursor:pointer;color:var(--hire-accent-dark);text-align:center;
  transition:transform var(--rs-motion-panel) var(--rs-ease),background-color var(--rs-motion-fast),border-color var(--rs-motion-fast),box-shadow var(--rs-motion-fast)}
.file-picker svg {width:27px;height:27px;filter:drop-shadow(0 4px 8px rgba(13,137,126,.12))}
.file-picker > * {pointer-events:none}
.file-picker span {font-size:12px;color:var(--hire-muted)}
.file-picker:disabled {opacity:.62;cursor:default}
.file-picker:not(:disabled):is(:hover,.hovering) {background:#eaf8f4;border-color:var(--hire-accent);box-shadow:0 8px 22px rgba(13,137,126,.1);transform:translateY(-2px)}
.file-picker:focus-visible {outline:2px solid var(--hire-accent);outline-offset:3px}
</style>
