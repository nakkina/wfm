import { errorDetail } from './hierarchy.ts'

export type DailyPoint = {
  date: string
  calls_offered: number
  calls_handled: number
  handle_seconds: number
  aht_seconds: number | null // null when no calls were handled that day
}

export type QueueHistory = { queue_id: string; start: string; end: string; days: DailyPoint[] }

export async function fetchQueueHistory(queueId: string): Promise<QueueHistory> {
  const response = await fetch(`/api/queues/${encodeURIComponent(queueId)}/history`)
  if (!response.ok) throw new Error(await errorDetail(response, 'Failed to load ACD history'))
  return (await response.json()) as QueueHistory
}
