<template>
  <section class="space-y-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm" data-testid="decision-card-panel">
    <header>
      <h2 class="text-base font-semibold">证据驱动决策卡 · 合成考生画像</h2>
      <p class="mt-1 text-xs text-slate-600">
        查看指定虚构专业在两所虚构院校的历年录取记录、招生计划与选科约束。
        不预测录取概率、不推荐真实院校，执行策略非大模型。
      </p>
    </header>
    <p v-if="disabled" role="status" data-testid="decision-card-disabled" class="text-sm text-slate-600">
      实验默认关闭：仅在开发环境由 operator/admin 启用。
    </p>
    <p v-else-if="error" role="alert" data-testid="decision-card-error" class="text-sm text-red-700">{{ error }}</p>

    <template v-if="sample">
      <div class="grid gap-2 text-sm sm:grid-cols-2">
        <div class="rounded-lg bg-slate-50 p-3">
          <div class="text-xs text-slate-500">合成画像（非真实考生）</div>
          <p>{{ sample.request.province }} · {{ sample.request.score }} 分 · {{ sample.request.subject_type }}</p>
          <p>{{ sample.request.major }} · 合成年份 {{ sample.request.year }}</p>
        </div>
        <div class="rounded-lg bg-slate-50 p-3">
          <div class="text-xs text-slate-500">指定的虚构院校</div>
          <p>{{ sample.request.school_a }}</p>
          <p>{{ sample.request.school_b }}</p>
        </div>
      </div>
      <button
        type="button"
        class="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        data-testid="decision-card-run"
        :disabled="busy"
        @click="execute"
      >{{ busy ? '查询中…' : '核验历史数据与招生约束' }}</button>
    </template>

    <div v-if="result" class="space-y-3" data-testid="decision-card-result" aria-live="polite">
      <div class="flex flex-wrap gap-2 text-xs text-slate-700">
        <span class="rounded-md bg-slate-100 px-2 py-1">结论状态：{{ result.status }}</span>
        <span class="rounded-md bg-slate-100 px-2 py-1">执行工具：{{ result.execution.tool_calls }}</span>
        <span class="rounded-md bg-slate-100 px-2 py-1">工具步骤：{{ result.execution.tool_sequence.join(' → ') }}</span>
      </div>

      <p v-if="result.status === 'needs_review'" class="text-sm font-medium text-amber-800" data-testid="decision-card-abstain">
        证据不足或约束不一致：停止生成完整历史对比，请复核。
      </p>
      <ul v-if="result.missing_evidence.length" class="list-disc space-y-1 pl-5 text-sm text-amber-800">
        <li v-for="item in result.missing_evidence" :key="item">{{ item }}</li>
      </ul>

      <div v-if="result.schools.length" class="grid gap-3 md:grid-cols-2">
        <article v-for="school in result.schools" :key="school.school_name" class="rounded-lg border border-slate-200 p-3">
          <h3 class="mb-2 text-sm font-semibold">{{ school.school_name }}</h3>
          <dl class="grid grid-cols-2 gap-2 text-xs">
            <dt class="text-slate-500">合成历史最低分</dt><dd>{{ school.historical_min_score ?? '缺失' }}</dd>
            <dt class="text-slate-500">与画像分差</dt><dd>{{ school.historical_score_difference ?? '缺失' }}（非概率）</dd>
            <dt class="text-slate-500">历史最低位次</dt><dd>{{ school.historical_min_rank ?? '缺失' }}</dd>
            <dt class="text-slate-500">招生计划数</dt><dd>{{ school.plan_count ?? '缺失' }}</dd>
            <dt class="text-slate-500">选科约束</dt><dd>{{ school.plan_subject_requirement ?? '未核验' }}</dd>
            <dt class="text-slate-500">约束检查</dt><dd>{{ school.subject_constraint }}</dd>
          </dl>
          <div class="mt-3 space-y-1 text-[11px] text-slate-500">
            <code v-for="id in school.evidence_ids" :key="id" class="block break-all">{{ id }}</code>
          </div>
        </article>
      </div>

      <div class="rounded-lg bg-slate-50 p-3">
        <h3 class="text-sm font-medium">缺失的现实决策依据</h3>
        <ul class="mt-1 list-disc space-y-1 pl-5 text-xs text-slate-600">
          <li v-for="check in result.manual_checks" :key="check">{{ check }}</li>
        </ul>
      </div>
      <p class="text-xs font-medium text-amber-800">{{ result.disclaimer }}</p>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { AdminApiError, adminApi } from '../../api/admin'
import { useAdminStore } from '../../stores/admin'
import type { SyntheticDecisionCardExample, SyntheticDecisionCardResult } from '../../types/admin'

const admin = useAdminStore()
const sample = ref<SyntheticDecisionCardExample | null>(null)
const result = ref<SyntheticDecisionCardResult | null>(null)
const busy = ref(false)
const disabled = ref(false)
const error = ref('')

async function loadExample(): Promise<void> {
  if (!admin.token) return
  try {
    sample.value = await adminApi.decisionCardExample(admin.token)
  } catch (err) {
    if (err instanceof AdminApiError && err.status === 404) {
      disabled.value = true
      return
    }
    error.value = err instanceof Error ? err.message : '无法读取虚构决策卡示例'
  }
}

async function execute(): Promise<void> {
  if (!admin.token || !sample.value || busy.value) return
  busy.value = true
  result.value = null
  error.value = ''
  try {
    result.value = await adminApi.decisionCard(sample.value.request, admin.token)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '合成决策卡查询失败'
  } finally {
    busy.value = false
  }
}

onMounted(() => void loadExample())
</script>
