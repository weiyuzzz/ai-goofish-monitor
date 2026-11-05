<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  BookOpen,
  ExternalLink,
  KeyRound,
  Loader2,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Square,
  Trash2,
} from 'lucide-vue-next'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { toast } from '@/components/ui/toast'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import KongfzTaskForm from '@/components/kongfz/KongfzTaskForm.vue'
import { formatRelativeTimeFromNow } from '@/i18n'
import * as kongfzApi from '@/api/kongfz'
import type {
  KongfzAccount,
  KongfzMonitor,
  KongfzProductInfo,
  KongfzResultItem,
} from '@/api/kongfz'
import * as taskApi from '@/api/tasks'
import type { Task } from '@/types/task.d.ts'
import { useWebSocket } from '@/composables/useWebSocket'

const { t } = useI18n()
const { on } = useWebSocket()

const monitors = ref<KongfzMonitor[]>([])
const accounts = ref<KongfzAccount[]>([])
const stateDir = ref('')
const isLoading = ref(false)
const error = ref<Error | null>(null)

const loginJob = ref<{
  jobId: string
  status: 'running' | 'done' | 'failed'
  output: string
  stateFile: string
} | null>(null)
const isStartingLogin = ref(false)
const verifyResults = reactive<Record<string, { ok: boolean; message: string }>>({})
const verifyingPath = ref<string | null>(null)
const deletingAccount = ref<string | null>(null)

const isFormOpen = ref(false)
const formMode = ref<'create' | 'edit'>('create')
const editingTask = ref<Task | null>(null)
const isSubmitting = ref(false)

const isImportOpen = ref(false)
const importDraft = ref('')
const isImporting = ref(false)

const selectedMonitorId = ref<number | null>(null)
const windowItems = ref<KongfzResultItem[]>([])
const isWindowLoading = ref(false)
const onlyNew = ref(false)
const windowMeta = ref<{
  newCount: number
  backfillCount: number
  latestRunAt: string | null
  size: number
}>({
  newCount: 0,
  backfillCount: 0,
  latestRunAt: null,
  size: 0,
})

const previewKeyword = ref('')
const previewLimit = ref(20)
const previewItems = ref<KongfzProductInfo[]>([])
const previewMeta = ref<{ scene: string; fromState: string | null; totalFound: number } | null>(null)
const isPreviewLoading = ref(false)

const previewLimitOptions = [10, 20, 30, 50]

const accountOptions = computed(() =>
  accounts.value
    .filter((account) => account.exists)
    .map((account) => ({ name: account.name, path: account.path })),
)

const selectedMonitor = computed(
  () => monitors.value.find((monitor) => monitor.id === selectedMonitorId.value) ?? null,
)

const newCount = computed(
  () =>
    windowMeta.value.newCount ??
    windowItems.value.filter((item) => item.is_new_listing).length,
)

const backfillCount = computed(
  () =>
    windowMeta.value.backfillCount ??
    windowItems.value.filter((item) => item.is_backfill).length,
)

const displayItems = computed(() =>
  onlyNew.value ? windowItems.value.filter((item) => item.is_new_listing) : windowItems.value,
)

const runningCount = computed(() => monitors.value.filter((monitor) => monitor.enabled).length)

let loginPollTimer: ReturnType<typeof setInterval> | null = null

async function fetchMonitors(options?: { silent?: boolean }) {
  if (!options?.silent) isLoading.value = true
  error.value = null
  try {
    const response = await kongfzApi.getMonitors()
    monitors.value = response.monitors
    if (
      selectedMonitorId.value !== null &&
      !monitors.value.some((monitor) => monitor.id === selectedMonitorId.value)
    ) {
      selectedMonitorId.value = null
      windowItems.value = []
    }
  } catch (e) {
    error.value = e as Error
  } finally {
    if (!options?.silent) isLoading.value = false
  }
}

