<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  Activity,
  ArrowDownToLine,
  ArrowUpFromLine,
  Coins,
  Database,
  RefreshCw,
} from 'lucide-vue-next'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import Badge from '@/components/ui/badge/Badge.vue'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import TokenBarChart from '@/components/tokens/TokenBarChart.vue'
import { useTokenUsage } from '@/composables/useTokenUsage'
import { formatDateTime, formatNumber } from '@/i18n'
import type { TokenUsageGroup } from '@/types/token.d.ts'

const { t } = useI18n()
const { snapshot, days, isLoading, error, lastUpdatedAt, fetchSummary, setDays } =
  useTokenUsage(14)

const RANGE_OPTIONS = [7, 14, 30]
const SOURCE_LABEL_KEYS: Record<string, string> = {
  analysis: 'tokens.sourceLabels.analysis',
  prompt: 'tokens.sourceLabels.prompt',
  test: 'tokens.sourceLabels.test',
}

const today = computed(() => snapshot.value?.today ?? null)
const allTime = computed(() => snapshot.value?.all_time ?? null)
const recent = computed(() => snapshot.value?.recent || [])
const isLoadingFirstTime = computed(() => isLoading.value && !snapshot.value)

const avgPerRequest = computed(() => {
  const totals = today.value
  if (!totals || totals.requests === 0) return 0
  return Math.round(totals.total_tokens / totals.requests)
})

function shareOfToday(value: number) {
  const total = today.value?.total_tokens || 0
  if (total <= 0) return 0
  return Math.round((value / total) * 100)
}

const statCards = computed(() => [
  {
    label: t('tokens.cards.todayTotal'),
    value: formatNumber(today.value?.total_tokens || 0),
    detail: t('tokens.cards.allTimeDetail', {
      count: formatNumber(allTime.value?.total_tokens || 0),
    }),
    icon: Coins,
    color: 'text-primary',
    bg: 'bg-primary/10',
  },
  {
    label: t('tokens.cards.todayRequests'),
    value: formatNumber(today.value?.requests || 0),
    detail: t('tokens.cards.avgDetail', { count: formatNumber(avgPerRequest.value) }),
    icon: Activity,
    color: 'text-emerald-500',
    bg: 'bg-emerald-500/10',
  },
  {
    label: t('tokens.cards.inputTokens'),
    value: formatNumber(today.value?.prompt_tokens || 0),
    detail: t('tokens.cards.shareDetail', {
      percent: shareOfToday(today.value?.prompt_tokens || 0),
    }),
    icon: ArrowDownToLine,
    color: 'text-sky-600',
    bg: 'bg-sky-500/10',
  },
  {
    label: t('tokens.cards.outputTokens'),
    value: formatNumber(today.value?.completion_tokens || 0),
    detail: t('tokens.cards.shareDetail', {
      percent: shareOfToday(today.value?.completion_tokens || 0),
    }),
    icon: ArrowUpFromLine,
    color: 'text-amber-500',
    bg: 'bg-amber-500/10',
  },
])

const hourlyPoints = computed(() =>
  (snapshot.value?.hourly || []).map((item) => ({
    label: item.label,
    prompt: item.prompt_tokens,
    completion: item.completion_tokens,
  })),
)

const dailyPoints = computed(() =>
  (snapshot.value?.daily || []).map((item) => ({
    label: item.label || item.day.slice(5),
    prompt: item.prompt_tokens,
    completion: item.completion_tokens,
  })),
)

function withShares(groups: TokenUsageGroup[]) {
  const total = today.value?.total_tokens || 0
  return groups.map((group) => ({
    name: group.name,
    total: group.total_tokens,
    requests: group.requests,
    percent: total > 0 ? Math.max(2, Math.round((group.total_tokens / total) * 100)) : 0,
  }))
}

const modelGroups = computed(() => withShares(snapshot.value?.models || []))
const sourceGroups = computed(() => withShares(snapshot.value?.sources || []))

function sourceLabel(source: string) {
  const key = SOURCE_LABEL_KEYS[source]
  return key ? t(key) : t('tokens.sourceLabels.other')
}

