<template>
  <details ref="panel" class="border-t border-amber-200 bg-amber-50 px-4 py-3" data-testid="chat-research">
    <summary class="mx-auto max-w-4xl cursor-pointer text-sm font-medium text-slate-800">
      发起复杂院校研究
    </summary>
    <section class="mx-auto mt-3 max-w-4xl space-y-3" aria-label="复杂招生研究请求">
      <p class="text-xs leading-5 text-slate-600">
        研究请求会进入现有私有 TaskRun，由运营人员审核后才可能执行。此表单不会直接调用模型；结果只包含经校验的历史汇总，不构成录取预测。
        请先在数据浏览器确认院校和专业 ID。当前接口仅在已配置的非生产授权环境开放。
      </p>
      <p v-if="!chat.currentSessionId || !chat.sessionToken" class="text-xs text-slate-700" role="status">
        请先在本会话发送一条消息以建立会话凭证。
      </p>
      <div class="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <label class="text-xs">搜索已授权数据中的院校
          <input v-model.trim="schoolQuery" maxlength="100" class="mt-1 w-full rounded border border-slate-300 bg-white p-2" />
        </label>
        <label class="text-xs">搜索已授权数据中的专业
          <input v-model.trim="majorQuery" maxlength="100" class="mt-1 w-full rounded border border-slate-300 bg-white p-2" />
        </label>
        <button type="button" data-testid="research-options-search" :disabled="busy || !canSubmit" class="w-fit rounded border border-slate-400 px-3 py-2 text-sm disabled:opacity-50 sm:col-span-2" @click="loadOptions">
          搜索可用范围
        </button>
      </div>
      <form class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4" @submit.prevent="submit">
        <label class="text-xs">院校 A ID
          <select v-model.number="schoolA" data-testid="research-school-a" required class="mt-1 w-full rounded border border-slate-300 bg-white p-2">
            <option value="" disabled>请选择院校</option><option v-for="school in options.schools" :key="school.id" :value="school.id">{{ school.name }}（{{ school.id }}）</option>
          </select>
        </label>
        <label class="text-xs">院校 B ID
          <select v-model.number="schoolB" data-testid="research-school-b" required class="mt-1 w-full rounded border border-slate-300 bg-white p-2">
            <option value="" disabled>请选择院校</option><option v-for="school in options.schools" :key="school.id" :value="school.id">{{ school.name }}（{{ school.id }}）</option>
          </select>
        </label>
        <label class="text-xs">专业 ID
          <select v-model.number="majorId" data-testid="research-major" required class="mt-1 w-full rounded border border-slate-300 bg-white p-2">
            <option value="" disabled>请选择专业</option><option v-for="major in options.majors" :key="major.id" :value="major.id">{{ major.name }}（{{ major.id }}）</option>
          </select>
        </label>
        <label class="text-xs">省份
          <input v-model.trim="province" data-testid="research-province" maxlength="20" required class="mt-1 w-full rounded border border-slate-300 bg-white p-2" />
        </label>
        <label class="text-xs">科类
          <input v-model.trim="subjectType" data-testid="research-subject" maxlength="10" required class="mt-1 w-full rounded border border-slate-300 bg-white p-2" />
        </label>
        <label class="text-xs">批次
          <input v-model.trim="batch" data-testid="research-batch" maxlength="20" required class="mt-1 w-full rounded border border-slate-300 bg-white p-2" />
        </label>
        <label class="text-xs">起始年份
          <input v-model.number="yearFrom" data-testid="research-year-from" type="number" min="2000" max="2100" required class="mt-1 w-full rounded border border-slate-300 bg-white p-2" />
        </label>
        <label class="text-xs">结束年份
          <input v-model.number="yearTo" data-testid="research-year-to" type="number" min="2000" max="2100" required class="mt-1 w-full rounded border border-slate-300 bg-white p-2" />
        </label>
        <div class="flex flex-wrap items-center gap-2 sm:col-span-2 lg:col-span-4">
          <button type="submit" :disabled="busy || !canSubmit" class="rounded bg-slate-900 px-3 py-2 text-sm text-white disabled:cursor-not-allowed disabled:opacity-50">
            {{ busy ? '提交中…' : '提交给运营审核' }}
          </button>
          <button v-if="active" type="button" :disabled="busy" class="rounded border border-slate-400 px-3 py-2 text-sm disabled:opacity-50" @click="cancel">
            取消任务
          </button>
          <button v-if="runId" type="button" :disabled="busy" class="rounded border border-slate-400 px-3 py-2 text-sm disabled:opacity-50" @click="refresh">
            刷新状态
          </button>
          <button v-if="hasReport" type="button" data-testid="research-report" :disabled="busy" class="rounded border border-slate-400 px-3 py-2 text-sm disabled:opacity-50" @click="loadReport">
            查看验证报告
          </button>
          <button v-if="['FAILED', 'CANCELLED', 'EXPIRED'].includes(status)" type="button" data-testid="research-resubmit" :disabled="busy" class="rounded border border-amber-700 px-3 py-2 text-sm text-amber-900 disabled:opacity-50" @click="resubmitForApproval">
            重新提交为新待审批任务
          </button>
        </div>
      </form>
      <p v-if="['FAILED', 'CANCELLED', 'EXPIRED'].includes(status)" class="text-xs text-amber-900">
        重新提交会创建新的 TaskRun，并再次等待运营审批；系统不会自动重放失败任务。
      </p>
      <p v-if="error" role="alert" class="text-sm text-red-700">{{ error }}</p>
      <div v-if="runId" class="space-y-1 rounded border border-slate-200 bg-white p-3 text-xs" aria-live="polite" data-testid="research-status">
        <p>任务：<code>{{ runId }}</code></p>
        <p>状态：{{ statusLabel }}<span v-if="stage"> · {{ stageLabel }}</span></p>
        <p v-if="failureCode" class="text-red-700">失败代码：{{ failureCode }}</p>
        <pre v-if="report" class="max-h-64 overflow-auto whitespace-pre-wrap border-t pt-2">{{ report }}</pre>
      </div>
    </section>
  </details>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { chatResearchAPI } from '../../api/client'
