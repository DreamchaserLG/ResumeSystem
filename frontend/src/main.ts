import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { discardLegacySession, getSessionToken } from './api/session'
import '@fontsource-variable/noto-sans-sc'
import 'element-plus/dist/index.css'
import './styles/main.css'
import './styles/recruiting.css'

discardLegacySession()
// Revalidate before displaying a previously cached private page on back/forward.
let privateSnapshot = false
window.addEventListener('pagehide', event => {
  if (!event.persisted || !getSessionToken()) return
  privateSnapshot = true
  const appRoot = document.getElementById('app')
  if (appRoot) appRoot.style.visibility='hidden'
})
window.addEventListener('pageshow', event => {
  if (event.persisted && privateSnapshot) location.reload()
})

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
