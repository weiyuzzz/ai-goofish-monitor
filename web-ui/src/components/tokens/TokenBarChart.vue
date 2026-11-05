<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { formatNumber } from '@/i18n'

export interface TokenBarPoint {
  label: string
  prompt: number
  completion: number
}

const props = withDefaults(defineProps<{
  points: TokenBarPoint[]
  height?: number
  maxLabels?: number
  hint?: string
}>(), {
  height: 240,
  maxLabels: 12,
  hint: '',
})

const { t } = useI18n()

const chartWidth = 720
const padding = { top: 16, right: 16, bottom: 32, left: 58 }
const plotWidth = chartWidth - padding.left - padding.right

const totals = computed(() => props.points.map((point) => point.prompt + point.completion))
const hasData = computed(() => totals.value.some((value) => value > 0))
const niceMax = computed(() => niceCeil(Math.max(0, ...totals.value)))

const plotHeight = computed(() => Math.max(80, props.height - padding.top - padding.bottom))
const baseline = computed(() => padding.top + plotHeight.value)

const bandWidth = computed(() => (props.points.length ? plotWidth / props.points.length : plotWidth))
const barWidth = computed(() => Math.min(30, Math.max(5, bandWidth.value * 0.6)))
const labelStep = computed(() =>
  Math.max(1, Math.ceil(props.points.length / Math.max(1, props.maxLabels))),
)

const gridLines = computed(() =>
  Array.from({ length: 5 }, (_, index) => {
    const value = (niceMax.value / 4) * index
    return { value, y: resolveY(value) }
  }),
)

function resolveX(index: number) {
  return padding.left + bandWidth.value * (index + 0.5)
}

function resolveY(value: number) {
  const ratio = niceMax.value > 0 ? value / niceMax.value : 0
  return baseline.value - ratio * plotHeight.value
}

function barY(point: TokenBarPoint) {
  return resolveY(point.prompt + point.completion)
}

function promptBarHeight(point: TokenBarPoint) {
  if (point.prompt <= 0 || niceMax.value <= 0) return 0
  return (point.prompt / niceMax.value) * plotHeight.value
}

function completionBarHeight(point: TokenBarPoint) {
  if (point.completion <= 0 || niceMax.value <= 0) return 0
  return (point.completion / niceMax.value) * plotHeight.value
}

function niceCeil(value: number) {
  if (!Number.isFinite(value) || value <= 0) return 1
  const magnitude = 10 ** Math.floor(Math.log10(value))
  const normalized = value / magnitude
  const step = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 2.5 ? 2.5 : normalized <= 5 ? 5 : 10
  return step * magnitude
}

function formatCompact(value: number) {
  if (value >= 1_000_000) return `${round(value / 1_000_000)}M`
  if (value >= 1_000) return `${round(value / 1_000)}k`
  // 没有任何用量时坐标轴最大值只有 1，整数刻度会连续出现相同的 "0 / 1"。
  if (value > 0 && value < 10 && !Number.isInteger(value)) return value.toFixed(1)
  return String(Math.round(value))
}

function round(value: number) {
  return Number.isInteger(value) ? String(value) : value.toFixed(1)
}

function tooltip(point: TokenBarPoint) {
  return [
    point.label,
    `${t('tokens.chart.input')}: ${formatNumber(point.prompt)}`,
    `${t('tokens.chart.output')}: ${formatNumber(point.completion)}`,
    `${t('tokens.chart.total')}: ${formatNumber(point.prompt + point.completion)}`,
  ].join(' · ')
}
</script>

<template>
  <div class="app-surface-subtle p-4">
    <div class="mb-3 flex flex-col gap-3 text-xs uppercase tracking-[0.22em] text-slate-500 sm:flex-row sm:items-center sm:justify-between">
      <span>{{ hint }}</span>
      <div class="flex items-center gap-3">
        <span class="inline-flex items-center gap-1">
          <span class="h-2.5 w-2.5 rounded-full bg-sky-600" />
          {{ t('tokens.chart.input') }}
        </span>
        <span class="inline-flex items-center gap-1">
          <span class="h-2.5 w-2.5 rounded-full bg-amber-500" />
          {{ t('tokens.chart.output') }}
        </span>
      </div>
    </div>

    <div v-if="points.length === 0" class="rounded-2xl border border-dashed border-slate-200 bg-white/70 px-4 py-10 text-center text-sm text-slate-500">
      {{ t('tokens.chart.noData') }}
    </div>

    <svg
      v-else
      :viewBox="`0 0 ${chartWidth} ${height}`"
      :style="{ height: `${height}px` }"
      class="w-full"
      role="img"
      :aria-label="hint || t('tokens.chart.noData')"
    >
      <g>
        <line
          v-for="line in gridLines"
          :key="line.value"
          :x1="padding.left"
          :x2="chartWidth - padding.right"
          :y1="line.y"
          :y2="line.y"
          :stroke="line.value === 0 ? '#cbd5e1' : '#e2e8f0'"
          :stroke-dasharray="line.value === 0 ? undefined : '4 6'"
        />
        <text
          v-for="line in gridLines"
          :key="`label-${line.value}`"
          :x="padding.left - 8"
          :y="line.y + 4"
          text-anchor="end"
          fill="#94a3b8"
          font-size="11"
        >
          {{ formatCompact(line.value) }}
        </text>
      </g>

      <g v-for="(point, index) in points" :key="`${point.label}-${index}`">
        <title>{{ tooltip(point) }}</title>
        <rect
          v-if="point.prompt > 0"
          :x="resolveX(index) - barWidth / 2"
          :y="resolveY(point.prompt)"
          :width="barWidth"
          :height="promptBarHeight(point)"
          fill="#0284c7"
          rx="3"
        />
        <rect
          v-if="point.completion > 0"
          :x="resolveX(index) - barWidth / 2"
          :y="barY(point)"
          :width="barWidth"
          :height="completionBarHeight(point)"
          fill="#f59e0b"
          rx="3"
        />
        <text
          v-if="index % labelStep === 0"
          :x="resolveX(index)"
          :y="height - 10"
          text-anchor="middle"
          fill="#64748b"
          font-size="11"
        >
          {{ point.label }}
        </text>
      </g>

      <text
        v-if="!hasData"
        :x="chartWidth / 2"
        :y="padding.top + plotHeight / 2"
        text-anchor="middle"
        fill="#94a3b8"
        font-size="13"
      >
        {{ t('tokens.chart.noData') }}
      </text>
    </svg>
  </div>
</template>