<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import { useRoute, useRouter } from "vue-router";
import { useMessage } from "naive-ui";
import { ApiError, api } from "@/api/client";
import {
  label,
  loadJobs,
  schoolTagLabels,
  stageDisplay,
  stageKey,
  stagesFor,
  type Job,
  type Stage,
  type ResumeCard,
  type ResumeCardPage,
  type ResumeTag,
} from "@/api/hr";
import ResumeBook from "@/components/ResumeBook.vue";

type Format = "pdf" | "word" | "image";
const formats: Record<Format, { title: string; accept: string }> = {
  pdf: { title: "PDF", accept: ".pdf,application/pdf" },
  word: { title: "Word", accept: ".doc,.docx" },
  image: { title: "图片", accept: ".png,.jpg,.jpeg,.webp" },
};
const tagNames: Record<string, string> = {
  legal: "法律",
  finance: "金融",
  consumer: "生活服务",
  technology: "技术",
  healthcare: "医疗",
  human_resources: "人力资源",
  biomed: "生物医药",
};
const bucketNames: Record<string, string> = {
  under1: "1 年以内",
  "1to3": "1–3 年",
  "3to5": "3–5 年",
  "5plus": "5 年以上",
};
const genders: Record<string, string> = {
  male: "男",
  female: "女",
  other: "其他",
};
const route = useRoute(),
  router = useRouter();
const toast = useMessage();
function routeJob(): number | "" {
  const id = typeof route.query.job === "string" ? Number(route.query.job) : 0;
  return Number.isSafeInteger(id) && id > 0 ? id : "";
}
const rows = ref<ResumeCard[]>([]);
const jobs = ref<Job[]>([]);
const jobId = ref<number | "">(routeJob()),
  filterJob = ref<number | "">(routeJob());
const urlStage = typeof route.query.stage === "string" ? route.query.stage : "";
const stageFilter = ref(
  /^(review|progress|hired|rejected|interview:[1-9]\d{0,17})$/.test(urlStage)
    ? urlStage
    : "",
);
const urlDepartment = Number(
  typeof route.query.department === "string" ? route.query.department : 0,
);
const scopeDepartment = ref(
  Number.isSafeInteger(urlDepartment) && urlDepartment > 0
    ? urlDepartment
    : null,
);
const scopeParse = ref(route.query.parse === "failed" ? "failed" : null);
const scopeStalled = ref(route.query.stalled === "1");
const selectedJob = computed(() =>
  jobs.value.find((j) => j.id === filterJob.value),
);
const availableStages = computed(() => stagesFor(selectedJob.value?.rounds));
const schoolFilters = ref(false);
const editStage = ref<{ id: number; choice: string }>();
const savingStage = ref(false);
const format = ref<Format>("pdf");
const file = ref<File>(),
  input = ref<HTMLInputElement>();
const previous = ref<number | "">("");
const showImport = ref(false);
const query = ref(""),
  currentQuery = ref("");
const tags = ref<{ id: string; name: string }[]>([]);
const error = ref(""),
  message = ref(route.query.transferred ? "已调剂到目标岗位。" : "");
const busy = ref(false),
  loading = ref(false);
const page = ref(1),
  total = ref(0);
const mode = ref("auto"),
  size = ref(6),
  customSize = ref(6);
const selected = ref<ResumeCard>();
const fullscreenPreview = ref(false);
watch(selected, (row) => {
  if (!row) fullscreenPreview.value = false;
});
const cardViewport = ref<HTMLElement>();
const portrait = ref<ResumeCard>(),
  modalClose = ref<HTMLButtonElement>();
const avatars = ref<Record<string, string>>({});
const ROW_HEIGHT = 116; // 固定行高：选中态与详情栏不再改变行尺寸，列表布局恒定
const maxPage = computed(() =>
  Math.max(1, Math.ceil(total.value / size.value)),
);
const visibleCount = computed(() => rows.value.length);
let request = 0,
  isUnmounted = false;
let refreshTimer: ReturnType<typeof setTimeout> | undefined;
let resizeTimer: ReturnType<typeof setTimeout> | undefined;
let importTimer: ReturnType<typeof setTimeout> | undefined;
let importCheck = 0;

function trackImport({
  application_id: id,
  version_id: version,
}: {
  application_id: number;
  version_id: number;
}) {
  const check = ++importCheck;
  const started = Date.now();
  if (importTimer) clearTimeout(importTimer);
  async function poll() {
    try {
      const result = await api.get<{
        status: string;
        fail_reason: string | null;
      }>(`/applications/${id}/resumes/${version}`);
      if (isUnmounted || check !== importCheck) return;
      if (result.status === "published") {
        message.value = "简历解析完成。";
        void load();
        return;
      }
      if (result.status === "failed") {
        message.value = "";
        message.value =
          result.fail_reason || "简历解析失败，请在详情页重新解析。";
        return;
      }
    } catch (e) {
      if (isUnmounted || check !== importCheck) return;
      if (e instanceof ApiError && e.status === 404) {
        message.value = "申请已不在当前可查看的范围内。";
        return;
      }
    }
    if (Date.now() - started >= 15 * 60_000) {
      message.value = "暂未确认解析结果，可刷新列表或在申请详情中查看。";
      return;
    }
    importTimer = setTimeout(poll, 3500);
  }
  void poll();
}

