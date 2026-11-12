<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { NButton, NForm, NFormItem, NInput, NIcon, useMessage } from "naive-ui";
import {
  PersonOutline,
  LockClosedOutline,
  ArrowForward,
} from "@vicons/ionicons5";
import { api } from "@/api/client";
import { useAuthStore } from "@/stores/auth";

const router = useRouter();
const message = useMessage();
const auth = useAuthStore();

const loading = ref(false);
const form = ref({ username: "", password: "" });

async function handleLogin() {
  if (!form.value.username || !form.value.password) {
    message.warning("请输入企业邮箱与密码");
    return;
  }
  loading.value = true;
  try {
    const resp = await api.post<{
      token: string;
      session: {
        user_id: number;
        display_name: string;
        email: string;
        role: "super_admin" | "dept_hr";
        departments: number[];
        display_departments: string[];
      };
    }>("/auth/login", {
      email: form.value.username.trim(),
      password: form.value.password,
    });
    localStorage.setItem("rs-token", resp.token);
    auth.setSession({
      userId: resp.session.user_id,
      displayName: resp.session.display_name,
      role: resp.session.role,
      departmentName: resp.session.display_departments.join("、") || undefined,
    });
    message.success("登录成功");
    router.push("/dashboard");
  } catch (e: any) {
    message.error(e?.message ?? "登录失败，请检查企业邮箱与密码");
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="login-page">
    <!-- 背景装饰 -->
    <div class="bg-orb bg-orb-1" />
    <div class="bg-orb bg-orb-2" />
    <div class="bg-grid" />

    <div class="login-panel">
      <!-- 左：品牌区 -->
      <div class="brand-side">
        <div class="brand-logo">
          <div class="logo-mark">R</div>
          <span class="logo-text">智能简历筛选系统</span>
        </div>

        <h1 class="brand-headline">
          让每一位候选人<br />
          都被<span class="highlight">准确看见</span>
        </h1>
        <p class="brand-desc">
          结构化提取 · 原文证据回溯 · 关键词检索<br />
          为企业内部招聘提供可验证的筛选依据
        </p>

        <ul class="brand-features">
          <li>
            <span class="feat-dot" />硬条件 SQL 筛选，学历 / 年限 / 技能精确匹配
          </li>
          <li><span class="feat-dot" />关键词 + 同义词检索，证据可跳回原文</li>
          <li>
            <span class="feat-dot" />字段用途 default-deny，照片与敏感信息隔离
          </li>
          <li><span class="feat-dot" />有头像 / 无头像简历分层评估</li>
        </ul>

        <div class="brand-foot">v1 · 企业内部招聘</div>
      </div>

      <!-- 右：登录卡 -->
      <div class="form-side">
        <div class="login-card">
          <h2 class="card-title">欢迎回来</h2>
          <p class="card-sub">使用企业内部账号登录</p>

          <NForm :show-label="false" size="large" @keyup.enter="handleLogin">
            <NFormItem>
              <NInput v-model:value="form.username" placeholder="企业邮箱">
                <template #prefix>
                  <NIcon :component="PersonOutline" class="input-icon" />
                </template>
              </NInput>
            </NFormItem>
            <NFormItem>
              <NInput
                v-model:value="form.password"
                type="password"
                show-password-on="click"
                placeholder="密码"
              >
                <template #prefix>
                  <NIcon :component="LockClosedOutline" class="input-icon" />
                </template>
              </NInput>
            </NFormItem>
            <NButton
              type="primary"
              block
              size="large"
              :loading="loading"
              class="login-btn"
              @click="handleLogin"
            >
              登 录
              <template #icon>
                <NIcon :component="ArrowForward" />
              </template>
            </NButton>
          </NForm>

          <div class="hint-block">
            账号由超级管理员统一开通。忘记密码请联系系统管理员重置。
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  height: 100%;
  background:
    radial-gradient(
      1200px 600px at 85% -10%,
      rgba(91, 124, 250, 0.25),
      transparent 60%
    ),
    radial-gradient(
      900px 500px at -10% 110%,
      rgba(47, 72, 196, 0.35),
      transparent 55%
    ),
    linear-gradient(160deg, #101425 0%, #1a2140 55%, #232c55 100%);
  color: #eef1fb;
  overflow: hidden;
  position: relative;
}

.bg-orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(90px);
  opacity: 0.5;
  animation: float 12s ease-in-out infinite alternate;
}
.bg-orb-1 {
  width: 420px;
  height: 420px;
  background: #5b7cfa;
  top: -120px;
  right: -80px;
}
.bg-orb-2 {
  width: 360px;
  height: 360px;
  background: #2f48c4;
  bottom: -100px;
  left: -60px;
  animation-delay: -6s;
}
.bg-grid {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.035) 1px, transparent 1px);
  background-size: 44px 44px;
  mask-image: radial-gradient(ellipse at center, black 30%, transparent 75%);
}

