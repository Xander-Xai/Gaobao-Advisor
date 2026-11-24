import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { adminApi } from '../../../api/admin'
import { useAdminStore } from '../../../stores/admin'
import type { AuthorizedResearchRun } from '../../../types/admin'
import AdminAuthorizedResearchPanel from '../AdminAuthorizedResearchPanel.vue'

const task: AuthorizedResearchRun = {
  run_id: 'research_' + 'a'.repeat(32), owner: 'operator', status: 'PENDING_APPROVAL',
  spec: { scope: { school_ids: [101, 102], major_id: 42, province: '广东', subject_type: '物理',
    batch: '本科', year_from: 2022, year_to: 2024 }, dataset_ref: 'approved-fixture',
    model_id: 'openai:approved-model', image_id: 'sha256:' + 'b'.repeat(64), approval_ref: null },
  spec_sha256: 'c'.repeat(64), approved_by: null, result: null, failure_code: null,
  automatic_paid_replay: false, created_at: 123, updated_at: 123,
}

beforeEach(() => {
  vi.restoreAllMocks()
  setActivePinia(createPinia())
  const admin = useAdminStore()
  admin.token = 'test-token'
  admin.user = { username: 'operator', role: 'operator' }
})

describe('authorized research workbench', () => {
  it('creates an immutable business task with no automatic execution', async () => {
    const create = vi.spyOn(adminApi, 'createAuthorizedResearch').mockResolvedValue(task)
    const wrapper = mount(AdminAuthorizedResearchPanel)
    const fields: Record<string, string> = { 'school-a': '101', 'school-b': '102', major: '42',
      province: '广东', subject: '物理', batch: '本科', from: '2022', to: '2024',
      model: task.spec.model_id, image: task.spec.image_id, ref: 'approval-42' }
    for (const [name, value] of Object.entries(fields)) {
      await wrapper.find('[data-testid="research-' + name + '"]').setValue(value)
    }
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(create).toHaveBeenCalledOnce()
    expect(create.mock.calls[0][0].scope.school_ids).toEqual([101, 102])
    expect(wrapper.text()).toContain('PENDING_APPROVAL')
    expect(wrapper.find('[data-testid="research-approve"]').exists()).toBe(false)
  })

  it('requires independent reviewer acknowledgement and displays verified report only', async () => {
    const admin = useAdminStore()
    admin.user = { username: 'reviewer', role: 'admin' }
    const get = vi.spyOn(adminApi, 'getAuthorizedResearch').mockResolvedValue(task)
    const approve = vi.spyOn(adminApi, 'approveAuthorizedResearch').mockResolvedValue({
      ...task, status: 'APPROVED', approved_by: 'reviewer',
    })
    const report = vi.spyOn(adminApi, 'authorizedResearchReport').mockResolvedValue('Verified aggregate only')
    const wrapper = mount(AdminAuthorizedResearchPanel)
    await wrapper.find('[data-testid="research-run-id"]').setValue(task.run_id)
    await wrapper.find('[data-testid="research-refresh"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="research-approve"]').attributes('disabled')).toBeDefined()
    await wrapper.find('[data-testid="research-reviewed"]').setValue(true)
    await wrapper.find('[data-testid="research-approve"]').trigger('click')
    await flushPromises()
    expect(approve).toHaveBeenCalledWith(task.run_id, task.spec_sha256, 'test-token')
    expect(report).not.toHaveBeenCalled()
    get.mockResolvedValue({ ...task, status: 'NEEDS_REVIEW', result: {
      status: 'VERIFIED_PARTIAL_RESEARCH', evidence_sha256: 'd'.repeat(64),
      analysis_sha256: 'e'.repeat(64), coverage: {}, analysis: {},
      admission_recommendation_verified: false, spec_sha256: task.spec_sha256,
    } })
    await wrapper.find('[data-testid="research-refresh"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="research-report"]').trigger('click')
    await flushPromises()
    expect(report).toHaveBeenCalledWith(task.run_id, 'test-token')
    expect(wrapper.find('[data-testid="research-report-content"]').text()).toContain('Verified aggregate only')
  })
})
