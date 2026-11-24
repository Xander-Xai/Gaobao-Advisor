<template>
  <section class="space-y-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm" data-testid="agent-taskrun-panel">
    <header class="space-y-1">
      <h2 class="text-base font-semibold">Agent TaskRun · 任务与人工审批</h2>
      <p class="text-xs text-slate-600">
        任务创建、管理员复核、独立 Worker、状态与证据回执。默认仅处理 SYNTHETIC_DEMO，
        不访问真实考生信息。Deep Agents 不允许从浏览器启动付费模型。
      </p>
    </header>

    <p v-if="disabled" data-testid="taskrun-disabled" role="status" class="text-sm text-slate-600">
      实验默认关闭；需在开发环境配置 SYNTHETIC_TASKRUN_JOURNAL 和实验开关。
    </p>
    <p v-else-if="error" role="alert" data-testid="taskrun-error" class="text-sm text-red-700">{{ error }}</p>

    <div v-if="example" class="space-y-2">
      <div class="flex flex-wrap items-center gap-3">
        <label class="text-xs text-slate-600" for="agent-engine">自主执行引擎</label>
        <select id="agent-engine" v-model="engine" data-testid="taskrun-engine" class="rounded-md border border-slate-300 px-2 py-1 text-sm">
          <option value="deterministic">确定性基线（无模型费用）</option>
          <option value="native_harness">原生 Harness（只能由授权 Worker 启动）</option>
          <option value="deepagents">Deep Agents（只能由授权 Worker 启动）</option>
        </select>
        <button type="button" data-testid="taskrun-create" :disabled="busy" class="rounded-md bg-slate-900 px-3 py-2 text-sm text-white disabled:opacity-50" @click="createRun">
          创建待审批任务
        </button>
      </div>
      <p class="text-xs text-slate-500">
        冻结合成画像：{{ example.request.school_a }} / {{ example.request.school_b }} ·
        {{ example.request.major }} · {{ example.request.province }} · {{ example.request.score }} 分
      </p>
    </div>

    <div class="flex flex-wrap gap-2">
      <label class="w-full text-xs text-slate-600" for="taskrun-id">查询或审批已有 TaskRun（需持有其 ID）</label>
      <input
        id="taskrun-id"
        v-model.trim="runId"
        data-testid="taskrun-id"
        class="min-w-0 flex-1 rounded-md border border-slate-300 px-3 py-2 font-mono text-xs"
        placeholder="syn_..."
      />
      <button type="button" :disabled="busy || !runId" data-testid="taskrun-refresh" class="rounded-md border border-slate-300 px-3 py-2 text-xs" @click="refresh">刷新任务</button>
    </div>

    <div v-if="task" class="space-y-3 rounded-lg border border-slate-200 p-3" data-testid="taskrun-detail">
      <div class="flex flex-wrap items-center gap-2 text-xs">
        <span class="rounded-md bg-slate-100 px-2 py-1">状态：{{ task.status }}</span>
        <span class="rounded-md bg-slate-100 px-2 py-1">执行引擎：{{ task.engine }}</span>
        <span class="rounded-md bg-slate-100 px-2 py-1">尝试：{{ task.attempts }}</span>
        <span class="rounded-md bg-slate-100 px-2 py-1">发起人：{{ task.owner }}</span>
        <span class="rounded-md bg-slate-100 px-2 py-1">审批人：{{ task.approved_by ?? '待审批' }}</span>
      </div>

      <div class="flex flex-wrap gap-2">
        <button
          v-if="admin.isAdmin && task.status === 'PENDING_APPROVAL' && task.owner !== admin.user?.username"
          type="button"
          data-testid="taskrun-approve"
          :disabled="busy"
          class="rounded-md bg-slate-800 px-3 py-2 text-xs text-white disabled:opacity-50"
          @click="approve"
        >管理员审批（不能自批）</button>
        <button
          v-if="task.status === 'APPROVED' && task.engine === 'deterministic'"
          type="button"
          data-testid="taskrun-execute"
          :disabled="busy"
          class="rounded-md bg-slate-900 px-3 py-2 text-xs text-white disabled:opacity-50"
          @click="execute"
        >运行已批准的合成任务</button>
      </div>
      <p v-if="task.status === 'APPROVED' && (task.engine === 'deepagents' || task.engine === 'native_harness')" data-testid="taskrun-model-worker" class="text-xs text-amber-800">
        模型引擎必须由独立 Worker 经外部 API 授权、密钥和模型预算检查后运行；浏览器不会自动调用模型。
      </p>
      <p v-if="task.status === 'PENDING_APPROVAL' && admin.user?.username === task.owner" class="text-xs text-amber-800">
        发起人不可审批自己的任务，请由另一位管理员核验。
      </p>

      <div v-if="task.result" data-testid="taskrun-receipt" class="space-y-2 text-xs">
        <p class="font-semibold">执行回执：{{ task.result.status }} · {{ task.result.planner }}</p>
        <p>数据库工具：{{ task.result.execution.tool_calls }} · 步骤：{{ task.result.execution.model_steps }} ·
          实际模型 Token：{{ task.result.execution.prompt_tokens ?? '未测量' }}</p>
        <p v-if="task.result.missing_evidence.length" class="text-amber-800">
          拒绝完整比较：{{ task.result.missing_evidence.join('；') }}
        </p>
        <ul v-if="task.result.schools.length" class="space-y-1">
          <li v-for="school in task.result.schools" :key="school.school_name">
            {{ school.school_name }} · 历史分差 {{ school.historical_score_difference }}（非录取概率） ·
            来源 {{ school.evidence_ids.join(', ') }}
          </li>
        </ul>
        <p class="font-medium text-amber-800">{{ task.result.disclaimer }}</p>
      </div>
      <p v-if="task.failure_code" class="text-xs text-red-700">失败代码：{{ task.failure_code }}</p>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { AdminApiError, adminApi } from '../../api/admin'