import { useChatStore } from '../../stores/chat'

const chat = useChatStore()
const panel = ref(null)
const schoolA = ref('')
const schoolB = ref('')
const majorId = ref('')
const province = ref('')
const subjectType = ref('')
const batch = ref('')
const yearFrom = ref('')
const yearTo = ref('')
const schoolQuery = ref('')
const majorQuery = ref('')
const options = ref({ schools: [], majors: [] })
const runId = ref('')
const status = ref('')
const stage = ref('')
const failureCode = ref('')
const report = ref('')
const error = ref('')
const busy = ref(false)
let lastFingerprint = ''
let idempotencyKey = ''
let streamController = null
const terminal = ['SUCCEEDED', 'NEEDS_REVIEW', 'FAILED', 'CANCELLED', 'EXPIRED']
const active = computed(() => runId.value && !terminal.includes(status.value))
const hasReport = computed(() => ['SUCCEEDED', 'NEEDS_REVIEW'].includes(status.value))
const canSubmit = computed(() => Boolean(chat.currentSessionId && chat.sessionToken))
const latestAssistant = computed(() => [...chat.messages].reverse().find(message => message.role === 'assistant'))
const statusLabel = computed(() => ({
  PENDING_APPROVAL: '待运营人员审批',
  APPROVED: '已审批，等待授权 Worker',
  RUNNING: '受控 Deep Agents 执行中',
  SUCCEEDED: '证据核验通过',
  NEEDS_REVIEW: '部分结果待人工复核',
  FAILED: '执行失败',
  CANCELLED: '已取消',
  EXPIRED: '任务过期，未自动重放',
}[status.value] || status.value))
const stageLabel = computed(() => ({
  awaiting_operator_approval: '等待审批',
  awaiting_authorized_worker: '等待 Worker',
  deep_agents_execution: '执行中',
  verified_report_ready: '报告可查看',
  partial_result_requires_review: '需人工复核',
  execution_failed: '执行失败',
  cancelled: '已取消',
  expired_without_replay: '已过期且未重放',
}[stage.value] || stage.value))

watch(() => chat.currentSessionId, () => {
  streamController?.abort()
  streamController = null
  runId.value = ''
  status.value = ''
  stage.value = ''
  failureCode.value = ''
  report.value = ''
  error.value = ''
})

watch(() => latestAssistant.value?.researchIntent, async intent => {
  if (!intent?.enabled) return
  await nextTick()
  if (panel.value) panel.value.open = true
}, { immediate: true })

onBeforeUnmount(() => streamController?.abort())

function scope() {
  const ids = [Number(schoolA.value), Number(schoolB.value)]
  const from = Number(yearFrom.value)
  const to = Number(yearTo.value)
  if (!ids.every(id => Number.isSafeInteger(id) && id > 0) || ids[0] === ids[1] ||
      !Number.isSafeInteger(Number(majorId.value)) || Number(majorId.value) < 1 ||
      !Number.isInteger(from) || !Number.isInteger(to) || from < 2000 || to > 2100 || to < from || to - from > 4 ||
      !province.value || !subjectType.value || !batch.value) {
    throw new Error('请填写两个不同的院校 ID、专业 ID、完整科类范围及最多五个连续年份。')
  }
  return {
    school_ids: ids,
    major_id: Number(majorId.value),
    province: province.value,
    subject_type: subjectType.value,
    batch: batch.value,
    year_from: from,
    year_to: to,
  }
}

async function follow(run, sessionId, token) {
  streamController?.abort()
  streamController = new AbortController()
  try {
    await chatResearchAPI.follow({
      runId: run,
      sessionId,
      token,
      signal: streamController.signal,
      onEvent(event) {
        if (event.type !== 'progress' || event.run_id !== run ||
            chat.currentSessionId !== sessionId || chat.sessionToken !== token || runId.value !== run) return
        status.value = event.status
        stage.value = event.stage
        failureCode.value = event.failure_code || ''
      },
    })
  } catch (err) {
    if (err?.name !== 'AbortError') error.value = err?.message || '进度流暂时不可用，可刷新状态。'
  }
}

