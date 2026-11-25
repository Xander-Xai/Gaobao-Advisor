import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { chatResearchAPI } from '../../../api/client'
import { useChatStore } from '../../../stores/chat'
import ChatResearchRequest from '../ChatResearchRequest.vue'

const task = {
  run_id: 'research_' + 'a'.repeat(32),
  status: 'PENDING_APPROVAL',
}

beforeEach(() => {
  vi.restoreAllMocks()
  setActivePinia(createPinia())
})

describe('ChatResearchRequest', () => {
  it('submits a session-bound structured request without starting execution', async () => {
    const chat = useChatStore()
    chat.currentSessionId = 'session-user-1234'
    await flushPromises()
    chat.sessionToken = 'session-capability'
    const options = vi.spyOn(chatResearchAPI, 'options').mockResolvedValue({
      data: {
        schools: [{ id: 101, name: 'Authorized School A' }, { id: 102, name: 'Authorized School B' }],
        majors: [{ id: 42, name: 'Authorized Major' }],
      },
      error: null,
    })
    const create = vi.spyOn(chatResearchAPI, 'create').mockResolvedValue({ data: task, error: null })
    vi.spyOn(chatResearchAPI, 'follow').mockResolvedValue(undefined)
    const wrapper = mount(ChatResearchRequest)
    await wrapper.find('[data-testid="research-options-search"]').trigger('click')
    await flushPromises()
    expect(options).toHaveBeenCalledOnce()
    const fields: Record<string, string> = {
      'school-a': '101', 'school-b': '102', major: '42', province: '广东',
      subject: '物理', batch: '本科', 'year-from': '2022', 'year-to': '2024',
    }
    for (const [name, value] of Object.entries(fields)) {
      await wrapper.find(`[data-testid="research-${name}"]`).setValue(value)
    }
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(create).toHaveBeenCalledWith(expect.objectContaining({
      sessionId: 'session-user-1234',
      token: 'session-capability',
      scope: expect.objectContaining({ school_ids: [101, 102], major_id: 42 }),
    }))
    expect(chatResearchAPI.follow).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('待运营人员审批')
    expect(wrapper.text()).toContain(task.run_id)
  })

  it('requires a chat session bearer before a research request can be submitted', () => {
    const wrapper = mount(ChatResearchRequest)
    expect(wrapper.text()).toContain('请先在本会话发送一条消息')
    expect(wrapper.find('button[type="submit"]').attributes('disabled')).toBeDefined()
  })

  it('makes a failed retry a new request that returns to operator approval', async () => {
    const chat = useChatStore()
    chat.currentSessionId = 'session-user-1234'
    await flushPromises()
    chat.sessionToken = 'session-capability'
    vi.spyOn(chatResearchAPI, 'options').mockResolvedValue({
      data: {
        schools: [{ id: 101, name: 'Authorized School A' }, { id: 102, name: 'Authorized School B' }],
        majors: [{ id: 42, name: 'Authorized Major' }],
      },
      error: null,
    })
    const create = vi.spyOn(chatResearchAPI, 'create')
      .mockResolvedValueOnce({ data: task, error: null })
      .mockResolvedValueOnce({ data: { ...task, run_id: 'research_' + 'b'.repeat(32) }, error: null })
    vi.spyOn(chatResearchAPI, 'follow').mockImplementation(async ({ runId, onEvent }) => {
      onEvent({ type: 'progress', run_id: runId, status: 'FAILED', stage: 'execution_failed', failure_code: 'failed' })
    })
    const wrapper = mount(ChatResearchRequest)
    await wrapper.find('[data-testid="research-options-search"]').trigger('click')
    await flushPromises()
    const fields: Record<string, string> = {
      'school-a': '101', 'school-b': '102', major: '42', province: '广东',
      subject: '物理', batch: '本科', 'year-from': '2022', 'year-to': '2024',
    }
    for (const [name, value] of Object.entries(fields)) {
      await wrapper.find(`[data-testid="research-${name}"]`).setValue(value)
    }
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    await wrapper.find('[data-testid="research-resubmit"]').trigger('click')
    await flushPromises()

    expect(create).toHaveBeenCalledTimes(2)
    expect(create.mock.calls[1][0].idempotencyKey).not.toBe(create.mock.calls[0][0].idempotencyKey)
    expect(create.mock.calls[1][0].scope).toEqual(create.mock.calls[0][0].scope)
    expect(wrapper.text()).toContain('重新提交会创建新的 TaskRun')
  })

  it('does not render a prior session report after the active chat changes', async () => {
    const chat = useChatStore()
    chat.currentSessionId = 'session-user-1234'
    await flushPromises()
    chat.sessionToken = 'session-capability'
    vi.spyOn(chatResearchAPI, 'options').mockResolvedValue({
      data: {
        schools: [{ id: 101, name: 'Authorized School A' }, { id: 102, name: 'Authorized School B' }],
        majors: [{ id: 42, name: 'Authorized Major' }],
      },
      error: null,
    })
    vi.spyOn(chatResearchAPI, 'create').mockResolvedValue({ data: { ...task, status: 'SUCCEEDED' }, error: null })
    vi.spyOn(chatResearchAPI, 'follow').mockResolvedValue(undefined)
    let resolveReport: (value: { data: string; error: null }) => void = () => undefined
    vi.spyOn(chatResearchAPI, 'report').mockReturnValue(new Promise(resolve => { resolveReport = resolve }))
    const wrapper = mount(ChatResearchRequest)
    await wrapper.find('[data-testid="research-options-search"]').trigger('click')
    await flushPromises()
    const fields: Record<string, string> = {
      'school-a': '101', 'school-b': '102', major: '42', province: '广东',
      subject: '物理', batch: '本科', 'year-from': '2022', 'year-to': '2024',
    }
    for (const [name, value] of Object.entries(fields)) {
      await wrapper.find(`[data-testid="research-${name}"]`).setValue(value)
    }
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    await wrapper.find('[data-testid="research-report"]').trigger('click')
    await flushPromises()

    chat.currentSessionId = 'session-other-1234'
    await flushPromises()
    resolveReport({ data: 'PRIVATE REPORT FROM OLD SESSION', error: null })
    await flushPromises()

    expect(wrapper.text()).not.toContain('PRIVATE REPORT FROM OLD SESSION')
  })
})
