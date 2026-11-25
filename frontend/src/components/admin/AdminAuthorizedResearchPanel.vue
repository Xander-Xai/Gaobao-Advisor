<template>
  <section class="space-y-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm" data-testid="authorized-research-panel">
    <header>
      <h2 class="text-base font-semibold">授权招生研究 · Deep Agents / Sandbox</h2>
      <p class="text-xs text-slate-600">
        提交经授权的院校及专业范围，由另一位管理员审核不可变请求，再由独立 Worker 执行。
        不使用虚构院校作为业务数据，不收集考生个人资料，不从浏览器调用模型或 Docker。
      </p>
    </header>
    <p v-if="error" role="alert" class="text-sm text-red-700">{{ error }}</p>
    <form class="space-y-3" @submit.prevent="createRun">
      <div class="grid gap-3 sm:grid-cols-3">
        <label class="text-xs">院校 A ID<input v-model.number="schoolA" data-testid="research-school-a" type="number" min="1" required class="w-full rounded border p-2" /></label>
        <label class="text-xs">院校 B ID<input v-model.number="schoolB" data-testid="research-school-b" type="number" min="1" required class="w-full rounded border p-2" /></label>
        <label class="text-xs">专业 ID<input v-model.number="major" data-testid="research-major" type="number" min="1" required class="w-full rounded border p-2" /></label>
        <label class="text-xs">省份<input v-model.trim="province" data-testid="research-province" maxlength="20" required class="w-full rounded border p-2" /></label>
        <label class="text-xs">科类<input v-model.trim="subject" data-testid="research-subject" maxlength="10" required class="w-full rounded border p-2" /></label>
        <label class="text-xs">批次<input v-model.trim="batch" data-testid="research-batch" maxlength="20" required class="w-full rounded border p-2" /></label>
        <label class="text-xs">起始年份<input v-model.number="fromYear" data-testid="research-from" type="number" min="2000" max="2100" required class="w-full rounded border p-2" /></label>
        <label class="text-xs">结束年份<input v-model.number="toYear" data-testid="research-to" type="number" min="2000" max="2100" required class="w-full rounded border p-2" /></label>
        <label class="text-xs">批准的模型 ID<input v-model.trim="model" data-testid="research-model" placeholder="openai:MODEL" required class="w-full rounded border p-2" /></label>
      </div>
      <label class="block text-xs">批准的本地沙箱镜像摘要<input v-model.trim="image" data-testid="research-image" placeholder="sha256:..." required class="w-full rounded border p-2 font-mono" /></label>
      <label class="block text-xs">数据及模型授权记录编号（不要输入密钥）<input v-model.trim="approvalRef" data-testid="research-ref" maxlength="200" required class="w-full rounded border p-2" /></label>
      <button type="submit" data-testid="research-create" :disabled="busy" class="rounded bg-slate-900 px-3 py-2 text-sm text-white disabled:opacity-50">创建待审批研究</button>
    </form>
    <div class="flex flex-wrap gap-2">
      <label class="w-full text-xs" for="research-run-id">查询已有研究任务（从任务发起人获取 ID）</label>
      <input id="research-run-id" v-model.trim="runId" data-testid="research-run-id" placeholder="research_..." class="min-w-0 flex-1 rounded border p-2 font-mono text-xs" />
      <button type="button" data-testid="research-refresh" :disabled="busy || !runId" class="rounded border px-3 py-2 text-xs" @click="refresh">查询状态</button>
    </div>
    <div class="space-y-2 rounded border border-slate-200 p-3">
      <div class="flex items-center justify-between gap-2">
        <h3 class="text-sm font-medium">待审核请求</h3>
        <button type="button" data-testid="research-queue-refresh" :disabled="busy" class="rounded border px-3 py-2 text-xs disabled:opacity-50" @click="refreshQueue">刷新队列</button>
      </div>
      <p v-if="queueLoaded && !queue.length" class="text-xs text-slate-600">当前没有待审批任务。</p>
      <ul v-if="queue.length" class="space-y-1" data-testid="research-queue">
        <li v-for="item in queue" :key="item.run_id">
          <button type="button" class="w-full rounded bg-slate-50 p-2 text-left text-xs hover:bg-slate-100" @click="selectQueued(item)">
            {{ item.run_id }} · {{ item.spec.scope.province }} {{ item.spec.scope.subject_type }} · {{ item.owner }}
          </button>
        </li>
      </ul>
    </div>
    <div v-if="task" class="space-y-3 rounded border p-3" data-testid="research-detail">
      <p class="text-sm font-semibold">状态：{{ task.status }} · 发起人：{{ task.owner }} · 审批人：{{ task.approved_by || '待审批' }}</p>
      <p class="break-all text-xs">请求摘要：{{ task.spec_sha256 }}</p>
      <pre class="max-h-56 overflow-auto whitespace-pre-wrap break-all rounded bg-slate-50 p-2 text-xs">{{ JSON.stringify(task.spec, null, 2) }}</pre>
      <p v-if="task.status === 'APPROVED'" class="text-xs text-amber-800">审批通过，等待授权 Worker。浏览器不触发付费执行。</p>
      <label v-if="canApprove" class="flex items-start gap-2 text-xs">
        <input v-model="reviewed" type="checkbox" data-testid="research-reviewed" />
        我已核对完整请求、外部授权记录、数据范围、模型预算及本地镜像。
      </label>
      <div class="flex flex-wrap gap-2">
        <button v-if="canApprove" type="button" data-testid="research-approve" :disabled="busy || !reviewed" class="rounded bg-slate-900 px-3 py-2 text-xs text-white disabled:opacity-50" @click="approve">审批当前摘要</button>
        <button v-if="canCancel" type="button" data-testid="research-cancel" :disabled="busy" class="rounded border px-3 py-2 text-xs" @click="cancel">取消任务</button>
        <button v-if="canReport" type="button" data-testid="research-report" :disabled="busy" class="rounded border px-3 py-2 text-xs" @click="loadReport">查看验证报告</button>
      </div>
      <p v-if="task.failure_code" class="text-xs text-red-700">{{ task.failure_code }}</p>
      <pre v-if="report" data-testid="research-report-content" class="max-h-96 overflow-auto whitespace-pre-wrap break-all rounded bg-slate-50 p-3 text-xs">{{ report }}</pre>
      <p class="text-xs text-amber-800">历史数据描述性研究，不是录取预测或志愿推荐。审批不证明数据准确性；失效任务不会自动重放付费调用。</p>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { AdminApiError, adminApi } from '../../api/admin'
