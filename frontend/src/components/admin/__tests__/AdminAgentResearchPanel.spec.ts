import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { AdminApiError, adminApi } from '../../../api/admin'
import { useAdminStore } from '../../../stores/admin'
import type { SyntheticAgentExample, SyntheticAgentResearchResult } from '../../../types/admin'
import AdminAgentResearchPanel from '../AdminAgentResearchPanel.vue'

const sample: SyntheticAgentExample = {
  data_profile: 'SYNTHETIC_DEMO',
  planner: 'DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM',
  request: {
    school_a: '北辰示例科技大学（合成）',
    school_b: '海岳示例师范大学（合成）',
    major: '计算机科学与技术（合成）',
  },
}

const report: SyntheticAgentResearchResult = {
  status: 'completed',
  reason: 'evidence_bound_finish',
  facts: [
    { tool: 'school_lookup', school_name: sample.request.school_a, province: '示例省·甲', source_id: 'synthetic:school:0123456789ab' },
    { tool: 'school_lookup', school_name: sample.request.school_b, province: '示例省·甲', source_id: 'synthetic:school:abcdef012345' },
    { tool: 'major_lookup', major_name: sample.request.major, category: '工学', source_id: 'synthetic:major:012345abcdef' },
  ],
  disclaimer: '仅合成演示数据，不得用于真实高考填报',
  planner: 'DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM',
  execution: {
    tool_calls: 3,
    model_steps: 4,
    prompt_tokens: null,
    completion_tokens: null,
    tool_sequence: ['school_lookup', 'school_lookup', 'major_lookup'],
    trace: [
      { step: 1, kind: 'tool', tool: 'school_lookup', result: 'ok' },
      { step: 2, kind: 'tool', tool: 'school_lookup', result: 'ok' },
      { step: 3, kind: 'tool', tool: 'major_lookup', result: 'ok' },
      { step: 4, kind: 'finish', result: 'completed' },
    ],
  },
}

beforeEach(() => {
  vi.restoreAllMocks()
  setActivePinia(createPinia())
  const store = useAdminStore()
  store.token = 'synthetic-admin-token'
})

describe('AdminAgentResearchPanel', () => {
  it('shows a real API-driven three-tool research trace without claiming LLM autonomy', async () => {
    vi.spyOn(adminApi, 'agentResearchExample').mockResolvedValue(sample)
    const run = vi.spyOn(adminApi, 'agentResearch').mockResolvedValue(report)
    const wrapper = mount(AdminAgentResearchPanel)
    await flushPromises()
    expect(wrapper.text()).toContain(sample.request.school_a)
    expect(wrapper.text()).toContain('确定性 Planner')
    await wrapper.find('[data-testid="agent-run"]').trigger('click')
    await flushPromises()
    expect(run).toHaveBeenCalledWith(sample.request, 'synthetic-admin-token')
    expect(wrapper.findAll('[data-testid="agent-fact"]')).toHaveLength(3)
    expect(wrapper.findAll('[data-testid="agent-trace-step"]')).toHaveLength(4)
    expect(wrapper.text()).toContain('真实工具调用：3')
    expect(wrapper.text()).toContain('真实模型 Token：未测量')
    expect(wrapper.text()).toContain('不得用于真实高考')
  })

  it('shows a closed-state explanation instead of pretending the experiment is available', async () => {
    vi.spyOn(adminApi, 'agentResearchExample').mockRejectedValue(
      new AdminApiError('Experiment not enabled', { code: 'HTTP_404', status: 404 }),
    )
    const wrapper = mount(AdminAgentResearchPanel)
    await flushPromises()
    expect(wrapper.find('[data-testid="agent-disabled"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="agent-run"]').exists()).toBe(false)
  })
})
