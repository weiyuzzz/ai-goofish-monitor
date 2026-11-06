<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { Task } from '@/types/task.d.ts'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Button } from '@/components/ui/button'

type FormMode = 'create' | 'edit'

const AUTO_ACCOUNT_VALUE = '__auto__'
const MANUAL_CRON_VALUE = '__manual__'

const props = defineProps<{
  mode: FormMode
  initialData?: Task | null
  accountOptions: { name: string; path: string }[]
  isSubmitting?: boolean
}>()

const emit = defineEmits<{
  (e: 'submit', data: Record<string, unknown>): void
  (e: 'cancel'): void
}>()

const { t } = useI18n()

const form = ref({
  task_name: '',
  keyword: '',
  latest_count: 10,
  cron: '',
  account_state_file: AUTO_ACCOUNT_VALUE,
  kongfz_playwright: 'auto',
  notify_removal: false,
  enabled: true,
})

const cronMode = ref<'preset' | 'custom'>('preset')

const cronPresets = computed(() => [
  { value: MANUAL_CRON_VALUE, label: t('tasks.form.cron.manual') },
  { value: '* * * * *', label: t('tasks.form.cron.every1Minute') },
  { value: '*/5 * * * *', label: t('tasks.form.cron.every5Minutes') },
  { value: '*/10 * * * *', label: t('tasks.form.cron.every15Minutes') },
  { value: '*/30 * * * *', label: t('tasks.form.cron.every30Minutes') },
  { value: '0 * * * *', label: t('tasks.form.cron.hourly') },
  { value: '0 */6 * * *', label: t('tasks.form.cron.every6Hours') },
  { value: '0 8 * * *', label: t('tasks.form.cron.daily8') },
  { value: '0 8,18 * * *', label: t('kongfz.form.cronTwiceDaily') },
])

function isPresetCron(value: string): boolean {
  if (!value) return true
  return cronPresets.value.some((preset) => preset.value === value)
}

const presetCronValue = computed({
  get: () => (isPresetCron(form.value.cron) ? (form.value.cron || MANUAL_CRON_VALUE) : MANUAL_CRON_VALUE),
  set: (value: string) => {
    form.value.cron = value === MANUAL_CRON_VALUE ? '' : value
  },
})

watch(
  () => [props.mode, props.initialData] as const,
  () => {
    if (props.mode === 'edit' && props.initialData) {
      const task = props.initialData
      form.value = {
        task_name: task.task_name ?? '',
        keyword: task.keyword ?? '',
        latest_count: task.latest_count ?? 10,
        cron: task.cron ?? '',
        account_state_file: task.account_state_file || AUTO_ACCOUNT_VALUE,
        kongfz_playwright: task.kongfz_playwright || 'auto',
        notify_removal: task.notify_removal ?? false,
        enabled: task.enabled ?? true,
      }
      cronMode.value = isPresetCron(form.value.cron) ? 'preset' : 'custom'
      if (cronMode.value === 'custom') {
        form.value.cron = task.cron ?? ''
      }
    } else {
      form.value = {
        task_name: '',
        keyword: '',
        latest_count: 10,
        cron: '',
        account_state_file: AUTO_ACCOUNT_VALUE,
        kongfz_playwright: 'auto',
        notify_removal: false,
        enabled: true,
      }
      cronMode.value = 'preset'
    }
  },
  { immediate: true },
)

const validationError = computed(() => {
  if (!form.value.keyword.trim()) return t('kongfz.form.keywordRequired')
  if (!form.value.task_name.trim()) return t('kongfz.form.taskNameRequired')
  const count = Number(form.value.latest_count)
  if (!Number.isFinite(count) || count < 1 || count > 200) return t('kongfz.form.countRange')
  return ''
})

function handleSubmit() {
  if (validationError.value) return
  const accountState =
    form.value.account_state_file === AUTO_ACCOUNT_VALUE ? null : form.value.account_state_file
  emit('submit', {
    task_name: form.value.task_name.trim(),
    keyword: form.value.keyword.trim(),
    platform: 'kongfz',
    latest_count: Math.round(Number(form.value.latest_count)),
    cron: form.value.cron.trim() || null,
    account_state_file: accountState,
    account_strategy: accountState ? 'fixed' : 'auto',
    kongfz_playwright: form.value.kongfz_playwright || 'auto',
    notify_all_new: true,
    notify_removal: form.value.notify_removal,
    decision_mode: 'keyword',
    keyword_rules: [form.value.keyword.trim()],
    max_pages: 1,
    personal_only: false,
    analyze_images: false,
    description: '',
    free_shipping: false,
    enabled: form.value.enabled,
  })
}
</script>

