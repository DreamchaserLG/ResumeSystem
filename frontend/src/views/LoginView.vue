<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { NButton, NInput, NIcon, NModal, useMessage } from "naive-ui";
import {
  MailOutline,
  LockClosedOutline,
} from "@vicons/ionicons5";
import sorterConcept from "@/assets/resume-sorter-concept.webp";
import { api, ApiError } from "@/api/client";
import { takeoverNotice } from "@/api/session";
import { sessionFromIdentity, useAuthStore, type SessionIdentity } from "@/stores/auth";

const router = useRouter();
const route = useRoute();
const message = useMessage();
const auth = useAuthStore();

const loading = ref(false);
const form = ref<HTMLFormElement>();
const showConflict = ref(false);
const replacement = ref<string>();
const retryRestore = () => location.reload();

async function handleLogin(replace?:string) {
  if (loading.value || !form.value) return;
  const fields = new FormData(form.value);
  const email = String(fields.get("username") || "").trim();
  const password = String(fields.get("password") || "");
  if (!email || !password) {
    message.warning("请输入企业邮箱与密码");
    return;
  }
  loading.value = true;
  try {
    const resp = await api.post<{
      token: string;
      session: SessionIdentity;
    }>("/auth/login", {
      email,
      password,
      replace_login: replace,
    });
    showConflict.value=false;
    replacement.value=undefined;
    auth.setSession(resp.token, sessionFromIdentity(resp.session));
    message.success("登录成功");
    const next = route.query.next;
    const target = typeof next === "string" && next.startsWith("/") &&
      !next.startsWith("//") && !next.includes("\\") && router.resolve(next).name !== "login"
      ? next : "/jobs";
    await router.replace(target);
  } catch (error) {
    if (error instanceof ApiError && error.status === 409 && error.detail && typeof error.detail === "object") {
      const detail = error.detail as {code?:string; replace_login?:string};
      if (detail.code === "login_conflict" && detail.replace_login) {
        replacement.value=detail.replace_login;
        showConflict.value=true;
        return;
      }
    }
    showConflict.value=false;
    replacement.value=undefined;
    message.error(error instanceof Error ? error.message : "登录失败，请检查企业邮箱与密码");
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <main class="login-page">
    <div class="login-visual" aria-hidden="true">
      <svg class="visual-backdrop" viewBox="0 0 1000 900" preserveAspectRatio="none">
        <defs>
          <linearGradient id="login-panel-blue" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop stop-color="#eaf3fd" />
            <stop offset="1" stop-color="#d7e8fa" />
          </linearGradient>
        </defs>
        <path d="M0 0H1000C870 175 858 338 892 520C920 668 864 795 756 900H0Z" fill="url(#login-panel-blue)" />
      </svg>
      <svg class="sorter-illustration" viewBox="0 165 934 595" role="presentation">
        <image :href="sorterConcept" x="0" y="0" width="1672" height="941" />
      </svg>
    </div>

    <section class="login-content" aria-labelledby="login-title">
      <div class="login-card">
        <img class="login-logo" src="/cenbest.png" alt="新百 CENBEST" width="129" height="56">
        <h1 id="login-title">智能简历筛选系统</h1>
        <p class="login-subtitle">请使用企业账号登录</p>

        <p v-if="route.query.reason==='taken-over' || auth.resumeIssue==='taken-over'" class="login-notice" role="alert">{{ takeoverNotice }}</p>
        <p v-else-if="auth.resumeIssue==='verify-failed'" class="login-notice" role="alert">暂时无法验证会话。
          <button type="button" class="hire-inline-button" @click="retryRestore">重试</button>
        </p>
        <form ref="form" class="login-form" method="post" autocomplete="on" @submit.prevent="handleLogin()">
          <NInput
            size="large" placeholder="企业邮箱"
            :input-props="{ name:'username', 'aria-label':'企业邮箱', autocomplete:'username', inputmode:'email', spellcheck:false }"
          >
            <template #prefix><NIcon :component="MailOutline" class="input-icon" aria-hidden="true" /></template>
          </NInput>
          <NInput
            size="large" type="password" show-password-on="click" placeholder="密码"
            :input-props="{ name:'password', 'aria-label':'密码', autocomplete:'current-password' }"
          >
            <template #prefix><NIcon :component="LockClosedOutline" class="input-icon" aria-hidden="true" /></template>
          </NInput>
          <NButton attr-type="submit" type="primary" block :loading="loading" class="login-btn">
            登录
          </NButton>
        </form>

        <p class="login-help">账号由管理员开通。忘记密码请联系管理员。</p>
        <NModal v-model:show="showConflict" preset="dialog" type="warning" title="账号已在其他地方登录"
          :mask-closable="false" :close-on-esc="!loading" :closable="!loading" style="width:min(410px, 92vw)">
          <p>继续登录会退出原来的会话。取消则保留原会话。</p>
          <template #action>
            <NButton :disabled="loading" @click="showConflict=false;replacement=undefined">取消</NButton>
            <NButton type="primary" :loading="loading" @click="handleLogin(replacement)">确认登录</NButton>
          </template>
        </NModal>
      </div>
    </section>
  </main>
</template>

<style scoped>
.login-page {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 56fr) minmax(0, 44fr);
  min-height: 100%;
  min-height: 100dvh;
  background: #fff;
  color: #202e42;
}

.login-visual {
  position: absolute;
  inset: 0 auto 0 0;
  width: 62%;
  display: grid;
  align-items: center;
  pointer-events: none;
}

.visual-backdrop { position: absolute; inset: 0; width: 100%; height: 100%; }
.sorter-illustration {
  position: relative;
  display: block;
  width: 86%;
  max-height: 74dvh;
  margin-inline: 2.5% auto;
  mask-image: linear-gradient(to right, transparent, #000 5% 95%, transparent),
    linear-gradient(to bottom, transparent, #000 13% 87%, transparent);
  mask-composite: intersect;
}

.login-content {
  position: relative;
  z-index: 1;
  grid-column: 2;
  display: grid;
  place-items: center;
  padding: clamp(28px, 5vw, 78px);
}

.login-card { width: min(100%, 420px); text-align: center; }
.login-logo { display: block; width: clamp(128px, 13vw, 185px); height: auto; margin: 0 auto 30px; }
.login-card h1 { font-size: clamp(25px, 2.65vw, 42px); line-height: 1.25; font-weight: 750; }
.login-subtitle { margin: 12px 0 34px; color: #53657e; font-size: 15px; }

.login-form { display: grid; gap: 16px; text-align: start; }
.login-form :deep(.n-input) { --n-height: 48px !important; --n-padding-left: 14px !important; --n-placeholder-color: #687a91 !important; }
.login-form :deep(.n-input__prefix) { margin-right: 10px; }
.login-form :deep(.input-icon) { color: #5d718e; font-size: 19px; }
.login-btn { margin-top: 5px; height: 48px; font-size: 16px; font-weight: 650; }
.login-help { margin-top: 22px; color: #566881; font-size: 12px; line-height: 1.65; }
.login-notice {text-align:start;margin:0 0 14px;padding:10px 12px;border:1px solid #e9d8a7;
  border-radius:8px;background:#fff8e8;color:#754d13;font-size:13px}

@media (max-width: 760px) {
  .login-page { grid-template-columns: 1fr; grid-template-rows: auto auto; align-content: center; }
  .login-content { grid-column: 1; grid-row: 1; padding: 32px 22px 20px; }
  .login-card { max-width: 390px; }
  .login-logo { margin-bottom: 18px; }
  .login-subtitle { margin-bottom: 24px; }
  .login-visual {
    position: relative;
    grid-row: 2;
    width: 100%;
    min-height: 145px;
    background: linear-gradient(180deg, #fff, #eaf3fd 36%, #dceafb);
    border-radius: 45% 45% 0 0 / 10% 10% 0 0;
  }
  .visual-backdrop { display: none; }
  .sorter-illustration { width: min(88%, 425px); max-height: 31dvh; margin: 0 auto; }
}
</style>
