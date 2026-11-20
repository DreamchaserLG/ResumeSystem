# 交接执行文档（Codex 执行）

> 版本：v1.0 · 日期：2026-11-20
> 读者：Codex CLI（Trae）
> 目标：把「简历库 / 人才库 / 文件服务器」改造的**剩余工作**落地。

---

## 〇、先读这个：当前进度快照

改造已由助手完成大部分，**以下是真实状态**。Codex 只需做「待办」部分，不要重复做「已完成」部分。

### 已完成 ✅（不要重复实现）

| 模块 | 文件 | 做了什么 |
|------|------|---------|
| 数据库迁移 | `migrations/sql/023_talent_pool.sql` | 新增 `talent`、`talent_followup`、`talent_interview`、`resume_dropfile` 四表 + RLS |
| 人才库 API | `app/api/talent.py`（新增） | `/talent` 全套接口，已注册进 `app/main.py` |
| 简历库排序/徽标 | `app/core/cards.py` | `in_talent` 置顶排序、`candidate_id`/`talent_status` 字段、`capability` facet |
| options 接口 | `app/api/hr.py` | `/hr/options` 返回 `capabilities` |
| 前端 API | `frontend/src/api/talent.ts`（新增）、`client.ts`（加 `patch`）、`hr.ts`（加字段） | — |
| 导航路由 | `MainLayout.vue`、`router/index.ts` | 移除 AI 分析、加人才管理 |
| 简历管理页 | `ApplicationsView.vue` | 移除跟进/面试/调剂/AI摘要、加放入人才库按钮+徽标、岗位-部门联动 |
| 筛选组件 | `ResumeFilters.vue` | 加能力标签 facet、岗位先选部门联动 |
| 人才管理页 | `TalentPoolView.vue`（新增） | 卡片网格+状态看板+详情抽屉 |

### 待办 ⚠️（Codex 要做的）

| # | 待办 | 优先级 | 说明 |
|---|------|--------|------|
| T1 | **筛选改成侧滑抽屉浮层** | 高 | 见下「任务一」 |
| T2 | 跑迁移验证 023 | 高 | docker 起 pg 后 `python -m migrations.migrate` |
| T3 | 文件服务器扫描逻辑 | 待地址 | 见下「任务三」 |

---

## 一、任务 T1：筛选改侧滑抽屉浮层（高优先级，用户明确要求）

### 现状
`ApplicationsView.vue` 中筛选是 `<ResumeFilters v-if="filterOpen">` 直接**替换**左侧 toolbar，属于「内嵌展开」而非「悬浮覆盖」。用户要的是**从侧边滑出的抽屉，覆盖在列表上方（不挤压布局），带返回**。

### 目标交互
- 点「筛选」按钮 → 筛选面板从侧边滑出，**覆盖**在候选人列表上方（`position: absolute` 浮层，不改变列表布局）
- 有遮罩或明显的浮层边界
- 有「返回/关闭」按钮 + 点击遮罩关闭 + Esc 关闭
- 选完「确定」关闭浮层

### 建议实现路径
1. 把 `ResumeFilters` 的容器样式从「占位替换」改为「绝对定位浮层」：
   - 在 `ApplicationsView.vue` 的 `<aside class="candidate-aside">` 里，给筛选面板加 `position: absolute; inset: 0; z-index` 覆盖
   - 或用 Element Plus 的 `ElDrawer` 包一层（`append-to-body`，`direction="ltr"`）
2. `ResumeFilters.vue` 本身逻辑不动（能力标签、岗位联动已就绪），只调整外层容器/遮罩
3. 确认 `filterCount` 徽标、`clear`、`apply` 逻辑在浮层模式下仍正常

### 验收
- 筛选浮层覆盖列表，列表不抖动/不挤压
- 点遮罩或返回按钮能关闭
- 岗位筛选仍是「先选部门→再选岗位」联动

---

## 二、任务 T2：跑迁移验证

```bash
cd /home/NG/workstation/智能简历筛选系统/resume-system
docker compose up -d pg
.venv/bin/python -m migrations.migrate   # 或 docker compose run --rm migrate
```

验证 023 迁移成功，重点检查：
1. `talent` 表的 `candidate_id` 唯一索引 + status CHECK 约束
2. 四张表的 RLS POLICY 已创建（`\d talent` 查看）
3. `resume_dropfile` 的 `sha256` 唯一索引

---

## 三、任务 T3：文件服务器扫描（等用户给地址后再做）

**本机已完成**：`resume_dropfile` 表 + 状态机（pending/processing/imported/failed/duplicate）。

