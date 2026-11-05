import type {
  Task,
  TaskCreatePayload,
  TaskCreateResponse,
  TaskGenerateRequest,
  TaskGenerationJob,
  TaskUpdate,
} from '@/types/task.d.ts'
import { http } from '@/lib/http'

export async function getAllTasks(platform?: 'xianyu' | 'kongfz'): Promise<Task[]> {
  return await http('/api/tasks', platform ? { params: { platform } } : {})
}

export async function createTask(data: TaskCreatePayload): Promise<Task> {
  const result = await http('/api/tasks/', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(data),
  })
  return result.task
}

export async function createTaskWithAI(data: TaskGenerateRequest): Promise<TaskCreateResponse> {
  return await http('/api/tasks/generate', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(data),
  })
}

export async function getTaskGenerationJob(jobId: string): Promise<TaskGenerationJob> {
  const result = await http(`/api/tasks/generate-jobs/${jobId}`)
  return result.job
}

export async function updateTask(taskId: number, data: TaskUpdate): Promise<Task> {
  const result = await http(`/api/tasks/${taskId}`, {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(data),
  })
  return result.task
}

export async function startTask(taskId: number): Promise<void> {
  await http(`/api/tasks/start/${taskId}`, { method: 'POST' })
}

export interface StopTaskResult {
  message: string
  stopped_process: boolean
  disabled: boolean
  enabled: boolean
}

/**
 * 停止任务。
 * `disable: true` 表示同时关闭定时（从调度器移除），即真正的「停止监控」；
 * 否则只终止当前这一轮运行，定时任务会在下一轮继续触发。
 */
export async function stopTask(
  taskId: number,
  options?: { disable?: boolean },
): Promise<StopTaskResult> {
  return await http(`/api/tasks/stop/${taskId}`, {
    method: 'POST',
    params: options?.disable ? { disable: true } : undefined,
  })
}

export async function deleteTask(taskId: number): Promise<void> {
  await http(`/api/tasks/${taskId}`, { method: 'DELETE' })
}
