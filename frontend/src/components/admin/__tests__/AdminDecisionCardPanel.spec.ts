import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { AdminApiError, adminApi } from '../../../api/admin'
import { useAdminStore } from '../../../stores/admin'
import type { SyntheticDecisionCardExample, SyntheticDecisionCardResult } from '../../../types/admin'
import AdminDecisionCardPanel from '../AdminDecisionCardPanel.vue'

const profile = {
  school_a: '北辰示例科技大学（合成）',
  school_b: '沧澜示例农业大学（合成）',
  major: '计算机科学与技术（合成）',
  province: '示例省·甲',
  subject_type: '物理类' as const,
  score: 550,
  year: 2025,
}

const example: SyntheticDecisionCardExample = {
  data_profile: 'SYNTHETIC_DEMO',
  planner: 'DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM',
  request: profile,
}

const result: SyntheticDecisionCardResult = {
  status: 'evidence_complete_historical_only',
  reason: 'evidence_bound_finish',
  data_profile: 'SYNTHETIC_DEMO',
  planner: 'DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM',
  profile: {
    major: profile.major, province: profile.province, score: profile.score,
    subject_type: profile.subject_type, year: profile.year,
  },
  major_category: '工学',
  schools: [
    {
      school_name: profile.school_a,
      historical_min_score: 520, historical_min_rank: 3000, historical_subject_type: '物理类',
      historical_score_difference: 30, plan_count: 50, plan_subject_requirement: '物理必选',
      subject_constraint: 'compatible',
      evidence_ids: ['synthetic:admission:123456789abc', 'synthetic:plan:abcdef012345'],
    },
    {
      school_name: profile.school_b,
      historical_min_score: 556, historical_min_rank: 4000, historical_subject_type: '物理类',
      historical_score_difference: -6, plan_count: 70, plan_subject_requirement: '物理必选',
      subject_constraint: 'compatible',
      evidence_ids: ['synthetic:admission:abcdef123456', 'synthetic:plan:123456abcdef'],
    },
  ],
  missing_evidence: [],
  evidence_ids: [
    'synthetic:major:0123456789ab', 'synthetic:admission:123456789abc',
    'synthetic:admission:abcdef123456', 'synthetic:plan:abcdef012345',
    'synthetic:plan:123456abcdef',
  ],
  manual_checks: ['历史最低分差不是录取概率，不提供冲稳保推荐'],
  disclaimer: '虚构演示数据；不得用于真实志愿填报',
  execution: {
    tool_calls: 5, model_steps: 6, prompt_tokens: null, completion_tokens: null,
    tool_sequence: ['major_lookup', 'admission_lookup', 'admission_lookup', 'plan_lookup', 'plan_lookup'],
    trace: [{ step: 1, kind: 'tool', tool: 'major_lookup', result: 'ok' }],
  },
}

beforeEach(() => {
  vi.restoreAllMocks()
  setActivePinia(createPinia())
  useAdminStore().token = 'fictional-operator-token'
})

describe('AdminDecisionCardPanel', () => {
  it('renders verified source IDs and never presents score differences as probability', async () => {
    vi.spyOn(adminApi, 'decisionCardExample').mockResolvedValue(example)
    const post = vi.spyOn(adminApi, 'decisionCard').mockResolvedValue(result)
    const wrapper = mount(AdminDecisionCardPanel)
    await flushPromises()
    await wrapper.find('[data-testid="decision-card-run"]').trigger('click')
    await flushPromises()
    expect(post).toHaveBeenCalledWith(profile, 'fictional-operator-token')
    expect(wrapper.findAll('article')).toHaveLength(2)
    expect(wrapper.text()).toContain('历史最低分差不是录取概率')
    expect(wrapper.text()).toContain('synthetic:admission:')
    expect(wrapper.text()).toContain('真实志愿填报')
    expect(wrapper.text()).toContain('执行工具：5')
    expect(wrapper.find('[data-testid="decision-card-abstain"]').exists()).toBe(false)
  })

  it('shows missing evidence and withholds a school comparison when the runtime abstains', async () => {
    vi.spyOn(adminApi, 'decisionCardExample').mockResolvedValue(example)
    vi.spyOn(adminApi, 'decisionCard').mockResolvedValue({
      ...result, status: 'needs_review', schools: [], evidence_ids: [],
      missing_evidence: ['学校 B 缺少合成招生计划'],
    })
    const wrapper = mount(AdminDecisionCardPanel)
    await flushPromises()
    await wrapper.find('[data-testid="decision-card-run"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="decision-card-abstain"]').exists()).toBe(true)
    expect(wrapper.findAll('article')).toHaveLength(0)
    expect(wrapper.text()).toContain('缺少合成招生计划')
  })

  it('remains closed when the feature flag is disabled', async () => {
    vi.spyOn(adminApi, 'decisionCardExample').mockRejectedValue(
      new AdminApiError('disabled', { code: 'HTTP_404', status: 404 }),
    )
    const wrapper = mount(AdminDecisionCardPanel)
    await flushPromises()
    expect(wrapper.find('[data-testid="decision-card-disabled"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="decision-card-run"]').exists()).toBe(false)
  })
})
