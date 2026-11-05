import { http } from '@/lib/http'
import type { TokenUsageSnapshot } from '@/types/token.d.ts'

export async function getTokenUsageSummary(days = 14): Promise<TokenUsageSnapshot> {
  return await http('/api/tokens/summary', { params: { days } })
}