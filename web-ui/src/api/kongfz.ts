import { http } from '@/lib/http'
import type { Task } from '@/types/task.d.ts'

export interface KongfzAccount {
  name: string
  path: string
  exists: boolean
  size: number
  updated_at: string | null
  cookie_count: number
  valid: boolean
  error: string
}

export interface KongfzAccountListResponse {
  accounts: KongfzAccount[]
  state_dir: string
  default_path: string
}

export interface KongfzLoginStartResponse {
  message: string
  job_id: string
  state_file: string
  pid: number
}

export interface KongfzLoginStatusResponse {
  job_id: string
  status: 'running' | 'done' | 'failed'
  returncode: number | null
  state_file: string
  output: string
}

export interface KongfzVerifyResponse {
  ok: boolean
  message: string
  path: string
}

export interface KongfzMonitor {
  id: number
  task_name: string
  keyword: string
  enabled: boolean
  cron: string | null
  latest_count: number
  notify_all_new: boolean
  notify_removal: boolean
  window_size: number
  new_count: number
  backfill_count: number
  /** 最近一次真实运行时间（每轮都会更新，与是否发现新商品无关） */
  last_run_at: string | null
  last_run_status: 'ok' | 'failed' | 'skipped' | null
  last_run_message: string | null
  last_run_new_count: number
  last_run_backfill_count: number
  last_run_removed_count: number
  /** 下一次计划运行时间 */
  next_run_at: string | null
  /** 兼容字段：窗口内最新商品的入库时间 */
  latest_run_at: string | null
  latest_crawl_time: string | null
  account_state_file: string | null
  account: KongfzAccount | null
}

export interface KongfzProductInfo {
  商品ID: string
  商品标题: string
  当前售价: string
  商品链接: string
  商品图片列表: string[]
  商品主图链接: string
  商品标签: string[]
  发货地区: string
  卖家昵称: string
  发布时间: string
  品相?: string
  作者?: string
  出版社?: string
}

export interface KongfzResultItem {
  [key: string]: unknown
  商品信息?: KongfzProductInfo
  _status?: string
  window_rank?: number | null
  /** 首次被发现的时间（即入库时间） */
  first_seen_at?: string
  /** 是否属于最近一次抓取进入窗口的商品（含递补） */
  is_new?: boolean
  /** 真·新上架（会触发通知） */
  is_new_listing?: boolean
  /** 因在售商品下架而递补进入窗口（不通知） */
  is_backfill?: boolean
}

export interface KongfzItemsResponse {
  total_items: number
  page: number
  limit: number
  keyword: string
  filename: string
  /** 窗口总条数（= 监控最新 N 条） */
  window_size?: number
  /** 最近一次抓取时间 */
  latest_run_at?: string | null
  /** 最近一次抓取「真·新上架」的条数（会通知的） */
  new_count?: number
  /** 最近一次抓取「因下架递补」的条数（不通知） */
  backfill_count?: number
  items: KongfzResultItem[]
}

export interface KongfzPreviewResponse {
  keyword: string
  scene: string
  from_state_file: string | null
  total_found: number
  items: KongfzProductInfo[]
  playwright_used?: boolean
}

export interface KongfzConfig {
  state_dir: string
  default_state_path: string
  request_interval: string
  list_scene: string
  login_entry: string
  accounts: KongfzAccount[]
  playwright_enabled: boolean
  playwright_headless: boolean
}

export async function getAccounts(): Promise<KongfzAccountListResponse> {
  return await http('/api/kongfz/accounts')
}

export async function getKongfzConfig(): Promise<KongfzConfig> {
  return await http('/api/kongfz/config')
}

export async function updatePlaywrightToggle(enabled: boolean): Promise<{
  message: string
  playwright_enabled: boolean
}> {
  return await http('/api/kongfz/config/playwright', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
}

export async function startLogin(payload: {
  name?: string
  state_file?: string
  timeout?: number
}): Promise<KongfzLoginStartResponse> {
  return await http('/api/kongfz/accounts/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function getLoginStatus(jobId: string): Promise<KongfzLoginStatusResponse> {
  return await http(`/api/kongfz/accounts/login/${jobId}`)
}

export async function verifyAccount(path?: string): Promise<KongfzVerifyResponse> {
  return await http('/api/kongfz/accounts/verify', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  })
}

export async function importAccount(payload: {
  content: string
  name?: string
}): Promise<{ message: string; path: string; cookie_count: number }> {
  return await http('/api/kongfz/accounts/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function deleteAccount(name: string): Promise<void> {
  await http(`/api/kongfz/accounts/${encodeURIComponent(name)}`, { method: 'DELETE' })
}

export async function getMonitors(): Promise<{ monitors: KongfzMonitor[]; total: number }> {
  return await http('/api/kongfz/monitors')
}

export async function getItems(params: {
  task_id?: number
  keyword?: string
  page?: number
  limit?: number
}): Promise<KongfzItemsResponse> {
  return await http('/api/kongfz/items', { params })
}

export async function previewLatest(keyword: string, limit: number): Promise<KongfzPreviewResponse> {
  return await http('/api/kongfz/preview', { params: { keyword, limit } })
}

export async function getKongfzTasks(): Promise<Task[]> {
  return await http('/api/tasks', { params: { platform: 'kongfz' } })
}