**待做**（等文件服务器地址 + 默认部门 ID 确定后）：
1. `app/core/config.py` 加 `resume_drop_dir`、`drop_default_department_id`
2. 新建 `worker/tasks/scan_dropdir.py`：扫描目录 → sha256 去重 → 登记 `resume_dropfile` → 复用 `resume_pipeline.py` 解析
3. `worker/celery_app.py` 注册 beat 调度（每天定时）
4. 关键决策：入库简历**不建 application**（纯简历池），需改 `cards.py` 的 `scoped` 查询（当前强依赖 `application JOIN job`），或以 `candidate` 为主表

> ⚠️ 这个任务当前**不要动手**，因为：
> - 文件服务器地址未提供
> - 「无岗位简历的部门归属」未定
> 等用户明确后再开工。

---

## 四、关键接口契约（Codex 对接时参考）

### 4.1 人才库 API（`/api/v1/talent`）

```
GET    /talent?status=&capability=&query=&offset=&limit=  列表
GET    /talent/status-options                              状态字典（含颜色）
POST   /talent            { candidate_id? | application_id? }   复制转入（幂等）
GET    /talent/{id}                                          详情（含 interviews）
PATCH  /talent/{id}/status   { status, status_label? }       改状态
GET    /talent/{id}/followups?before=                        跟进列表
POST   /talent/{id}/followups   (FormData: content, mentions, attachment)  新增跟进
DELETE /talent/{id}/followups/{note_id}                      软删跟进
GET    /talent/{id}/followups/{note_id}/attachment           下载附件
POST   /talent/{id}/interview  { scheduled_at, interviewer_ids?, note? }   安排面试
```

### 4.2 状态颜色（前端从 `/talent/status-options` 拿，勿硬编码）

| code | label | color |
|------|-------|-------|
| interviewing | 面试进行中 | #2F80ED |
| hired | 录用 | #27AE60 |
| rejected | 淘汰 | #EB5757 |
| blacklist | 黑名单 | #4F4F4F |
| custom | 自定义 | #9B51E0 |

### 4.3 简历库卡片新增字段

`ResumeCard` 新增 `candidate_id: number`、`in_talent: boolean`、`talent_status: string | null`。排序由后端 `in_talent DESC` 保证，前端无需再排。

### 4.4 能力标签 facet

`/hr/resume-cards/search` 的 `facets` 现在接受 `capability` 维度，候选值来自 `capability_group.code`（如 `backend`、`frontend`、`machine_learning`）。可选值清单通过 `/hr/options` 的 `capabilities: [{code,name}]` 获取。

---

## 五、技术栈与约定（Codex 必读）

- **后端**：FastAPI + SQLAlchemy（psycopg3），读走 `rls_session`（RLS 生效），写走 `owner_engine`（绕过 RLS）
- **RLS**：`app_departments()` / `app_is_super_admin()` / `app_client_id()` 三个会话函数，见 `002_rls.sql`
- **迁移**：`migrations/sql/*.sql` 按文件名顺序，`023` 是最新
- **前端**：Vue3 + Element Plus + Naive UI（布局），`frontend/dist` 挂到 nginx
- **构建**：改前端后 `cd frontend && npx vite build`（注意 `pnpm build` 会因 esbuild build script 被忽略而失败，用 `npx vite build`）
- **能力标签基建**：`capability_group`（23 组）已存在，见 `015_skill_taxonomy.sql`，勿重复建表

---

## 六、文件清单汇总

### 本次已改（供 Codex 了解全貌，勿重复改）

```
migrations/sql/023_talent_pool.sql     新增
app/api/talent.py                       新增
app/main.py                             注册 talent 路由
app/api/hr.py                           options 加 capabilities
app/core/cards.py                       排序/facet/字段
frontend/src/api/talent.ts              新增
frontend/src/api/client.ts              加 patch
frontend/src/api/hr.ts                  加字段
frontend/src/layouts/MainLayout.vue     导航
frontend/src/router/index.ts            路由
frontend/src/views/ApplicationsView.vue 简历管理页改造
frontend/src/components/ResumeFilters.vue 筛选改造
frontend/src/views/TalentPoolView.vue   新增（人才管理页）
frontend/src/views/JobCandidatesView.vue mock 数据补字段
```

### 本次待办（Codex 要产出）

```
T1: ApplicationsView.vue + ResumeFilters.vue  筛选改侧滑抽屉浮层
T2: 跑迁移验证 023
T3: worker/tasks/scan_dropdir.py + celery_app.py  文件服务器扫描（等地址）
```