async function fetchAccounts() {
  try {
    const response = await kongfzApi.getAccounts()
    accounts.value = response.accounts
    stateDir.value = response.state_dir
  } catch (e) {
    console.error(e)
  }
}

async function fetchAll() {
  await Promise.all([fetchMonitors(), fetchAccounts()])
}

async function loadWindowItems(monitorId: number) {
  selectedMonitorId.value = monitorId
  isWindowLoading.value = true
  try {
    const response = await kongfzApi.getItems({ task_id: monitorId, limit: 200 })
    windowItems.value = response.items
    windowMeta.value = {
      newCount: response.new_count ?? response.items.filter((item) => item.is_new_listing).length,
      backfillCount:
        response.backfill_count ?? response.items.filter((item) => item.is_backfill).length,
      latestRunAt: response.latest_run_at ?? null,
      size: response.window_size ?? response.items.length,
    }
  } catch (e) {
    toast({ title: t('kongfz.errors.loadItems'), description: (e as Error).message, variant: 'destructive' })
    windowItems.value = []
  } finally {
    isWindowLoading.value = false
  }
}

function openCreateForm() {
  formMode.value = 'create'
  editingTask.value = null
  isFormOpen.value = true
}

function openEditForm(monitor: KongfzMonitor) {
  formMode.value = 'edit'
  editingTask.value = {
    id: monitor.id,
    task_name: monitor.task_name,
    enabled: monitor.enabled,
    keyword: monitor.keyword,
    description: '',
    analyze_images: false,
    max_pages: 1,
    personal_only: false,
    min_price: null,
    max_price: null,
    cron: monitor.cron,
    ai_prompt_base_file: '',
    ai_prompt_criteria_file: '',
    account_state_file: monitor.account_state_file,
    account_strategy: monitor.account_state_file ? 'fixed' : 'auto',
    free_shipping: false,
    new_publish_option: null,
    region: null,
    decision_mode: 'keyword',
    keyword_rules: [monitor.keyword],
    is_running: false,
    platform: 'kongfz',
    latest_count: monitor.latest_count,
    notify_all_new: monitor.notify_all_new,
    notify_removal: monitor.notify_removal,
  } as Task
  isFormOpen.value = true
}

async function handleFormSubmit(payload: Record<string, unknown>) {
  isSubmitting.value = true
  try {
    if (formMode.value === 'create') {
      await taskApi.createTask(payload as never)
      toast({ title: t('kongfz.messages.created') })
    } else if (editingTask.value?.id != null) {
      await taskApi.updateTask(editingTask.value.id, payload as never)
      toast({ title: t('kongfz.messages.updated') })
    }
    isFormOpen.value = false
    await fetchMonitors()
  } catch (e) {
    toast({ title: t('kongfz.errors.save'), description: (e as Error).message, variant: 'destructive' })
  } finally {
    isSubmitting.value = false
  }
}

async function startMonitor(monitor: KongfzMonitor) {
  try {
    await taskApi.startTask(monitor.id)
    toast({ title: t('kongfz.messages.started', { name: monitor.task_name }) })
    await fetchMonitors({ silent: true })
  } catch (e) {
    toast({ title: t('kongfz.errors.start'), description: (e as Error).message, variant: 'destructive' })
  }
}

async function stopMonitor(monitor: KongfzMonitor) {
  try {
    // 真正的「停止监控」：关闭定时（从调度器移除）+ 终止当轮运行
    const result = await taskApi.stopTask(monitor.id, { disable: true })
    toast({
      title: t('kongfz.messages.stopped', { name: monitor.task_name }),
      description: result.message,
    })
    await fetchMonitors({ silent: true })
  } catch (e) {
    toast({ title: t('kongfz.errors.stop'), description: (e as Error).message, variant: 'destructive' })
  }
}

async function handleStartMonitor(monitor: KongfzMonitor) {
  try {
    await taskApi.updateTask(monitor.id, { enabled: true } as never)
    toast({ title: t('kongfz.messages.resumed', { name: monitor.task_name }) })
    await fetchMonitors()
  } catch (e) {
    toast({ title: t('kongfz.errors.save'), description: (e as Error).message, variant: 'destructive' })
  }
}

