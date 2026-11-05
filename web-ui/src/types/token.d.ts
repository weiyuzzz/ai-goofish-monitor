export interface TokenUsageTotals {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  requests: number
}

export interface TokenUsageDay extends TokenUsageTotals {
  day: string
  label?: string
}

export interface TokenUsageHour extends TokenUsageTotals {
  hour: number
  label: string
  day: string
}

export interface TokenUsageGroup extends TokenUsageTotals {
  name: string
}

export interface TokenUsageRecord extends TokenUsageTotals {
  recorded_at: string
  day: string
  model: string | null
  source: string
  task_name: string | null
}

export interface TokenUsageSnapshot {
  generated_at: string
  today: TokenUsageDay
  hourly: TokenUsageHour[]
  daily: TokenUsageDay[]
  trend_days: number
  models: TokenUsageGroup[]
  sources: TokenUsageGroup[]
  all_time: TokenUsageTotals
  recent: TokenUsageRecord[]
}