import { useAdminStore } from '../../stores/admin'
import type { SyntheticDecisionCardExample, SyntheticTaskRun } from '../../types/admin'

const admin = useAdminStore()
const example = ref<SyntheticDecisionCardExample | null>(null)
const task = ref<SyntheticTaskRun | null>(null)
const runId = ref('')
const engine = ref<'deterministic' | 'deepagents' | 'native_harness'>('deterministic')
const busy = ref(false)
const disabled = ref(false)
const error = ref('')

async function initialize(): Promise<void> {
  if (!admin.token) return
  try {
    example.value = await adminApi.decisionCardExample(admin.token)
  } catch (err) {
    if (err instanceof AdminApiError && err.status === 404) {
      disabled.value = true
      return
    }
    error.value = err instanceof Error ? err.message : '无法读取合成任务示例'
  }
}

async function operate(action: () => Promise<SyntheticTaskRun>): Promise<void> {
  if (busy.value || !admin.token) return
  busy.value = true
  error.value = ''
  try {
    const next = await action()
    task.value = next
    runId.value = next.run_id
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'TaskRun 操作失败'
  } finally {
    busy.value = false
  }
}

function createRun(): void {
  if (!example.value || !admin.token) return
  const token = admin.token
  const key = globalThis.crypto?.randomUUID?.() ?? 'synthetic-' + Date.now().toString(16)
  const request = { engine: engine.value, idempotency_key: key, profile: example.value.request }
  void operate(() => adminApi.createSyntheticTaskRun(request, token))
}

function refresh(): void {
  if (!admin.token || !runId.value) return
  const token = admin.token
  const id = runId.value
  void operate(() => adminApi.getSyntheticTaskRun(id, token))
}

function approve(): void {
  if (!admin.token || !task.value) return
  const token = admin.token
  const id = task.value.run_id
  void operate(() => adminApi.approveSyntheticTaskRun(id, token))
}

function execute(): void {
  if (!admin.token || !task.value || task.value.engine !== 'deterministic') return
  const token = admin.token
  const id = task.value.run_id
  void operate(() => adminApi.executeSyntheticTaskRun(id, token))
}

onMounted(() => void initialize())
</script>