function formatRecordTime(value: string) {
  return formatDateTime(value, {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}
</script>

<template>
  <div class="space-y-8 animate-fade-in">
    <div class="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
      <div>
        <h1 class="text-3xl font-black text-slate-800 tracking-tight flex items-center gap-3">
          <Coins class="w-8 h-8 text-primary" />
          {{ t('tokens.title') }}
        </h1>
        <p class="mt-1 font-medium text-slate-500">{{ t('tokens.description') }}</p>
        <p class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs font-bold text-slate-400">
          <span class="inline-flex items-center gap-1">
            <RefreshCw class="h-3 w-3" />
            {{ t('tokens.autoRefreshHint') }}
          </span>
          <span v-if="lastUpdatedAt">
            {{
              t('tokens.updatedAt', {
                time: formatDateTime(lastUpdatedAt, {
                  hour: '2-digit',
                  minute: '2-digit',
                  second: '2-digit',
                }),
              })
            }}
          </span>
        </p>
      </div>
      <div class="flex items-center gap-3">
        <Button variant="outline" size="sm" :disabled="isLoading" @click="fetchSummary">
          {{ isLoading ? t('common.loading') : t('tokens.refresh') }}
        </Button>
      </div>
    </div>

    <div v-if="error" class="app-alert-error" role="alert">
      {{ t('tokens.loadFailed') }}: {{ error.message }}
    </div>

    <div
      v-if="isLoadingFirstTime"
      class="rounded-2xl border border-dashed border-slate-200 bg-white/60 px-4 py-10 text-center text-sm text-slate-500"
    >
      {{ t('tokens.loading') }}
    </div>

    <template v-else>
      <div class="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4">
        <Card
          v-for="stat in statCards"
          :key="stat.label"
          class="app-surface border-none transition-all hover:-translate-y-0.5"
        >
          <CardContent class="p-6">
            <div class="flex items-center justify-between">
              <div>
                <p class="text-sm font-bold uppercase tracking-wider text-slate-400">
                  {{ stat.label }}
                </p>
                <h3 class="mt-1 text-2xl font-black text-slate-800">{{ stat.value }}</h3>
              </div>
              <div :class="[stat.bg, 'p-3 rounded-2xl']">
                <component :is="stat.icon" :class="['w-6 h-6', stat.color]" />
              </div>
            </div>
            <div class="mt-4 text-xs font-bold text-slate-500">{{ stat.detail }}</div>
          </CardContent>
        </Card>
      </div>

      <div class="grid grid-cols-1 gap-8 lg:grid-cols-3">
        <Card class="app-surface border-none lg:col-span-2">
          <CardHeader class="border-b border-slate-100/60 pb-5">
            <CardTitle class="text-lg font-bold text-slate-800">
              {{ t('tokens.hourly.title') }}
            </CardTitle>
            <p class="text-sm text-slate-500">{{ t('tokens.hourly.description') }}</p>
          </CardHeader>
          <CardContent class="p-6">
            <TokenBarChart
              :points="hourlyPoints"
              :hint="t('tokens.hourly.hint')"
              :max-labels="8"
              :height="220"
            />
          </CardContent>
        </Card>

        <Card class="app-surface border-none">
          <CardHeader class="border-b border-slate-100/60 pb-5">
            <CardTitle class="text-lg font-bold text-slate-800">
              {{ t('tokens.breakdown.title') }}
            </CardTitle>
          </CardHeader>
          <CardContent class="p-6">
            <div
              v-if="!modelGroups.length && !sourceGroups.length"
              class="rounded-2xl border border-dashed border-slate-200 bg-white/60 px-4 py-10 text-center text-sm text-slate-500"
            >
              {{ t('tokens.breakdown.empty') }}
            </div>
            <div v-else class="space-y-6">
              <div>
                <p class="mb-3 text-xs font-black uppercase tracking-widest text-slate-400">
                  {{ t('tokens.breakdown.models') }}
                </p>
                <ul class="space-y-3">
                  <li v-for="group in modelGroups" :key="group.name">
                    <div class="flex items-center justify-between gap-3 text-sm">
                      <span class="truncate font-bold text-slate-700">{{ group.name }}</span>
                      <span class="shrink-0 text-slate-500">{{ formatNumber(group.total) }}</span>
                    </div>
                    <div class="mt-1 h-2 w-full overflow-hidden rounded-full bg-slate-100">
                      <div
                        class="h-full rounded-full bg-primary/70"
                        :style="{ width: `${group.percent}%` }"
                      ></div>
                    </div>
                    <p class="mt-1 text-[11px] text-slate-400">
                      {{ t('tokens.breakdown.requests', { count: group.requests }) }}
                    </p>
                  </li>
                </ul>
              </div>
              <div>
                <p class="mb-3 text-xs font-black uppercase tracking-widest text-slate-400">
                  {{ t('tokens.breakdown.sources') }}
                </p>
                <ul class="space-y-3">
                  <li v-for="group in sourceGroups" :key="group.name">
                    <div class="flex items-center justify-between gap-3 text-sm">
                      <span class="truncate font-bold text-slate-700">
                        {{ sourceLabel(group.name) }}
                      </span>
                      <span class="shrink-0 text-slate-500">{{ formatNumber(group.total) }}</span>
                    </div>
                    <div class="mt-1 h-2 w-full overflow-hidden rounded-full bg-slate-100">
                      <div
                        class="h-full rounded-full bg-emerald-500/70"
                        :style="{ width: `${group.percent}%` }"
                      ></div>
                    </div>
                    <p class="mt-1 text-[11px] text-slate-400">
                      {{ t('tokens.breakdown.requests', { count: group.requests }) }}
                    </p>
                  </li>
                </ul>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card class="app-surface border-none">
        <CardHeader
          class="flex flex-col gap-4 border-b border-slate-100/60 pb-5 md:flex-row md:items-start md:justify-between"
        >
          <div class="space-y-2">
            <CardTitle class="text-lg font-bold text-slate-800">
              {{ t('tokens.daily.title', { days }) }}
            </CardTitle>
            <p class="text-sm text-slate-500">{{ t('tokens.daily.description') }}</p>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <Badge variant="secondary" class="w-fit bg-primary/10 text-primary">
              <Database class="h-3 w-3" />
              {{ t('tokens.daily.allTime', { count: formatNumber(allTime?.total_tokens || 0) }) }}
            </Badge>
            <Button
              v-for="option in RANGE_OPTIONS"
              :key="option"
              :variant="option === days ? 'default' : 'outline'"
              size="sm"
              @click="setDays(option)"
            >
              {{ t(`tokens.range.days${option}`) }}
            </Button>
          </div>
        </CardHeader>
        <CardContent class="p-6">
          <TokenBarChart
            :points="dailyPoints"
            :hint="t('tokens.daily.hint', { days })"
            :height="260"
          />
        </CardContent>
      </Card>

      <Card class="app-surface border-none">
        <CardHeader class="border-b border-slate-100/60 pb-5">
          <CardTitle class="text-lg font-bold text-slate-800">
            {{ t('tokens.recent.title') }}
          </CardTitle>
          <p class="text-sm text-slate-500">
            {{ t('tokens.recent.description', { count: recent.length }) }}
          </p>
        </CardHeader>
        <CardContent class="p-0">
          <div v-if="!recent.length" class="px-6 py-10 text-center text-sm text-slate-500">
            {{ t('tokens.recent.empty') }}
          </div>
          <Table v-else>
            <TableHeader>
              <TableRow>
                <TableHead>{{ t('tokens.recent.time') }}</TableHead>
                <TableHead>{{ t('tokens.recent.source') }}</TableHead>
                <TableHead>{{ t('tokens.recent.model') }}</TableHead>
                <TableHead>{{ t('tokens.recent.task') }}</TableHead>
                <TableHead class="text-right">{{ t('tokens.recent.input') }}</TableHead>
                <TableHead class="text-right">{{ t('tokens.recent.output') }}</TableHead>
                <TableHead class="text-right">{{ t('tokens.recent.total') }}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow
                v-for="(record, index) in recent"
                :key="`${record.recorded_at}-${index}`"
              >
                <TableCell class="whitespace-nowrap text-slate-600">
                  {{ formatRecordTime(record.recorded_at) }}
                </TableCell>
                <TableCell>
                  <Badge variant="outline">{{ sourceLabel(record.source) }}</Badge>
                </TableCell>
                <TableCell class="text-slate-600">
                  {{ record.model || t('tokens.recent.unknown') }}
                </TableCell>
                <TableCell class="text-slate-600">
                  {{ record.task_name || t('tokens.recent.unknown') }}
                </TableCell>
                <TableCell class="text-right text-slate-600">
                  {{ formatNumber(record.prompt_tokens) }}
                </TableCell>
                <TableCell class="text-right text-slate-600">
                  {{ formatNumber(record.completion_tokens) }}
                </TableCell>
                <TableCell class="text-right font-bold text-slate-800">
                  {{ formatNumber(record.total_tokens) }}
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </template>
  </div>
</template>