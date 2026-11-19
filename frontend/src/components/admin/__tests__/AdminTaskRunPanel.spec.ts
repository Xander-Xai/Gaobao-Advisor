import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { AdminApiError, adminApi } from '../../../api/admin'
import { useAdminStore } from '../../../stores/admin'
import type { SyntheticDecisionCardExample, SyntheticTaskRun } from '../../../types/admin'
import AdminTaskRunPanel from '../AdminTaskRunPanel.vue'

const sample: SyntheticDecisionCardExample = {
  data_profile: 'SYNTHETIC_DEMO',
  planner: 'DETERMINISTIC_OBSERVATION_POLICY_NOT_LLM',
  request: {
    school_a: '北辰示例科技大学（合成）',
    school_b: '沧澜示例农业大学（合成）',
    major: '计算机科学与技术（合成）',
    province: '示例省·甲',
    score: 550,
    subject_type: '物理类',
    year: 2025,
  },
}

const created: SyntheticTaskRun = {
  run_id: 'syn_' + 'a'.repeat(32),
  owner: 'operator',
  engine: 'deterministic',
  status: 'PENDING_APPROVAL',
  approved_by: null,
  version: 0,
  attempts: 0,
  failure_code: null,
  result: null,
  created_at: 123,
  updated_at: 123,
  data_profile: 'SYNTHETIC_DEMO',
}

beforeEach(() => {
  vi.restoreAllMocks()
  setActivePinia(createPinia())
  const admin = useAdminStore()
  admin.token = 'mock-synthetic-token'
  admin.user = { username: 'operator', role: 'operator' }
})

describe('AdminTaskRunPanel', () => {
  it('creates an approval-required job and never auto-executes it', async () => {
    vi.spyOn(adminApi, 'decisionCardExample').mockResolvedValue(sample)
    const create = vi.spyOn(adminApi, 'createSyntheticTaskRun').mockResolvedValue(created)
    const run = vi.spyOn(adminApi, 'executeSyntheticTaskRun')
    const wrapper = mount(AdminTaskRunPanel)
    await flushPromises()
    await wrapper.find('[data-testid="taskrun-create"]').trigger('click')
    await flushPromises()
    expect(create).toHaveBeenCalledOnce()
    expect(create.mock.calls[0][0].engine).toBe('deterministic')
    expect(create.mock.calls[0][0].profile).toEqual(sample.request)
    expect(wrapper.text()).toContain('PENDING_APPROVAL')
    expect(wrapper.find('[data-testid="taskrun-approve"]').exists()).toBe(false)
    expect(run).not.toHaveBeenCalled()
  })

  it('requires a second admin principal and keeps Deep Agents execution out of browser', async () => {
    const admin = useAdminStore()
    admin.user = { username: 'reviewer', role: 'admin' }
    vi.spyOn(adminApi, 'decisionCardExample').mockResolvedValue(sample)
    vi.spyOn(adminApi, 'getSyntheticTaskRun').mockResolvedValue({
      ...created, engine: 'deepagents', status: 'PENDING_APPROVAL',
    })
    const approve = vi.spyOn(adminApi, 'approveSyntheticTaskRun').mockResolvedValue({
      ...created, engine: 'deepagents', status: 'APPROVED', approved_by: 'reviewer',
    })
    const run = vi.spyOn(adminApi, 'executeSyntheticTaskRun')
    const wrapper = mount(AdminTaskRunPanel)
    await flushPromises()
    await wrapper.find('[data-testid="taskrun-id"]').setValue(created.run_id)
    await wrapper.find('[data-testid="taskrun-refresh"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="taskrun-approve"]').exists()).toBe(true)
    await wrapper.find('[data-testid="taskrun-approve"]').trigger('click')
    await flushPromises()
    expect(approve).toHaveBeenCalledWith(created.run_id, 'mock-synthetic-token')
    expect(wrapper.find('[data-testid="taskrun-model-worker"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="taskrun-execute"]').exists()).toBe(false)
    expect(run).not.toHaveBeenCalled()
  })

  it('does not expose create when experiment is disabled', async () => {
    vi.spyOn(adminApi, 'decisionCardExample').mockRejectedValue(
      new AdminApiError('disabled', { code: 'HTTP_404', status: 404 }),
    )
    const wrapper = mount(AdminTaskRunPanel)
    await flushPromises()
    expect(wrapper.find('[data-testid="taskrun-disabled"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="taskrun-create"]').exists()).toBe(false)
  })
})
