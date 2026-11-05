import { onBeforeUnmount, ref } from 'vue'
import * as tokensApi from '@/api/tokens'
import type { TokenUsageSnapshot } from '@/types/token.d.ts'

// 当日用量会随爬虫进程持续增长，页面停留时定期刷新。
const AUTO_REFRESH_INTERVAL_MS = 30000

export function useTokenUsage(initialDays = 14) {
  const snapshot = ref<TokenUsageSnapshot | null>(null)
  const days = ref(initialDays)
  const isLoading = ref(false)
  const error = ref<Error | null>(null)
  const lastUpdatedAt = ref<Date | null>(null)

  async function fetchSummary() {
    isLoading.value = true
    error.value = null
    try {
      snapshot.value = await tokensApi.getTokenUsageSummary(days.value)
      lastUpdatedAt.value = new Date()
    } catch (e) {
      error.value = e instanceof Error ? e : new Error(String(e))
    } finally {
      isLoading.value = false
    }
  }

  async function setDays(nextDays: number) {
    if (days.value === nextDays) return
    days.value = nextDays
    await fetchSummary()
  }

  const refreshTimer = window.setInterval(fetchSummary, AUTO_REFRESH_INTERVAL_MS)
  onBeforeUnmount(() => window.clearInterval(refreshTimer))

  fetchSummary()

  return {
    snapshot,
    days,
    isLoading,
    error,
    lastUpdatedAt,
    fetchSummary,
    setDays,
  }
}