/** Keep each account in its own browser tab, including ordinary page reloads. */
const TOKEN_KEY='rs-tab-token'
const VIEW_KEY='rs-tab-resume-view'

function read(key:string) {
  try { return sessionStorage.getItem(key) } catch { return null }
}
function write(key:string,value:string|null) {
  try {
    if(value===null) sessionStorage.removeItem(key)
    else sessionStorage.setItem(key,value)
  } catch { /* The live page still works if the browser blocks storage. */ }
}

let token=read(TOKEN_KEY) || ''
export function getSessionToken() { return token }
export function setSessionToken(value:string) {
  if(value!==token) write(VIEW_KEY,null)
  token=value
  write(TOKEN_KEY,value || null)
}
export function clearSessionToken() { setSessionToken('') }
export function readResumeView() { return read(VIEW_KEY) }
export function saveResumeView(value:string) { if(token) write(VIEW_KEY,value) }

export function discardLegacySession() {
  try {
    localStorage.removeItem('rs-token')
    localStorage.removeItem('rs-session')
  } catch { /* Ignore unavailable legacy browser storage. */ }
}

export const takeoverNotice='该账号已在其他地方登录，请重新登录'

export function expireSession(requestToken:string, message?:unknown) {
  if (!requestToken || requestToken!==token) return
  clearSessionToken()
  if (location.pathname!=='/login') {
    const next=location.pathname+location.search+location.hash
    const search=new URLSearchParams({next})
    if(message===takeoverNotice) search.set('reason','taken-over')
    location.replace('/login?'+search)
  }
}