async function removeMonitor(monitor: KongfzMonitor) {
  try {
    await taskApi.deleteTask(monitor.id)
    toast({ title: t('kongfz.messages.deleted') })
    if (selectedMonitorId.value === monitor.id) {
      selectedMonitorId.value = null
      windowItems.value = []
    }
    await fetchMonitors()
  } catch (e) {
    toast({ title: t('kongfz.errors.delete'), description: (e as Error).message, variant: 'destructive' })
  }
}

async function startScanLogin() {
  isStartingLogin.value = true
  try {
    const response = await kongfzApi.startLogin({ timeout: 900 })
    loginJob.value = {
      jobId: response.job_id,
      status: 'running',
      output: response.message,
      stateFile: response.state_file,
    }
    toast({ title: t('kongfz.messages.loginStarted') })
    startLoginPolling(response.job_id)
  } catch (e) {
    toast({ title: t('kongfz.errors.login'), description: (e as Error).message, variant: 'destructive' })
  } finally {
    isStartingLogin.value = false
  }
}

function startLoginPolling(jobId: string) {
  stopLoginPolling()
  loginPollTimer = setInterval(async () => {
    try {
      const status = await kongfzApi.getLoginStatus(jobId)
      if (loginJob.value) {
        loginJob.value.status = status.status
        loginJob.value.output = status.output
      }
      if (status.status !== 'running') {
        stopLoginPolling()
        await fetchAccounts()
        if (status.status === 'done') {
          toast({ title: t('kongfz.messages.loginFinished') })
        } else {
          toast({
            title: t('kongfz.errors.loginFailed'),
            description: status.output.slice(-300),
            variant: 'destructive',
          })
        }
      }
    } catch {
      stopLoginPolling()
    }
  }, 2000)
}

function stopLoginPolling() {
  if (loginPollTimer) {
    clearInterval(loginPollTimer)
    loginPollTimer = null
  }
}

async function verifyAccount(account: KongfzAccount) {
  verifyingPath.value = account.path
  try {
    const response = await kongfzApi.verifyAccount(account.path)
    verifyResults[account.path] = { ok: response.ok, message: response.message }
  } catch (e) {
    verifyResults[account.path] = { ok: false, message: (e as Error).message }
  } finally {
    verifyingPath.value = null
  }
}

async function handleImportAccount() {
  const content = importDraft.value.trim()
  if (!content) return
  isImporting.value = true
  try {
    const response = await kongfzApi.importAccount({ content })
    toast({ title: t('kongfz.messages.imported'), description: response.path })
    isImportOpen.value = false
    importDraft.value = ''
    await fetchAccounts()
  } catch (e) {
    toast({ title: t('kongfz.errors.import'), description: (e as Error).message, variant: 'destructive' })
  } finally {
    isImporting.value = false
  }
}

async function removeAccount(account: KongfzAccount) {
  deletingAccount.value = account.name
  try {
    await kongfzApi.deleteAccount(account.name)
    toast({ title: t('kongfz.messages.accountDeleted') })
    await fetchAccounts()
  } catch (e) {
    toast({ title: t('common.error'), description: (e as Error).message, variant: 'destructive' })
  } finally {
    deletingAccount.value = null
  }
}

async function runPreview() {
  const keyword = previewKeyword.value.trim()
  if (!keyword) return
  isPreviewLoading.value = true
  previewMeta.value = null
  try {
    const response = await kongfzApi.previewLatest(keyword, previewLimit.value)
    previewItems.value = response.items
    previewMeta.value = {
      scene: response.scene,
      fromState: response.from_state_file,
      totalFound: response.total_found,
    }
  } catch (e) {
    previewItems.value = []
    toast({ title: t('kongfz.errors.preview'), description: (e as Error).message, variant: 'destructive' })
  } finally {
    isPreviewLoading.value = false
  }
}