@keyframes float {
  from {
    transform: translateY(-12px);
  }
  to {
    transform: translateY(16px);
  }
}

.login-panel {
  position: relative;
  z-index: 1;
  height: 100%;
  display: grid;
  grid-template-columns: 1.15fr 1fr;
  align-items: center;
  max-width: 1200px;
  margin: 0 auto;
  padding: 48px;
  gap: 64px;
}

/* ---- 品牌区 ---- */
.brand-logo {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 48px;
}
.logo-mark {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  background: var(--rs-gradient);
  display: grid;
  place-items: center;
  font-weight: 800;
  font-size: 20px;
  box-shadow: 0 6px 20px rgba(91, 124, 250, 0.45);
}
.logo-text {
  font-size: 16px;
  font-weight: 600;
  letter-spacing: 1px;
}
.brand-headline {
  font-size: 44px;
  line-height: 1.3;
  font-weight: 700;
  letter-spacing: 1px;
  margin-bottom: 18px;
}
.highlight {
  background: linear-gradient(90deg, #7d9bff, #b3c4ff);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.brand-desc {
  color: #a6b0d0;
  font-size: 15px;
  line-height: 1.9;
  margin-bottom: 36px;
}
.brand-features {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 14px;
  font-size: 14px;
  color: #c6cdea;
}
.brand-features li {
  display: flex;
  align-items: center;
  gap: 10px;
}
.feat-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #7d9bff;
  box-shadow: 0 0 8px rgba(125, 155, 255, 0.8);
  flex: none;
}
.brand-foot {
  margin-top: 48px;
  font-size: 12px;
  color: #6c78a0;
  letter-spacing: 0.5px;
}

/* ---- 登录卡 ---- */
.form-side {
  display: flex;
  justify-content: center;
}
.login-card {
  width: 100%;
  max-width: 400px;
  padding: 40px 36px 32px;
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.07);
  backdrop-filter: blur(20px);
  border: 1px solid rgba(255, 255, 255, 0.14);
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.35);
}
.card-title {
  font-size: 24px;
  font-weight: 700;
  margin-bottom: 6px;
}
.card-sub {
  color: #a6b0d0;
  font-size: 13px;
  margin-bottom: 28px;
}
.input-icon {
  color: #8a94b8;
}
.login-btn {
  margin-top: 8px;
  height: 46px;
  font-size: 15px;
  letter-spacing: 6px;
  font-weight: 600;
}

.hint-block {
  margin-top: 26px;
  padding: 12px 14px;
  border-radius: 8px;
  border: 1px dashed rgba(255, 255, 255, 0.15);
  background: rgba(255, 255, 255, 0.04);
  color: #8a94b8;
  font-size: 12px;
  line-height: 1.7;
}

/* 窄屏适配 */
@media (max-width: 900px) {
  .login-panel {
    grid-template-columns: 1fr;
    padding: 24px;
    gap: 24px;
  }
  .brand-side {
    display: none;
  }
  .form-side {
    align-items: center;
    min-height: 100vh;
  }
}
</style>
