<script setup lang="ts">
import {
  NConfigProvider,
  NMessageProvider,
  NDialogProvider,
  type GlobalThemeOverrides,
} from 'naive-ui'

document.documentElement.classList.remove('dark')

const overrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#4f6bed',
    primaryColorHover: '#3d5af1',
    primaryColorPressed: '#2f48c4',
    primaryColorSuppl: '#5b7cfa',
    borderRadius: '8px',
    fontSize: '14px',
    fontFamily:
      "'PingFang SC','HarmonyOS Sans SC','Microsoft YaHei',system-ui,-apple-system,'Segoe UI',sans-serif",
  },
  Card: { borderRadius: '12px' },
  Button: { borderRadiusMedium: '8px' },
}

</script>

<template>
  <NConfigProvider :theme-overrides="overrides">
    <NMessageProvider>
      <NDialogProvider>
        <RouterView />
      </NDialogProvider>
    </NMessageProvider>
  </NConfigProvider>
</template>