function avatarKey(row: ResumeCard) {
  return row.id + ":" + row.version_id;
}
function releaseAvatars() {
  Object.values(avatars.value).forEach((url) => URL.revokeObjectURL(url));
  avatars.value = {};
}
function initials(row: ResumeCard) {
  return row.name?.trim().slice(0, 2) || "简";
}
async function loadAvatars(list: ResumeCard[], sequence: number) {
  const keep = new Set(list.filter((row) => row.has_avatar).map(avatarKey));
  if (fullscreenPreview.value && selected.value?.has_avatar)
    keep.add(avatarKey(selected.value));
  for (const [key, url] of Object.entries(avatars.value))
    if (!keep.has(key)) {
      URL.revokeObjectURL(url);
      delete avatars.value[key];
    }
  await Promise.all(
    list
      .filter((row) => row.has_avatar && !avatars.value[avatarKey(row)])
      .map(async (row) => {
        try {
          const photo = await api.get<Blob>(
            `/hr/applications/${row.id}/avatar?version=${row.version_id}`,
          );
          if (!isUnmounted && sequence === request)
            avatars.value[avatarKey(row)] = URL.createObjectURL(photo);
        } catch {
          /* A revoked or unavailable photo uses the initials. */
        }
      }),
  );
}
async function load(next = page.value) {
  const seq = ++request;
  error.value = "";
  loading.value = true;
  if (refreshTimer) clearTimeout(refreshTimer);
  try {
    const response = await api.post<ResumeCardPage>("/hr/resume-cards/search", {
      job_id: filterJob.value || null,
      department_id: scopeDepartment.value,
      parse_status: scopeParse.value,
      stalled: scopeStalled.value,
      stage_key: stageFilter.value || null,
      query: currentQuery.value,
      tags: tags.value.map((t) => t.id),
      page: next,
      size: size.value,
    });
    if (seq !== request || isUnmounted) return;
    total.value = response.total;
    schoolFilters.value = response.school_filters;
    if (next > maxPage.value) {
      void load(maxPage.value);
      return;
    }
    page.value = next;
    rows.value = response.items;
    if (selected.value) {
      const active = rows.value.find((row) => row.id === selected.value?.id);
      if (active || !fullscreenPreview.value) selected.value = active;
    }
    if (portrait.value)
      portrait.value = rows.value.find((row) => row.id === portrait.value?.id);
    void loadAvatars(rows.value, seq);
    if (rows.value.some((row) => row.parse_status === "parsing"))
      refreshTimer = setTimeout(() => void load(), 3500);
  } catch (e) {
    if (seq === request) error.value = (e as Error).message;
  } finally {
    if (seq === request) loading.value = false;
  }
}
function actualHeight() {
  const viewport = cardViewport.value;
  if (!viewport) return 6;
  // 行高恒定（ROW_HEIGHT），每页数量只随视口高度变化，选中态不影响布局
  return Math.max(
    1,
    Math.min(100, Math.floor((viewport.clientHeight + 8) / (ROW_HEIGHT + 8))),
  );
}
function adjust(anchor?: number) {
  // 仅由用户手动调整「每页数量」触发；选中简历 / 开关详情栏不重算列表布局。
  const nextSize =
    mode.value === "auto"
      ? actualHeight()
      : mode.value === "custom"
        ? Math.max(1, Math.min(100, Math.trunc(customSize.value || 1)))
        : Number(mode.value);
  if (nextSize === size.value) return;
  const selectedIndex = selected.value
    ? rows.value.findIndex((row) => row.id === selected.value?.id)
    : -1;
  const first =
    anchor ?? (page.value - 1) * size.value + Math.max(0, selectedIndex);
  size.value = nextSize;
  void load(Math.floor(first / nextSize) + 1);
}
function updateSize() {
  if (mode.value === "custom") customSize.value = size.value;
  adjust();
}
function search() {
  currentQuery.value = query.value.trim();
  selected.value = undefined;
  editStage.value = undefined;
  void load(1);
}
function changeJob() {
  if (stageFilter.value.startsWith("interview:")) stageFilter.value = "";
  editStage.value = undefined;
  const target = selectedJob.value;
  jobId.value = target?.status === "open" && target.manageable ? target.id : "";
  search();
}
function selectJob() {
  const {
    job: _job,
    transferred: _transferred,
    department: _department,
    parse: _parse,
    stalled: _stalled,
    ...rest
  } = route.query;
  const scope = {
    ...rest,
    ...(scopeDepartment.value ? { department: scopeDepartment.value } : {}),
    ...(scopeParse.value ? { parse: scopeParse.value } : {}),
    ...(scopeStalled.value ? { stalled: "1" } : {}),
  };
  void router.replace({
    query: filterJob.value ? { ...scope, job: filterJob.value } : scope,
  });
  changeJob();
}
watch(
  () => route.query.job,
  () => {
    const id = routeJob();
    if (id !== filterJob.value) {
      filterJob.value = id;
      changeJob();
    }
  },
);
function tagText(tag: ResumeTag) {
  if (tag.type === "experience") {
    const months = Number(tag.value);
    const years = Math.floor(months / 12);
    return (
      "工作 " +
      (years ? years + " 年" : "") +
      (months % 12 ? (years ? " " : "") + (months % 12) + " 个月" : "")
    );
  }
  if (tag.type === "language") return "主语言：" + tag.value;
  if (tag.type === "school")
    return (
      schoolTagLabels[tag.value] ||
      (tag.value.startsWith("qs-") ? "QS #" + tag.value.slice(3) : tag.value)
    );
  return tag.type === "degree"
    ? label(tag.value)
    : tag.type === "domain"
      ? tagNames[tag.value] || tag.value
      : tag.value;
}
function toggleTag(tag: ResumeTag) {
  if (!tag.filterable) return;
  if (tags.value.some((t) => t.id === tag.id)) {
    removeTag(tag.id);
    return;
  }
  if (tags.value.length >= 10) {
    error.value = "最多可同时选择 10 个标签。";
    return;
  }
  const name =
    tag.id === "school:qs_top100"
      ? schoolTagLabels.qs_top100
      : tag.type === "experience"
        ? "工作 " + bucketNames[tag.id.split(":")[1]]
        : tagText(tag);
  tags.value.push({ id: tag.id, name });
  search();
}
function stagesForCard(row: ResumeCard) {
  return stagesFor(jobs.value.find((j) => j.id === row.job_id)?.rounds);
}
function selectStage(row: ResumeCard, event: Event) {
  const choice = (event.target as HTMLSelectElement).value;
  editStage.value =
    choice === stageKey(row.stage) ? undefined : { id: row.id, choice };
}
async function saveStage(row: ResumeCard) {
  const target = stagesForCard(row).find(
    (item) => stageKey(item) === editStage.value?.choice,
  );
  if (!target || savingStage.value || editStage.value?.id !== row.id) return;
  savingStage.value = true;
  error.value = "";
  message.value = "";
  try {
    const updated = await api.put<{ stage: Stage; workflow_version: number }>(
      `/hr/applications/${row.id}/stage`,
      {
        code: target.code,
        round_id: target.round_id,
        workflow_version: row.workflow_version,
      },
    );
    Object.assign(row, updated);
    editStage.value = undefined;
    toast.success("招聘状态已更新。");
    await load();
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      editStage.value = undefined;
      await load();
    }
    error.value = (e as Error).message;
  } finally {
    savingStage.value = false;
  }
}
function removeTag(id: string) {
  tags.value = tags.value.filter((tag) => tag.id !== id);
  search();
}
async function changePage(next: number) {
  const anchor = (next - 1) * size.value;
  selected.value = undefined;
  await nextTick();
  void load(Math.floor(anchor / size.value) + 1);
}
async function openCard(row: ResumeCard) {
  // 选中简历只切换右侧详情，列表尺寸与分页保持不变（固定占比布局）
  selected.value = row;
}
async function closeBook() {
  selected.value = undefined;
}
async function openPortrait(row: ResumeCard) {
  portrait.value = row;
  await nextTick();
  modalClose.value?.focus();
}
function clearPortrait() {
  portrait.value = undefined;
}
function onFullscreenChange(active: boolean) {
  fullscreenPreview.value = active;
}
function esc(event: KeyboardEvent) {
  if (fullscreenPreview.value) return;
  if (event.key === "Escape") {
    if (portrait.value) clearPortrait();
    else if (selected.value) void closeBook();
  }
}
function switchFormat(kind: Format) {
  format.value = kind;
  file.value = undefined;
  if (input.value) input.value.value = "";
}
async function upload() {
  if (!jobId.value || !file.value) return;
  busy.value = true;
  message.value = "";
  error.value = "";
  importCheck++;
  if (importTimer) clearTimeout(importTimer);
  try {
    const data = new FormData();
    data.append("file", file.value);
    const imported = await api.upload<{
      application_id: number;
      version_id: number;
    }>(
      "/applications?job_id=" +
        jobId.value +
        (previous.value ? "&candidate_application_id=" + previous.value : ""),
      data,
    );
    message.value = "简历已导入，后台正在解析。";
    showImport.value = false;
    switchFormat(format.value);
    query.value = "";
    currentQuery.value = "";
    tags.value = [];
    stageFilter.value = "";
    filterJob.value = jobId.value;
    selectJob();
    if (!isUnmounted) trackImport(imported);
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
onMounted(async () => {
  window.addEventListener("keydown", esc);
  // 每页数量仅在进入页面时按视口高度计算一次，此后固定；窗口缩放由用户刷新或手动调整
  size.value = actualHeight();
  void load(1);
  try {
    const positions = await loadJobs();
    jobs.value = positions;
    if (
      !positions.some(
        (j) => j.id === jobId.value && j.status === "open" && j.manageable,
      )
    )
      jobId.value = "";
  } catch (e) {
    error.value = (e as Error).message;
  }
});
onBeforeUnmount(() => {
  isUnmounted = true;
  releaseAvatars();
  if (refreshTimer) clearTimeout(refreshTimer);
  if (importTimer) clearTimeout(importTimer);
  window.removeEventListener("keydown", esc);
});
</script>

<template>
  <main class="resume-workspace">
    <div
      class="resume-layout"
      :class="{ split: selected }"
      :style="{ '--paper-ratio': paperRatio }"
    >
      <div class="resume-list">
        <header class="workspace-heading">
          <div>
            <span class="workspace-kicker">招聘工作台 / 候选人</span>
            <h1>简历管理</h1>
            <p>按岗位、经历和标签查找候选人。</p>
          </div>
          <div class="heading-actions">
            <button class="rs-button" type="button" @click="load()">
              刷新列表
            </button>
            <button
              class="rs-button primary"
              type="button"
              :aria-expanded="showImport"
              @click="showImport = !showImport"
            >
              {{ showImport ? "收起导入" : "＋ 导入简历" }}
            </button>
          </div>
        </header>
        <p v-if="error" class="workspace-notice danger" role="alert">
          {{ error }}
        </p>
        <p v-if="message" class="workspace-notice" role="status">
          {{ message }}
        </p>
        <form v-if="showImport" class="import-panel" @submit.prevent="upload">
          <div class="format-tabs" aria-label="导入方式">
            <button
              v-for="(item, key) in formats"
              :key="key"
              type="button"
              :class="{ active: format === key }"
              :aria-pressed="format === key"
              @click="switchFormat(key)"
            >
              {{ item.title }}
            </button>
          </div>
          <label
            >招聘岗位<select v-model="jobId" required>
              <option value="">选择岗位</option>
              <option
                v-for="job in jobs.filter(
                  (j) => j.status === 'open' && j.manageable,
                )"
                :key="job.id"
                :value="job.id"
              >
                {{ job.title }}
              </option>
            </select></label
          >
          <label class="file-label"
            >{{ formats[format].title }} 文件<input
              ref="input"
              type="file"
              :accept="formats[format].accept"
              required
              @change="file = ($event.target as HTMLInputElement).files?.[0]"
          /></label>
          <label
            >关联申请（可选）<input
              v-model="previous"
              type="number"
              min="1"
              placeholder="已有申请编号"
          /></label>
          <button class="rs-button primary" type="submit" :disabled="busy">
            {{ busy ? "上传中…" : "确认导入" }}
          </button>
        </form>
        <form class="filter-bar" @submit.prevent="search">
          <label class="keyword"
            ><span aria-hidden="true">⌕</span
            ><input
              v-model="query"
              aria-label="搜索简历"
              placeholder="搜索姓名、岗位或经历关键词"
          /></label>
          <button class="rs-button primary" type="submit">搜索</button>
          <div class="filter-selects">
            <select
              v-model="filterJob"
              aria-label="筛选岗位"
              @change="selectJob"
            >
              <option value="">全部岗位</option>
              <option v-for="job in jobs" :key="job.id" :value="job.id">
                {{ job.title }}（{{ job.application_count }}）
              </option>
            </select>
            <select
              v-model="stageFilter"
              aria-label="筛选招聘状态"
              @change="search"
            >
              <option value="">全部招聘状态</option>
              <option
                v-for="item in availableStages"
                :key="stageKey(item)"
                :value="stageKey(item)"
              >
                {{ stageDisplay(item) }}
              </option>
            </select>
            <select
              v-if="schoolFilters"
              aria-label="添加院校标签筛选"
              value=""
              @change="
                (event) => {
                  const input = event.target as HTMLSelectElement;
                  if (
                    input.value &&
                    !tags.some((t) => t.id === 'school:' + input.value)
                  ) {
                    toggleTag({
                      id: 'school:' + input.value,
                      type: 'school',
                      value: input.value,
                      filterable: true,
                    });
                  }
                  input.value = '';
                }
              "
            >
              <option value="">院校标签</option>
              <option
                v-for="(name, key) in schoolTagLabels"
                :key="key"
                :value="key"
              >
                {{ name }}
              </option>
            </select>
          </div>
        </form>
        <div
          v-if="scopeDepartment || scopeParse || scopeStalled"
          class="active-filters"
        >
          <span
            >{{
              scopeStalled
                ? "7 天未推进"
                : scopeParse
                  ? "解析失败"
                  : "指定部门"
            }}{{
              (scopeParse || scopeStalled) && scopeDepartment
                ? "（指定部门）"
                : ""
            }}</span
          >
          <button
            type="button"
            class="clear-filters"
            @click="
              scopeDepartment = null;
              scopeParse = null;
              scopeStalled = false;
              selectJob();
            "
          >
            清除此范围
          </button>
        </div>
        <div
          v-if="tags.length"
          class="active-filters"
          aria-label="当前标签条件"
        >
          <span>同时满足</span
          ><button
            v-for="tag in tags"
            :key="tag.id"
            class="active-filter"
            type="button"
            :aria-label="'移除 ' + tag.name"
            @click="removeTag(tag.id)"
          >
            {{ tag.name }} ×
          </button>
          <button
            type="button"
            class="clear-filters"
            @click="
              tags = [];
              search();
            "
          >
            清除标签
          </button>
        </div>
        <div class="workspace-body">
          <div
            ref="cardViewport"
            class="card-viewport"
            aria-label="候选人列表"
            :aria-busy="loading"
          >
            <div v-if="!rows.length" class="list-empty">
              <strong>{{
                loading ? "正在查找简历…" : "没有符合条件的简历"
              }}</strong>
              <p>
                {{ loading ? "" : "可以调整关键词、移除标签，或导入新简历。" }}
              </p>
            </div>
            <div
              v-else
              class="candidate-list"
              :class="{ fill: mode === 'auto' }"
              :style="{ '--slots': size }"
            >
              <article
                v-for="row in rows"
                :key="row.id"
                class="candidate-row"
                :class="{ chosen: selected?.id === row.id }"
                @click="openCard(row)"
              >
                <button
                  class="candidate-avatar"
                  type="button"
                  :aria-label="'放大头像：' + (row.name || '申请 ' + row.id)"
                  @click.stop="openPortrait(row)"
                >
                  <img
                    v-if="avatars[avatarKey(row)]"
                    :src="avatars[avatarKey(row)]"
                    alt=""
                  />
                  <span v-else>{{ initials(row) }}</span>
                </button>
                <div class="candidate-main">
                  <div class="candidate-title">
                    <button
                      type="button"
                      class="candidate-name"
                      @click.stop="openCard(row)"
                    >
                      {{ row.name || "申请 #" + row.id }}
                    </button>
                  </div>
                  <dl class="candidate-facts">
                    <div>
                      <dt>求职岗位</dt>
                      <dd :title="row.target_role || row.job_title">
                        {{ row.target_role || row.job_title }}
                      </dd>
                    </div>
                    <div>
                      <dt>性别</dt>
                      <dd>{{ genders[row.gender || ""] || "—" }}</dd>
                    </div>
                    <div>
                      <dt>手机号</dt>
                      <dd>{{ row.phone || "—" }}</dd>
                    </div>
                    <div>
                      <dt>地址</dt>
                      <dd :title="row.address || row.city || undefined">
                        {{ row.address || row.city || "—" }}
                      </dd>
                    </div>
                  </dl>
                  <div class="candidate-tags" aria-label="简历标签">
                    <template v-for="tag in row.tags" :key="tag.id">
                      <button
                        v-if="tag.filterable"
                        type="button"
                        class="candidate-tag"
                        :class="[
                          tag.type,
                          { 'is-selected': tags.some((t) => t.id === tag.id) },
                        ]"
                        :aria-pressed="tags.some((t) => t.id === tag.id)"
                        @click.stop="toggleTag(tag)"
                      >
                        {{ tagText(tag) }}
                      </button>
                      <span
                        v-else
                        class="candidate-tag"
                        :class="tag.type"
                        title="当前字段未开放筛选"
                        >{{ tagText(tag) }}</span
                      >
                    </template>
                    <span v-if="!row.tags.length" class="no-tags"
                      >暂无已核实标签</span
                    >
                  </div>
                </div>
                <div class="card-tail">
                  <div class="card-stage">
                    <select
                      class="candidate-status"
                      :class="row.stage.code"
                      :disabled="savingStage || !jobs.length"
                      :aria-label="'更新招聘状态：' + (row.name || row.id)"
                      :value="
                        editStage?.id === row.id
                          ? editStage.choice
                          : stageKey(row.stage)
                      "
                      @click.stop
                      @change="selectStage(row, $event)"
                    >
                      <option
                        v-for="item in stagesForCard(row)"
                        :key="stageKey(item)"
                        :value="stageKey(item)"
                      >
                        {{ stageDisplay(item) }}
                      </option>
                    </select>
                    <button
                      v-if="editStage?.id === row.id"
                      class="stage-save"
                      type="button"
                      :disabled="savingStage"
                      @click.stop="saveStage(row)"
                    >
                      保存状态
                    </button>
                  </div>
                  <div class="card-links">
                    <RouterLink
                      class="details-link"
                      :to="'/applications/' + row.id"
                      :aria-label="'申请详情：' + (row.name || row.id)"
                      @click.stop
                      >详情 ›</RouterLink
                    >
                    <RouterLink
                      class="details-link"
                      :to="'/applications/' + row.id + '#transfer'"
                      :aria-label="'调剂：' + (row.name || row.id)"
                      @click.stop
                      >调剂</RouterLink
                    >
                  </div>
                </div>
              </article>
            </div>
          </div>
        </div>
        <footer class="page-footer">
          <span aria-live="polite"
            >共 <strong>{{ total }}</strong> 份 · 本页 {{ visibleCount }} 份 /
            {{ size }} 位</span
          >
          <div>
            <label
              >每页
              <select
                v-model="mode"
                aria-label="每页简历数量"
                @change="updateSize"
              >
                <option value="auto">
                  自动（{{ mode === "auto" ? size : actualHeight() }}）
                </option>
                <option
                  v-for="n in [3, 5, 10, 15, 20, 30, 50, 100]"
                  :key="n"
                  :value="String(n)"
                >
                  {{ n }} 份
                </option>
                <option value="custom">自定义</option>
              </select></label
            >
            <input
              v-if="mode === 'custom'"
              class="size-number"
              v-model.number="customSize"
              type="number"
              min="1"
              max="100"
              aria-label="自定义每页数量"
              @change="adjust()"
            />
            <button
              class="rs-button"
              type="button"
              aria-label="上一页简历"
              :disabled="page <= 1 || loading"
              @click="changePage(page - 1)"
            >
              ‹
            </button>
            <span>{{ page }} / {{ maxPage }}</span>
            <button
              class="rs-button"
              type="button"
              aria-label="下一页简历"
              :disabled="page >= maxPage || loading"
              @click="changePage(page + 1)"
            >
              ›
            </button>
          </div>
        </footer>
      </div>
      <ResumeBook
        v-if="selected"
        :resume="selected"
        :avatar-url="avatars[avatarKey(selected)]"
        @close="closeBook"
        @fullscreen-change="onFullscreenChange"
      />
    </div>
    <Teleport to="body">
      <div
        v-if="portrait"
        class="portrait-overlay"
        role="presentation"
        @click.self="clearPortrait"
      >
        <section
          class="portrait-dialog"
          role="dialog"
          aria-modal="true"
          aria-label="头像预览"
        >
          <button
            ref="modalClose"
            class="portrait-close"
            type="button"
            aria-label="关闭头像"
            @click="clearPortrait"
          >
            ×
          </button>
          <img
            v-if="avatars[avatarKey(portrait)]"
            :src="avatars[avatarKey(portrait)]"
            :alt="(portrait.name || '候选人') + ' 的简历头像'"
          />
          <span v-else class="portrait-placeholder">{{
            initials(portrait)
          }}</span>
          <p>{{ portrait.name || "申请 #" + portrait.id }}</p>
        </section>
      </div>
    </Teleport>
  </main>
