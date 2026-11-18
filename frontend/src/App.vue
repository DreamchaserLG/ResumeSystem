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
    primaryColor: '#0d897e',
    primaryColorHover: '#08756c',
    primaryColorPressed: '#075f59',
    primaryColorSuppl: '#159a8d',
    borderRadius: '10px',
    fontSize: '14px',
    fontFamily: 'var(--rs-font-family)',
  },
  Card: { borderRadius: '14px' },
  Button: { borderRadiusMedium: '9px' },
  Input: { borderRadius: '9px' },
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