function useMonitorKeywordForPreview(monitor: KongfzMonitor) {
  previewKeyword.value = monitor.keyword
  previewLimit.value = Math.min(50, Math.max(10, monitor.latest_count))
  void runPreview()
}

function statusBadge(monitor: KongfzMonitor) {
  if (!monitor.enabled) return { label: t('common.disabled'), variant: 'outline' as const }
  if (monitor.window_size > 0) return { label: t('kongfz.status.watching'), variant: 'default' as const }
  return { label: t('kongfz.status.pending'), variant: 'outline' as const }
}

/** 最近一次真实运行：相对时间 + 具体时刻，明确区分「没跑」和「跑了但没新书」 */
function lastRunText(monitor: KongfzMonitor): string {
  if (!monitor.last_run_at) return t('kongfz.monitors.neverRun')
  return `${formatRelativeTimeFromNow(monitor.last_run_at)} (${formatClock(monitor.last_run_at)})`
}

function nextRunText(monitor: KongfzMonitor): string {
  if (!monitor.next_run_at) return t('kongfz.monitors.manualOnly')
  return formatClock(monitor.next_run_at)
}

function formatClock(value?: string | null): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleTimeString('zh-CN', { hour12: false })
}

function runResultText(monitor: KongfzMonitor): string {
  return t('kongfz.monitors.runResult', {
    added: monitor.last_run_new_count ?? 0,
    backfill: monitor.last_run_backfill_count ?? 0,
    removed: monitor.last_run_removed_count ?? 0,
  })
}

on('tasks_updated', () => fetchMonitors({ silent: true }))
on('task_status_changed', () => {
  fetchMonitors({ silent: true })
})