<template>
  <div class="space-y-4">
    <div class="grid gap-4 md:grid-cols-2">
      <div class="grid gap-2">
        <Label for="kfz-task-name">{{ t('kongfz.form.taskName') }}</Label>
        <Input id="kfz-task-name" v-model="form.task_name" :placeholder="t('kongfz.form.taskNamePlaceholder')" />
      </div>
      <div class="grid gap-2">
        <Label for="kfz-keyword">{{ t('kongfz.form.keyword') }}</Label>
        <Input id="kfz-keyword" v-model="form.keyword" :placeholder="t('kongfz.form.keywordPlaceholder')" />
      </div>
    </div>

    <div class="grid gap-4 md:grid-cols-2">
      <div class="grid gap-2">
        <Label for="kfz-count">{{ t('kongfz.form.latestCount') }}</Label>
        <Input id="kfz-count" v-model="form.latest_count" type="number" min="1" max="200" />
        <p class="text-xs text-slate-500">{{ t('kongfz.form.latestCountHint') }}</p>
      </div>
      <div class="grid gap-2">
        <Label for="kfz-account">{{ t('kongfz.form.account') }}</Label>
        <Select v-model="form.account_state_file">
          <SelectTrigger id="kfz-account">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem :value="AUTO_ACCOUNT_VALUE">{{ t('kongfz.form.accountAuto') }}</SelectItem>
            <SelectItem v-for="account in accountOptions" :key="account.path" :value="account.path">
              {{ account.name }}
            </SelectItem>
          </SelectContent>
        </Select>
        <p class="text-xs text-slate-500">{{ t('kongfz.form.accountHint') }}</p>
      </div>
      <div class="grid gap-2">
        <Label for="kfz-pw-mode">{{ t('kongfz.form.playwrightMode') }}</Label>
        <Select v-model="form.kongfz_playwright">
          <SelectTrigger id="kfz-pw-mode">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="auto">{{ t('kongfz.form.playwrightAuto') }}</SelectItem>
            <SelectItem value="on">{{ t('kongfz.form.playwrightOn') }}</SelectItem>
            <SelectItem value="off">{{ t('kongfz.form.playwrightOff') }}</SelectItem>
          </SelectContent>
        </Select>
        <p class="text-xs text-slate-500">{{ t('kongfz.form.playwrightModeHint') }}</p>
      </div>
    </div>

    <div class="grid gap-2">
      <div class="flex items-center justify-between">
        <Label>{{ t('kongfz.form.schedule') }}</Label>
        <button
          type="button"
          class="text-xs text-sky-600 underline-offset-2 hover:underline"
          @click="cronMode = cronMode === 'preset' ? 'custom' : 'preset'"
        >
          {{ cronMode === 'preset' ? t('kongfz.form.useCustomCron') : t('kongfz.form.usePresetCron') }}
        </button>
      </div>
      <Select v-if="cronMode === 'preset'" v-model="presetCronValue">
        <SelectTrigger>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem v-for="preset in cronPresets" :key="preset.value" :value="preset.value">
            {{ preset.label }}
          </SelectItem>
        </SelectContent>
      </Select>
      <Input v-else v-model="form.cron" placeholder="*/10 * * * *" />
      <p class="text-xs text-slate-500">{{ t('kongfz.form.scheduleHint') }}</p>
    </div>

    <div class="grid gap-3 md:grid-cols-2">
      <div class="app-surface-subtle flex items-center justify-between gap-3 p-3">
        <div>
          <p class="text-sm font-medium text-slate-800">{{ t('kongfz.form.notifyRemoval') }}</p>
          <p class="text-xs text-slate-500">{{ t('kongfz.form.notifyRemovalHint') }}</p>
        </div>
        <Switch v-model="form.notify_removal" />
      </div>
      <div class="app-surface-subtle flex items-center justify-between gap-3 p-3">
        <div>
          <p class="text-sm font-medium text-slate-800">{{ t('kongfz.form.enabled') }}</p>
          <p class="text-xs text-slate-500">{{ t('kongfz.form.enabledHint') }}</p>
        </div>
        <Switch v-model="form.enabled" />
      </div>
    </div>

    <p v-if="validationError" class="text-sm text-red-600">{{ validationError }}</p>

    <div class="flex justify-end gap-2">
      <Button variant="outline" type="button" @click="emit('cancel')">{{ t('common.cancel') }}</Button>
      <Button type="button" :disabled="!!validationError || props.isSubmitting" @click="handleSubmit">
        {{ t('common.save') }}
      </Button>
    </div>
  </div>
</template>