import { useAdminStore } from '../../stores/admin'
import type { AuthorizedResearchCreate, AuthorizedResearchRun } from '../../types/admin'

const admin = useAdminStore()
const schoolA = ref<number | string>('')
const schoolB = ref<number | string>('')
const major = ref<number | string>('')
const province = ref('')
const subject = ref('')
const batch = ref('')
const fromYear = ref<number | string>('')
const toYear = ref<number | string>('')
const model = ref('')
const image = ref('')
const approvalRef = ref('')
const runId = ref('')
const task = ref<AuthorizedResearchRun | null>(null)
const queue = ref<AuthorizedResearchRun[]>([])
const queueLoaded = ref(false)
const report = ref('')
const reviewed = ref(false)
const busy = ref(false)
const error = ref('')
let lastRequest = ''
let lastKey = ''

const canApprove = computed(() => admin.isAdmin && task.value?.status === 'PENDING_APPROVAL' && task.value.owner !== admin.user?.username)
const canCancel = computed(() => Boolean(task.value && ['PENDING_APPROVAL', 'APPROVED', 'RUNNING'].includes(task.value.status)))
const canReport = computed(() => Boolean(task.value?.result && ['SUCCEEDED', 'NEEDS_REVIEW'].includes(task.value.status)))

function fail(err: unknown): void {
  error.value = err instanceof AdminApiError && [404, 503].includes(err.status)
    ? '授权研究功能、任务或私有数据配置暂不可用，请核对后端配置和权限。'
    : err instanceof Error ? err.message : '研究操作失败'
}

async function operate(action: () => Promise<AuthorizedResearchRun>): Promise<void> {
  if (busy.value || !admin.token) return
  busy.value = true
  error.value = ''
  report.value = ''
  reviewed.value = false
  try {
    const result = await action()
    task.value = result
    runId.value = result.run_id
  } catch (err) {
    task.value = null
    fail(err)
  } finally {
    busy.value = false
  }
}

function createRun(): void {
  if (!admin.token || busy.value) return
  const ids = [Number(schoolA.value), Number(schoolB.value), Number(major.value)]
  const start = Number(fromYear.value)
  const end = Number(toYear.value)
  if (!ids.every(id => Number.isSafeInteger(id) && id > 0) || ids[0] === ids[1] ||
      !Number.isInteger(start) || !Number.isInteger(end) || start < 2000 || end > 2100 || end < start || end - start > 4 ||
      !province.value || !subject.value || !batch.value || !approvalRef.value ||
      !/^openai:[A-Za-z0-9_.-]{2,100}$/.test(model.value) || !/^sha256:[0-9a-f]{64}$/.test(image.value)) {
    error.value = '请核对两个不同院校、专业、科类及不超过五年的范围，以及模型、镜像和授权编号。'
    return
  }
  const body: Omit<AuthorizedResearchCreate, 'idempotency_key'> = {
    scope: { school_ids: [ids[0], ids[1]], major_id: ids[2], province: province.value,
      subject_type: subject.value, batch: batch.value, year_from: start, year_to: end },
    model_id: model.value, image_id: image.value, approval_ref: approvalRef.value,
  }
  const fingerprint = JSON.stringify(body)
  if (fingerprint !== lastRequest) {
    lastRequest = fingerprint
    lastKey = globalThis.crypto?.randomUUID?.() ?? 'research-' + Date.now().toString(16)
  }
  const token = admin.token
  void operate(() => adminApi.createAuthorizedResearch({ ...body, idempotency_key: lastKey }, token))
}

function refresh(): void {
  if (!admin.token || !runId.value) return
  const token = admin.token
  const id = runId.value
  void operate(() => adminApi.getAuthorizedResearch(id, token))
}

async function refreshQueue(): Promise<void> {
  if (!admin.token || busy.value) return
  busy.value = true
  error.value = ''
  try {
    queue.value = await adminApi.listAuthorizedResearch('PENDING_APPROVAL', admin.token)
    queueLoaded.value = true
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

function selectQueued(item: AuthorizedResearchRun): void {
  runId.value = item.run_id
  task.value = item
  report.value = ''
  reviewed.value = false
  error.value = ''
}

function approve(): void {
  if (!admin.token || !task.value || !canApprove.value || !reviewed.value) return
  const token = admin.token
  const { run_id, spec_sha256 } = task.value
  void operate(() => adminApi.approveAuthorizedResearch(run_id, spec_sha256, token))
}

function cancel(): void {
  if (!admin.token || !task.value || !canCancel.value) return
  const token = admin.token
  const id = task.value.run_id
  void operate(() => adminApi.cancelAuthorizedResearch(id, token))
}

async function loadReport(): Promise<void> {
  if (!admin.token || !task.value || !canReport.value || busy.value) return
  busy.value = true
  error.value = ''
  report.value = ''
  try {
    report.value = await adminApi.authorizedResearchReport(task.value.run_id, admin.token)
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}
</script>