onMounted(fetchAll)
onUnmounted(stopLoginPolling)
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
      <div>
        <h1 class="flex items-center gap-2 text-2xl font-bold text-gray-800">
          <BookOpen class="h-6 w-6 text-amber-600" />
          {{ t('kongfz.title') }}
        </h1>
        <p class="mt-1 text-sm text-slate-500">{{ t('kongfz.subtitle') }}</p>
      </div>
      <div class="flex flex-wrap gap-2">
        <Button variant="outline" :disabled="isLoading" @click="fetchAll">
          <RefreshCw class="h-4 w-4" :class="isLoading ? 'animate-spin' : ''" />
          {{ t('common.refresh') }}
        </Button>
        <Button @click="openCreateForm">
          <Plus class="h-4 w-4" />
          {{ t('kongfz.actions.newMonitor') }}
        </Button>
      </div>
    </div>

    <div v-if="error" class="app-alert-error" role="alert">
      <strong class="font-bold">{{ t('common.error') }}</strong>
      <span class="block sm:inline">{{ error.message }}</span>
    </div>

    <!-- 账号配置栏 -->
    <Card class="app-surface overflow-hidden border-none">
      <CardHeader>
        <div class="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <div class="space-y-1">
            <CardTitle class="flex items-center gap-2">
              <KeyRound class="h-5 w-5 text-amber-600" />
              {{ t('kongfz.account.title') }}
            </CardTitle>
            <CardDescription>{{ t('kongfz.account.description') }}</CardDescription>
            <p class="text-xs text-slate-400">{{ t('kongfz.account.dir', { dir: stateDir }) }}</p>
          </div>
          <div class="flex flex-wrap gap-2">
            <Button variant="outline" :disabled="isStartingLogin || loginJob?.status === 'running'" @click="startScanLogin">
              <Loader2 v-if="loginJob?.status === 'running'" class="h-4 w-4 animate-spin" />
              <KeyRound v-else class="h-4 w-4" />
              {{ loginJob?.status === 'running' ? t('kongfz.account.waitingLogin') : t('kongfz.account.scanLogin') }}
            </Button>
            <Button variant="ghost" @click="isImportOpen = true">
              {{ t('kongfz.account.import') }}
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent class="space-y-3">
        <div v-if="accounts.length === 0" class="rounded-2xl border border-dashed border-slate-200 p-4 text-sm text-slate-500">
          {{ t('kongfz.account.empty') }}
        </div>
        <div
          v-for="account in accounts"
          :key="account.path"
          class="app-surface-subtle flex flex-col gap-3 p-4 md:flex-row md:items-center md:justify-between"
        >
          <div class="space-y-1">
            <div class="flex flex-wrap items-center gap-2">
              <span class="font-semibold text-slate-800">{{ account.name }}</span>
              <Badge :variant="account.valid ? 'default' : 'outline'">
                {{ account.valid ? t('kongfz.account.cookieOk') : t('kongfz.account.cookieMissing') }}
              </Badge>
              <Badge v-if="verifyResults[account.path]" :variant="verifyResults[account.path]?.ok ? 'default' : 'outline'">
                {{ verifyResults[account.path]?.message }}
              </Badge>
            </div>
            <p class="text-xs text-slate-500">
              {{ t('kongfz.account.updatedAt') }}: {{ account.updated_at || '-' }} ·
              {{ t('kongfz.account.cookieCount', { count: account.cookie_count }) }}
            </p>
          </div>
          <div class="flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              :disabled="verifyingPath === account.path"
              @click="verifyAccount(account)"
            >
              <ShieldCheck class="h-4 w-4" />
              {{ t('kongfz.account.verify') }}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              :disabled="deletingAccount === account.name"
              @click="removeAccount(account)"
            >
              <Trash2 class="h-4 w-4" />
              {{ t('common.delete') }}
            </Button>
          </div>
        </div>

        <div v-if="loginJob" class="rounded-2xl border border-slate-200 bg-slate-900/95 p-3 text-xs text-slate-100">
          <div class="mb-2 flex items-center justify-between">
            <span class="font-semibold">
              {{ t('kongfz.account.loginStatus') }}: {{ loginJob.status }}
            </span>
            <span class="text-slate-400">{{ loginJob.stateFile }}</span>
          </div>
          <pre class="max-h-40 overflow-y-auto whitespace-pre-wrap break-all text-[11px] leading-5 text-slate-300">{{ loginJob.output }}</pre>
        </div>
      </CardContent>
    </Card>

    <!-- 监控任务 -->
    <Card class="app-surface overflow-hidden border-none">
      <CardHeader>
        <div class="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
          <div>
            <CardTitle>{{ t('kongfz.monitors.title') }}</CardTitle>
            <CardDescription>{{ t('kongfz.monitors.description') }}</CardDescription>
          </div>
          <Badge variant="outline" class="border-amber-200 bg-amber-50 text-amber-700">
            {{ t('kongfz.monitors.enabledCount', { count: runningCount }) }}
          </Badge>
        </div>
      </CardHeader>
      <CardContent class="space-y-3">
        <div v-if="isLoading && monitors.length === 0" class="py-8 text-center text-sm text-slate-500">
          {{ t('common.loading') }}
        </div>
        <div v-else-if="monitors.length === 0" class="rounded-2xl border border-dashed border-slate-200 p-6 text-center text-sm text-slate-500">
          {{ t('kongfz.monitors.empty') }}
        </div>

        <div
          v-for="monitor in monitors"
          :key="monitor.id"
          class="app-surface-subtle flex flex-col gap-3 p-4 lg:flex-row lg:items-center lg:justify-between"
          :class="selectedMonitorId === monitor.id ? 'ring-2 ring-amber-300' : ''"
        >
          <div class="min-w-0 space-y-1">
            <div class="flex flex-wrap items-center gap-2">
              <span class="font-semibold text-slate-900">{{ monitor.task_name }}</span>
              <Badge :variant="statusBadge(monitor).variant">{{ statusBadge(monitor).label }}</Badge>
              <Badge v-if="monitor.cron" variant="outline">{{ monitor.cron }}</Badge>
              <Badge
                v-if="monitor.new_count > 0"
                variant="default"
                class="border-emerald-200 bg-emerald-50 text-emerald-700"
              >
                {{ t('kongfz.window.newCount', { count: monitor.new_count }) }}
              </Badge>
              <Badge
                v-if="monitor.last_run_status === 'failed'"
                variant="destructive"
                class="border-red-200 bg-red-50 text-red-700"
              >
                {{ t('kongfz.monitors.runFailed') }}
              </Badge>
              <Badge
                v-else-if="monitor.last_run_status === 'skipped'"
                variant="outline"
                class="border-amber-200 bg-amber-50 text-amber-700"
              >
                {{ t('kongfz.monitors.runSkipped') }}
              </Badge>
            </div>
            <p class="text-sm text-slate-600">
              {{ t('kongfz.monitors.keyword') }}: <span class="font-medium">{{ monitor.keyword }}</span>
            </p>
            <p class="text-xs text-slate-500">
              {{ t('kongfz.monitors.window') }}: {{ monitor.window_size }} / {{ monitor.latest_count }} ·
              {{ t('kongfz.monitors.lastRun') }}: {{ lastRunText(monitor) }} ·
              {{ t('kongfz.monitors.nextRun') }}: {{ nextRunText(monitor) }} ·
              {{ t('kongfz.monitors.account') }}: {{ monitor.account?.name || t('kongfz.form.accountAuto') }}
            </p>
            <p class="text-xs text-slate-400">{{ runResultText(monitor) }}</p>
            <p v-if="monitor.last_run_status === 'failed' && monitor.last_run_message" class="text-xs text-red-600">
              {{ monitor.last_run_message }}
            </p>
          </div>

          <div class="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" @click="loadWindowItems(monitor.id)">
              {{ t('kongfz.actions.viewWindow') }}
            </Button>
            <Button variant="outline" size="sm" @click="useMonitorKeywordForPreview(monitor)">
              <Search class="h-4 w-4" />
              {{ t('kongfz.actions.preview') }}
            </Button>
            <Button v-if="monitor.enabled" variant="outline" size="sm" @click="startMonitor(monitor)">
              <Play class="h-4 w-4" />
              {{ t('kongfz.actions.runNow') }}
            </Button>
            <Button v-if="monitor.enabled" variant="ghost" size="sm" @click="stopMonitor(monitor)">
              <Square class="h-4 w-4" />
              {{ t('kongfz.actions.stop') }}
            </Button>
            <Button v-else variant="outline" size="sm" @click="handleStartMonitor(monitor)">
              <Play class="h-4 w-4" />
              {{ t('kongfz.actions.resume') }}
            </Button>
            <Button variant="ghost" size="sm" @click="openEditForm(monitor)">
              <Pencil class="h-4 w-4" />
              {{ t('common.edit') }}
            </Button>
            <Button variant="ghost" size="sm" @click="removeMonitor(monitor)">
              <Trash2 class="h-4 w-4" />
              {{ t('common.delete') }}
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>

    <!-- 监控窗口 -->
    <Card v-if="selectedMonitor" class="app-surface overflow-hidden border-none">
      <CardHeader>
        <div class="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
          <div>
            <CardTitle>{{ t('kongfz.window.title', { keyword: selectedMonitor.keyword }) }}</CardTitle>
            <CardDescription>{{ t('kongfz.window.description', { count: selectedMonitor.latest_count }) }}</CardDescription>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <Badge variant="outline">{{ t('kongfz.window.count', { count: windowItems.length }) }}</Badge>
            <Badge v-if="newCount > 0" variant="default" class="border-emerald-200 bg-emerald-50 text-emerald-700">
              {{ t('kongfz.window.newCount', { count: newCount }) }}
            </Badge>
            <Badge
              v-if="backfillCount > 0"
              variant="outline"
              class="border-amber-200 bg-amber-50 text-amber-700"
            >
              {{ t('kongfz.window.backfillCount', { count: backfillCount }) }}
            </Badge>
            <div class="flex items-center gap-2 rounded-full border border-slate-200 bg-slate-50 px-3 py-1.5">
              <Switch id="only-new" v-model="onlyNew" />
              <label for="only-new" class="cursor-pointer text-xs font-medium text-slate-600">
                {{ t('kongfz.window.onlyNew') }}
              </label>
            </div>
            <Button variant="outline" size="sm" :disabled="isWindowLoading" @click="loadWindowItems(selectedMonitor.id)">
              <RefreshCw class="h-4 w-4" :class="isWindowLoading ? 'animate-spin' : ''" />
              {{ t('common.refresh') }}
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div v-if="isWindowLoading" class="py-6 text-center text-sm text-slate-500">{{ t('common.loading') }}</div>
        <div v-else-if="displayItems.length === 0" class="rounded-2xl border border-dashed border-slate-200 p-6 text-center text-sm text-slate-500">
          {{ onlyNew ? t('kongfz.window.emptyNew') : t('kongfz.window.empty') }}
        </div>
        <div v-else class="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          <div
            v-for="(item, index) in displayItems"
            :key="index"
            class="app-surface-subtle flex gap-3 p-3"
            :class="item.is_new_listing ? 'ring-2 ring-emerald-300' : ''"
          >
            <img
              v-if="item.商品信息?.商品主图链接"
              :src="item.商品信息.商品主图链接"
              class="h-20 w-20 flex-shrink-0 rounded-xl object-cover"
              alt=""
              loading="lazy"
            />
            <div class="min-w-0 space-y-1">
              <div class="flex flex-wrap items-center gap-2">
                <Badge variant="outline">#{{ (item.window_rank ?? index) + 1 }}</Badge>
                <Badge
                  v-if="item.is_new_listing"
                  variant="default"
                  class="border-emerald-200 bg-emerald-50 text-emerald-700"
                >
                  {{ t('kongfz.window.newBadge') }}
                </Badge>
                <Badge
                  v-else-if="item.is_backfill"
                  variant="outline"
                  class="border-amber-200 bg-amber-50 text-amber-700"
                >
                  {{ t('kongfz.window.backfillBadge') }}
                </Badge>
              </div>
              <a
                :href="item.商品信息?.商品链接"
                target="_blank"
                rel="noreferrer"
                class="line-clamp-2 text-sm font-medium text-slate-800 hover:text-amber-700"
              >
                {{ item.商品信息?.商品标题 }}
                <ExternalLink class="ml-1 inline h-3 w-3" />
              </a>
              <p class="text-xs text-slate-500">
                <span class="font-semibold text-rose-600">¥{{ item.商品信息?.当前售价 }}</span>
                · {{ item.商品信息?.品相 || '-' }} · {{ item.商品信息?.卖家昵称 }}
              </p>
              <p class="text-xs text-slate-400">
                {{ t('kongfz.window.listedAt') }}: {{ item.商品信息?.发布时间 }}
                <template v-if="item.first_seen_at">
                  · {{ t('kongfz.window.firstSeen') }}: {{ formatRelativeTimeFromNow(item.first_seen_at) }}
                </template>
              </p>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>

    <!-- 最新上架预览 -->
    <Card class="app-surface overflow-hidden border-none">
      <CardHeader>
        <CardTitle>{{ t('kongfz.preview.title') }}</CardTitle>
        <CardDescription>{{ t('kongfz.preview.description') }}</CardDescription>
      </CardHeader>
      <CardContent class="space-y-4">
        <div class="flex flex-col gap-3 md:flex-row md:items-end">
          <div class="grid flex-1 gap-2">
            <Label for="kfz-preview-keyword">{{ t('kongfz.preview.keyword') }}</Label>
            <Input
              id="kfz-preview-keyword"
              v-model="previewKeyword"
              :placeholder="t('kongfz.form.keywordPlaceholder')"
              @keyup.enter="runPreview"
            />
          </div>
          <div class="grid gap-2">
            <Label>{{ t('kongfz.preview.limit') }}</Label>
            <Select v-model="previewLimit">
              <SelectTrigger class="w-[120px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem v-for="option in previewLimitOptions" :key="option" :value="option">
                  {{ option }}
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
          <Button :disabled="isPreviewLoading || !previewKeyword.trim()" @click="runPreview">
            <Loader2 v-if="isPreviewLoading" class="h-4 w-4 animate-spin" />
            <Search v-else class="h-4 w-4" />
            {{ t('kongfz.preview.action') }}
          </Button>
        </div>

        <div v-if="previewMeta" class="rounded-2xl border border-dashed border-slate-200 px-4 py-2 text-xs text-slate-500">
          {{ t('kongfz.preview.meta', { scene: previewMeta.scene, total: previewMeta.totalFound }) }}
          <span v-if="previewMeta.fromState"> · {{ t('kongfz.preview.state', { file: previewMeta.fromState }) }}</span>
          <span v-else> · {{ t('kongfz.preview.guest') }}</span>
        </div>

        <div v-if="previewItems.length" class="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          <div v-for="(item, index) in previewItems" :key="item.商品ID || index" class="app-surface-subtle flex gap-3 p-3">
            <img
              v-if="item.商品主图链接"
              :src="item.商品主图链接"
              class="h-20 w-20 flex-shrink-0 rounded-xl object-cover"
              alt=""
              loading="lazy"
            />
            <div class="min-w-0 space-y-1">
              <a
                :href="item.商品链接"
                target="_blank"
                rel="noreferrer"
                class="line-clamp-2 text-sm font-medium text-slate-800 hover:text-amber-700"
              >
                {{ item.商品标题 }}
                <ExternalLink class="ml-1 inline h-3 w-3" />
              </a>
              <p class="text-xs text-slate-500">
                <span class="font-semibold text-rose-600">¥{{ item.当前售价 }}</span>
                · {{ item.品相 || '-' }} · {{ item.卖家昵称 }}
              </p>
              <p class="text-xs text-slate-400">{{ item.发布时间 }}</p>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>

    <Dialog v-model:open="isImportOpen">
      <DialogContent class="sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle>{{ t('kongfz.account.importTitle') }}</DialogTitle>
          <DialogDescription>{{ t('kongfz.account.importDescription') }}</DialogDescription>
        </DialogHeader>
        <Textarea v-model="importDraft" class="min-h-[200px] font-mono text-xs" placeholder='{"cookies":[{"domain":".kongfz.com","name":"...","value":"..."}]}' />
        <div class="flex justify-end gap-2">
          <Button variant="outline" @click="isImportOpen = false">{{ t('common.cancel') }}</Button>
          <Button :disabled="isImporting || !importDraft.trim()" @click="handleImportAccount">
            <Loader2 v-if="isImporting" class="h-4 w-4 animate-spin" />
            {{ t('common.save') }}
          </Button>
        </div>
      </DialogContent>
    </Dialog>

    <Dialog v-model:open="isFormOpen">
      <DialogContent class="max-h-[85vh] overflow-y-auto sm:max-w-[720px]">
        <DialogHeader>
          <DialogTitle>
            {{ formMode === 'create' ? t('kongfz.form.createTitle') : t('kongfz.form.editTitle') }}
          </DialogTitle>
          <DialogDescription>{{ t('kongfz.form.description') }}</DialogDescription>
        </DialogHeader>
        <KongfzTaskForm
          :mode="formMode"
          :initial-data="editingTask"
          :account-options="accountOptions"
          :is-submitting="isSubmitting"
          @submit="handleFormSubmit"
          @cancel="isFormOpen = false"
        />
      </DialogContent>
    </Dialog>
  </div>
</template>