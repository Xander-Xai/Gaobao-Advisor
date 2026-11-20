<template>
  <section
    class="space-y-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
    aria-label="Agent 研究实验"
    data-testid="agent-research-panel"
  >
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 class="text-base font-semibold">Agent 多步研究 · 合成数据</h2>
        <p class="mt-1 text-xs text-slate-500">
          真实数据库工具执行、来源绑定、步数与异常轨迹；当前为确定性 Planner，非大模型自主评测。
        </p>
      </div>
      <span class="rounded-md bg-slate-100 px-2 py-1 text-xs font-medium text-slate-600">SYNTHETIC_DEMO</span>
    </div>

    <p v-if="unavailable" role="status" class="text-sm text-slate-600" data-testid="agent-disabled">
      此实验默认关闭。开发环境可配置 ENABLE_SYNTHETIC_AGENT_EXPERIMENT=true，由 operator/admin 登录后使用。
    </p>
    <p v-else-if="error" role="alert" class="text-sm text-red-700" data-testid="agent-error">
      {{ error }}
    </p>

    <template v-if="example">
      <div class="grid gap-2 text-sm sm:grid-cols-3">
        <div class="rounded-lg bg-slate-50 p-3">
          <span class="block text-xs text-slate-500">虚构院校 A</span>
          <span class="font-medium">{{ example.request.school_a }}</span>
        </div>
        <div class="rounded-lg bg-slate-50 p-3">
          <span class="block text-xs text-slate-500">虚构院校 B</span>
          <span class="font-medium">{{ example.request.school_b }}</span>
        </div>
        <div class="rounded-lg bg-slate-50 p-3">
          <span class="block text-xs text-slate-500">虚构专业</span>
          <span class="font-medium">{{ example.request.major }}</span>
        </div>
      </div>

      <div class="flex flex-wrap items-center gap-3">
        <button
          type="button"
          class="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-50"
          :disabled="running"
          data-testid="agent-run"
          @click="runResearch"
        >{{ running ? '执行中…' : '运行三步研究任务' }}</button>
        <span class="text-xs text-slate-500">仅查询事先声明的虚构实体，不调用付费模型</span>
      </div>
    </template>

    <div v-if="report" class="space-y-3" data-testid="agent-result" aria-live="polite">
      <div class="flex flex-wrap gap-2 text-xs">
        <span class="rounded-md bg-slate-100 px-2 py-1">状态：{{ report.status }}（{{ report.reason }}）</span>
        <span class="rounded-md bg-slate-100 px-2 py-1">真实工具调用：{{ report.execution.tool_calls }}</span>
        <span class="rounded-md bg-slate-100 px-2 py-1">Planner 步数：{{ report.execution.model_steps }}</span>
        <span class="rounded-md bg-slate-100 px-2 py-1">真实模型 Token：未测量</span>
      </div>

      <div class="grid gap-2 sm:grid-cols-3">
        <div
          v-for="fact in report.facts"
          :key="fact.source_id"
          class="rounded-lg border border-slate-200 p-3 text-sm"
          data-testid="agent-fact"
        >
          <div class="font-medium">{{ fact.school_name ?? fact.major_name }}</div>
          <div class="mt-1 text-slate-700">{{ fact.province ?? fact.category }}</div>
          <code class="mt-2 block break-all text-[11px] text-slate-500">{{ fact.source_id }}</code>
        </div>
      </div>

      <div class="rounded-lg border border-slate-200 p-3">
        <h3 class="mb-2 text-sm font-medium">可复核执行轨迹</h3>
        <ol class="space-y-1 text-xs text-slate-700">
          <li
            v-for="event in report.execution.trace"
            :key="event.step"
            data-testid="agent-trace-step"
          >
            {{ event.step }} · {{ event.tool ?? event.kind }} · {{ event.result ?? 'ok' }}
            <span v-if="event.elapsed_ms !== undefined"> · {{ event.elapsed_ms }} ms</span>
          </li>
        </ol>
      </div>
      <p class="text-xs font-medium text-amber-800">{{ report.disclaimer }}</p>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { AdminApiError, adminApi } from '../../api/admin'
import { useAdminStore } from '../../stores/admin'
import type { SyntheticAgentExample, SyntheticAgentResearchResult } from '../../types/admin'

const admin = useAdminStore()
const example = ref<SyntheticAgentExample | null>(null)
const report = ref<SyntheticAgentResearchResult | null>(null)
const running = ref(false)
const unavailable = ref(false)
const error = ref('')

async function loadExample(): Promise<void> {
  if (!admin.token) return
  try {
    example.value = await adminApi.agentResearchExample(admin.token)
  } catch (err) {
    if (err instanceof AdminApiError && err.status === 404) {
      unavailable.value = true
      return
    }
    error.value = err instanceof Error ? err.message : '无法获取合成研究示例'
  }
}

async function runResearch(): Promise<void> {
  if (!admin.token || !example.value || running.value) return
  running.value = true
  error.value = ''
  report.value = null
  try {
    report.value = await adminApi.agentResearch(example.value.request, admin.token)
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Agent 研究暂不可用'
  } finally {
    running.value = false
  }
}

onMounted(() => void loadExample())
</script>
