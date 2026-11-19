<template>
  <div class="min-h-screen bg-slate-100 text-slate-900">
    <header class="border-b border-slate-200 bg-white">
      <div class="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
        <div class="flex items-center gap-3">
          <span class="grid h-9 w-9 place-items-center rounded-xl bg-red-700 text-sm font-bold text-white">GB</span>
          <div>
            <h1 class="text-sm font-semibold tracking-wide">gaobao-advisor · 运营工作台</h1>
            <p class="text-[11px] text-slate-400">数据运营 / 质检任务 / 审计 · SYNTHETIC_DEMO</p>
          </div>
        </div>
        <div class="flex items-center gap-2 text-xs">
          <a href="/" class="rounded-md border border-slate-300 px-3 py-1.5 text-slate-600 hover:bg-slate-50">返回对话</a>
          <template v-if="admin.isAuthenticated">
            <span class="rounded-full bg-slate-100 px-3 py-1.5 text-slate-700" data-testid="admin-user">
              {{ admin.user?.username }} · {{ roleLabel }}
            </span>
            <button class="rounded-md border border-slate-300 px-3 py-1.5 text-slate-600 hover:bg-slate-50" data-testid="admin-logout" @click="admin.logout">
              退出
            </button>
          </template>
        </div>
      </div>
    </header>

    <main v-if="!admin.isAuthenticated" class="px-4 pb-10 sm:px-6">
      <AdminLoginPanel />
    </main>

    <main v-else class="mx-auto max-w-7xl space-y-4 px-4 py-5 sm:px-6">
      <div
        v-if="admin.actionError && activeTab !== 'quality'"
        role="alert"
        data-testid="admin-error"
        class="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700"
      >{{ admin.actionError }}</div>

      <AdminStatsCards :stats="admin.stats" :system-status="admin.systemStatus" />

      <nav class="flex flex-wrap gap-1 rounded-xl border border-slate-200 bg-white p-1 shadow-sm" role="tablist" aria-label="后台模块">
        <button
          v-for="tab in visibleTabs"
          :key="tab.id"
          role="tab"
          :aria-selected="activeTab === tab.id"
          :data-testid="`tab-${tab.id}`"
          class="rounded-lg px-4 py-2 text-sm font-medium transition-colors"
          :class="activeTab === tab.id ? 'bg-slate-900 text-white' : 'text-slate-600 hover:bg-slate-100'"
          @click="selectTab(tab.id)"
        >{{ tab.label }}</button>
      </nav>

      <AdminDataPanel v-show="activeTab === 'data'" />
      <AdminQualityPanel v-show="activeTab === 'quality'" />
      <AdminAgentResearchPanel v-if="admin.canWrite" v-show="activeTab === 'agent'" />
      <AdminDecisionCardPanel v-if="admin.canWrite" v-show="activeTab === 'agent'" />
      <AdminAuditPanel v-if="admin.isAdmin" v-show="activeTab === 'audit'" />
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import AdminAuditPanel from '../components/admin/AdminAuditPanel.vue'
import AdminAgentResearchPanel from '../components/admin/AdminAgentResearchPanel.vue'
import AdminDecisionCardPanel from '../components/admin/AdminDecisionCardPanel.vue'
import AdminDataPanel from '../components/admin/AdminDataPanel.vue'
import AdminLoginPanel from '../components/admin/AdminLoginPanel.vue'
import AdminQualityPanel from '../components/admin/AdminQualityPanel.vue'
import AdminStatsCards from '../components/admin/AdminStatsCards.vue'
import { useAdminStore } from '../stores/admin'

type TabId = 'data' | 'quality' | 'agent' | 'audit'

const admin = useAdminStore()
const activeTab = ref<TabId>('data')

const roleLabel = computed(() => {
  return { viewer: '只读', operator: '运营', admin: '管理员' }[admin.role ?? 'viewer']
})

const visibleTabs = computed(() => {
  const tabs: Array<{ id: TabId; label: string }> = [
    { id: 'data', label: '招生数据' },
    { id: 'quality', label: '质检任务' },
  ]
  if (admin.canWrite) tabs.push({ id: 'agent', label: 'Agent 研究' })
  if (admin.isAdmin) tabs.push({ id: 'audit', label: '操作审计' })
  return tabs
})

function selectTab(tab: TabId): void {
  activeTab.value = tab
  if (tab === 'data') void admin.loadSchools(admin.schoolPage)
  if (tab === 'quality') void admin.loadJobs(admin.jobsPage)
  if (tab === 'audit') void admin.loadAudit(admin.auditPage)
}

onMounted(() => {
  if (admin.isAuthenticated) void admin.refreshAll()
})

onUnmounted(() => admin.stopPolling())
</script>
