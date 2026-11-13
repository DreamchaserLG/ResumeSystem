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
    primaryColor: '#4e6fa4',
    primaryColorHover: '#405f93',
    primaryColorPressed: '#355282',
    primaryColorSuppl: '#6483b1',
    borderRadius: '8px',
    fontSize: '14px',
    fontFamily: 'var(--rs-font-family)',
  },
  Card: { borderRadius: '12px' },
  Button: { borderRadiusMedium: '8px' },
  Message: { colorSuccess: '#e3f5ed', textColorSuccess: '#14533f', iconColorSuccess: '#187653' },
}

</script>

<template>
  <NConfigProvider :theme-overrides="overrides">
    <NMessageProvider placement="top-right" :duration="3000" :max="3">
      <NDialogProvider>
        <RouterView />
      </NDialogProvider>
    </NMessageProvider>
  </NConfigProvider>
</template>