async function submit() {
  if (busy.value || !canSubmit.value) return
  const sessionId = chat.currentSessionId
  const token = chat.sessionToken
  busy.value = true
  error.value = ''
  report.value = ''
  try {
    const requestScope = scope()
    const fingerprint = JSON.stringify({ session: sessionId, scope: requestScope })
    if (fingerprint !== lastFingerprint) {
      lastFingerprint = fingerprint
      idempotencyKey = globalThis.crypto?.randomUUID?.() || `chat-research-${Date.now().toString(16)}`
    }
    const { data, error: apiError } = await chatResearchAPI.create({
      sessionId,
      scope: requestScope,
      idempotencyKey,
      token,
    })
    if (apiError) throw new Error(apiError.message || '提交研究请求失败。')
    if (chat.currentSessionId !== sessionId || chat.sessionToken !== token) return
    runId.value = data.run_id
    status.value = data.status
    stage.value = 'awaiting_operator_approval'
    void follow(data.run_id, sessionId, token)
  } catch (err) {
    if (chat.currentSessionId === sessionId && chat.sessionToken === token) {
      error.value = err?.message || '研究请求失败。'
    }
  } finally {
    busy.value = false
  }
}

async function loadOptions() {
  if (busy.value || !canSubmit.value) return
  const sessionId = chat.currentSessionId
  const token = chat.sessionToken
  busy.value = true
  error.value = ''
  try {
    const { data, error: apiError } = await chatResearchAPI.options({
      sessionId,
      schoolQuery: schoolQuery.value,
      majorQuery: majorQuery.value,
      token,
    })
    if (apiError) throw new Error(apiError.message || '无法读取已授权数据目录。')
    if (chat.currentSessionId !== sessionId || chat.sessionToken !== token) return
    options.value = data
  } catch (err) {
    if (chat.currentSessionId === sessionId && chat.sessionToken === token) {
      error.value = err?.message || '无法读取已授权数据目录。'
    }
  } finally {
    busy.value = false
  }
}

function resubmitForApproval() {
  lastFingerprint = ''
  idempotencyKey = ''
  void submit()
}

async function refresh() {
  if (busy.value || !runId.value || !canSubmit.value) return
  const sessionId = chat.currentSessionId
  const token = chat.sessionToken
  const requestedRunId = runId.value
  busy.value = true
  error.value = ''
  try {
    const { data, error: apiError } = await chatResearchAPI.get(requestedRunId, sessionId, token)
    if (apiError) throw new Error(apiError.message || '读取任务状态失败。')
    if (chat.currentSessionId !== sessionId || chat.sessionToken !== token || runId.value !== requestedRunId) return
    status.value = data.status
    failureCode.value = data.failure_code || ''
    stage.value = data.status === 'RUNNING' ? 'deep_agents_execution' : ''
  } catch (err) {
    if (chat.currentSessionId === sessionId && chat.sessionToken === token && runId.value === requestedRunId) {
      error.value = err?.message || '读取任务状态失败。'
    }
  } finally {
    busy.value = false
  }
}

async function cancel() {
  if (busy.value || !runId.value || !canSubmit.value) return
  const sessionId = chat.currentSessionId
  const token = chat.sessionToken
  const requestedRunId = runId.value
  busy.value = true
  error.value = ''
  try {
    const { data, error: apiError } = await chatResearchAPI.cancel(requestedRunId, sessionId, token)
    if (apiError) throw new Error(apiError.message || '取消任务失败。')
    if (chat.currentSessionId !== sessionId || chat.sessionToken !== token || runId.value !== requestedRunId) return
    status.value = data.status
    stage.value = 'cancelled'
    streamController?.abort()
  } catch (err) {
    if (chat.currentSessionId === sessionId && chat.sessionToken === token && runId.value === requestedRunId) {
      error.value = err?.message || '取消任务失败。'
    }
  } finally {
    busy.value = false
  }
}

async function loadReport() {
  if (busy.value || !runId.value || !hasReport.value || !canSubmit.value) return
  const sessionId = chat.currentSessionId
  const token = chat.sessionToken
  const requestedRunId = runId.value
  busy.value = true
  error.value = ''
  try {
    const { data, error: apiError } = await chatResearchAPI.report(requestedRunId, sessionId, token)
    if (apiError) throw new Error(apiError.message || '读取验证报告失败。')
    if (chat.currentSessionId !== sessionId || chat.sessionToken !== token || runId.value !== requestedRunId) return
    report.value = data
  } catch (err) {
    if (chat.currentSessionId === sessionId && chat.sessionToken === token && runId.value === requestedRunId) {
      error.value = err?.message || '读取验证报告失败。'
    }
  } finally {
    busy.value = false
  }
}
</script>