</template>

<style scoped>
.resume-workspace {
  height: 100%;
  min-height: 0;
  min-width: 0;
  container-type: size;
  background: #f3f6fb;
}
.resume-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  height: 100%;
  min-height: 0;
}
/* 选中简历后右侧详情栏固定按页面百分比展开（42%），列表列宽恒定不重排 */
.resume-layout.split {
  grid-template-columns: minmax(0, 1fr) minmax(400px, 42%);
  gap: 14px;
}
.resume-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 20px clamp(14px, 2.3vw, 34px) 12px;
  color: #162440;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.resume-layout > :deep(.resume-reader:not(.resume-fullscreen)) {
  min-height: 0;
  height: 100%;
  border-radius: 14px;
}

.workspace-heading,
.page-footer,
.heading-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.workspace-heading h1 {
  margin: 2px 0 3px;
  font-size: 30px;
  letter-spacing: -0.025em;
  line-height: 1.16;
}
.workspace-heading p {
  margin: 0;
  color: #596982;
  font-size: 13px;
}
.workspace-kicker {
  color: #5269a2;
  font-size: 11px;
  letter-spacing: 0.08em;
  font-weight: 750;
}
.workspace-notice {
  margin: 0;
  color: #15574c;
  background: #e2f3ed;
  padding: 7px 12px;
  border-radius: 8px;
  font-size: 13px;
}
.workspace-notice.danger {
  color: #9b2525;
  background: #fce9e9;
}
.filter-bar,
.import-panel {
  display: flex;
  align-items: flex-end;
  gap: 9px;
  border: 1px solid #e0e7f2;
  background: #fff;
  border-radius: 14px;
  padding: 9px 11px;
}
.filter-bar {
  flex-wrap: wrap;
}
.filter-selects {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}
.filter-selects select {
  max-width: 185px;
}
.keyword {
  display: flex;
  align-items: center;
  flex: 1;
  gap: 7px;
  min-width: 70px;
  color: #627798;
  font-size: 21px;
}
.keyword input {
  border: 0;
  outline: none;
  width: 100%;
  min-width: 0;
  font: inherit;
  font-size: 14px;
  height: 34px;
  color: #1f3355;
}
.keyword:focus-within {
  box-shadow: 0 2px #6783d6;
}
.filter-bar select,
.page-footer select,
.import-panel input,
.import-panel select {
  height: 35px;
  border: 1px solid #d9e2f1;
  background: #fff;
  color: #263a5c;
  padding: 0 8px;
  border-radius: 8px;
  max-width: 100%;
}
.import-panel {
  flex-wrap: wrap;
}
.import-panel label {
  display: grid;
  gap: 4px;
  font-size: 12px;
  color: #4e607e;
}
.import-panel input[type="file"] {
  max-width: 240px;
  padding: 5px;
}
.format-tabs {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px;
  background: #edf2fa;
  border-radius: 8px;
}
.format-tabs button,
.active-filter,
.clear-filters {
  padding: 5px 9px;
  background: none;
  border: 0;
  border-radius: 6px;
  color: #52688b;
  cursor: pointer;
}
.format-tabs button.active,
.active-filter {
  color: #2648a0;
  background: #e3ebff;
  font-weight: 700;
}
.active-filters {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  padding: 7px 11px;
  background: #edf0f4;
  border: 1px solid #dfe4ea;
  border-radius: 8px;
  font-size: 12px;
  color: #536584;
}
.active-filters button {
  border-radius: 0;
}
.active-filter {
  border: 1px solid #c3d1e6;
  background: #f6f9ff;
}
.workspace-body {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  flex: 1;
  gap: 14px;
  min-height: 0;
}
.card-viewport {
  min-height: 0;
  min-width: 0;
  overflow: auto;
  scrollbar-gutter: stable;
  /* 行高恒定：选中态 / 详情栏开关不改变行尺寸 */
  --row-height: 116px;
}
/* 候选列表合并为一张整体面板：行间用分隔线，不再每行一个独立框 */
.candidate-list {
  display: grid;
  border: 1px solid #e0e8f3;
  border-radius: 14px;
  background: #fff;
  overflow: hidden;
  box-shadow: 0 1px 3px #1a34550a;
}
.candidate-list.fill {
  height: 100%;
  grid-template-rows: repeat(var(--slots), minmax(var(--row-height), 1fr));
}
.candidate-row {
  display: grid;
  grid-template-columns: 56px minmax(0, 1fr) 140px;
  align-items: center;
  gap: 15px;
  min-height: var(--row-height);
  padding: 12px 18px;
  border-bottom: 1px solid #eef2f8;
  background: #fff;
  cursor: pointer;
  transition: background-color 0.15s;
}
.candidate-row:last-child {
  border-bottom: 0;
}
.candidate-row:hover,
.candidate-row:focus-within {
  background: #f7faff;
}
.candidate-row.chosen {
  background: #f3f7ff;
  box-shadow: inset 3px 0 0 #4e6fa4;
}
.candidate-avatar {
  width: 54px;
  height: 54px;
  display: grid;
  place-items: center;
  overflow: hidden;
  border: 0;
  border-radius: 15px;
  background: linear-gradient(145deg, #e6eeff, #d8e5fa);
  color: #36568e;
  font-weight: 800;
  font-size: 17px;
  cursor: zoom-in;
}
.candidate-avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.candidate-main {
  min-width: 0;
}
.candidate-title {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 0;
}
.candidate-name {
  color: #172b4d;
  background: none;
  border: 0;
  padding: 0;
  font-size: 17px;
  font-weight: 800;
  cursor: pointer;
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
  max-width: 100%;
}
.candidate-status {
  border: 1px solid transparent;
  cursor: pointer;
  width: 100%;
  max-width: 158px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #495d78;
  font-size: 10px;
  background: #eef2f7;
  border-radius: 5px;
  padding: 4px 6px;
}
.candidate-status.progress,
.candidate-status.interview {
  color: #3758a3;
  background: #edf2ff;
}
.candidate-status.rejected {
  color: #a23d42;
  background: #f9e8e9;
}
.stage-save {
  border: 0;
  border-radius: 5px;
  color: #284e9f;
  background: #e7eeff;
  font-size: 11px;
  padding: 3px 6px;
  white-space: nowrap;
  cursor: pointer;
}
.candidate-status.hired {
  color: #20715c;
  background: #e6f6ef;
}
.candidate-facts {
  display: grid;
  grid-template-columns: minmax(105px, 1.25fr) minmax(43px, 0.55fr) minmax(
      100px,
      0.85fr
    ) minmax(95px, 1.2fr);
  gap: 7px;
  margin: 6px 0;
  font-size: 12px;
}
.candidate-facts div {
  min-width: 0;
  display: flex;
  gap: 6px;
}
.candidate-facts dt {
  flex: none;
  color: #62718a;
}
.candidate-facts dd {
  margin: 0;
  color: #243650;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.candidate-tags {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  overflow-x: auto;
  scrollbar-width: none;
}
.candidate-tags::-webkit-scrollbar {
  display: none;
}
.candidate-tag {
  display: inline-block;
  padding: 3px 7px;
  border: 1px solid transparent;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 650;
  white-space: nowrap;
  line-height: 1.15;
  background: #eef2f8;
  color: #435978;
}
button.candidate-tag {
  cursor: pointer;
}
button.candidate-tag:hover,
button.candidate-tag.is-selected {
  border-color: #7893d7;
}
.candidate-tag.degree {
  color: #3758a3;
  background: #edf2ff;
}
.candidate-tag.school {
  color: #795513;
  background: #fff4d8;
}
.candidate-tag.language {
  color: #385098;
  background: #e8edff;
}
.candidate-tag.capability {
  color: #206878;
  background: #e3f4f7;
}
.candidate-tag.domain {
  color: #226959;
  background: #e7f6ef;
}
.candidate-tag.experience {
  color: #6c489c;
  background: #f2ebfa;
}
.no-tags {
  color: #62718a;
  font-size: 11px;
}
.card-tail {
  display: grid;
  gap: 7px;
  align-content: center;
  justify-items: end;
}
.card-stage {
  display: grid;
  width: 100%;
  justify-items: end;
  gap: 4px;
}
.card-links {
  display: flex;
  gap: 9px;
  justify-content: flex-end;
}
.details-link {
  font-size: 12px;
  color: #3858a1;
  white-space: nowrap;
}
/* split（选中详情）模式下行的密度与普通模式完全一致：尺寸恒定，不因选中而变化 */
.page-footer {
  min-height: 37px;
  padding: 0 1px;
  font-size: 12px;
  color: #4d5f7b;
}
.page-footer > div {
  display: flex;
  align-items: center;
  gap: 8px;
}
.page-footer label {
  white-space: nowrap;
}
.page-footer button {
  min-width: 33px;
}
.size-number {
  width: 55px;
  height: 33px;
  padding: 4px;
  border: 1px solid #d9e2f1;
  border-radius: 7px;
  background: #fff;
  color: #263a5c;
}
.list-empty {
  display: grid;
  place-content: center;
  height: 100%;
  text-align: center;
  color: #546884;
  border: 1px dashed #d5e1f2;
  border-radius: 16px;
}
.list-empty p {
  margin: 5px 0;
  font-size: 13px;
}
.portrait-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: grid;
  place-items: center;
  background: #121e39bf;
  padding: 2vh 2vw;
}
.portrait-dialog {
  position: relative;
  display: flex;
  align-items: center;
  flex-direction: column;
  width: min(86vw, 84vh);
  padding: 12px;
  border-radius: 22px;
  background: #f8fbff;
  box-shadow: 0 20px 80px #09162975;
  color: #294775;
}
.portrait-dialog img,
.portrait-placeholder {
  width: 100%;
  height: min(78vh, 82vw);
  object-fit: contain;
  border-radius: 15px;
}
.portrait-placeholder {
  display: grid;
  place-items: center;
  background: linear-gradient(135deg, #dce8ff, #bcd1ef);
  color: #36568e;
  font-weight: 800;
  font-size: clamp(70px, 22vmin, 200px);
}
.portrait-close {
  position: absolute;
  top: 17px;
  right: 17px;
  width: 42px;
  height: 42px;
  background: #fff;
  color: #15263e;
  border: 0;
  border-radius: 50%;
  font-size: 25px;
  cursor: pointer;
}
.portrait-dialog p {
  margin: 7px 0 0;
  font-weight: 700;
}
@container (max-width: 1000px) {
  .split .card-links {
    display: none;
  }
}
@container (max-width: 710px) {
  .resume-layout.split {
    position: relative;
    grid-template-columns: minmax(0, 1fr);
  }
  .split .resume-list {
    visibility: hidden;
    padding-right: clamp(14px, 2.3vw, 34px);
  }
  .resume-layout.split > :deep(.resume-reader:not(.resume-fullscreen)) {
    position: absolute;
    inset: 0;
    border-radius: 0;
  }
  .workspace-heading p,
  .workspace-kicker {
    display: none;
  }
  .workspace-heading h1 {
    font-size: 23px;
  }
  .filter-bar {
    flex-wrap: wrap;
  }
  .candidate-facts {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: 3px 6px;
  }
  .card-viewport {
    --row-height: 124px;
  }
  .candidate-row {
    grid-template-columns: 42px minmax(0, 1fr) 96px;
    gap: 7px;
    padding: 8px;
  }
  .candidate-avatar {
    width: 42px;
    height: 42px;
  }
  .candidate-status {
    font-size: 10px;
  }
}
@media (prefers-reduced-motion: reduce) {
  .candidate-row {
    transition: none;
  }
}
</style>
