import { errorDetail } from './hierarchy.ts'

/** One row of the workbook's Dictionary sheet. */
export type ColumnInfo = {
  field: string
  category: string
  type: string
  poc_use: string
  description: string
}

export type AgentRecord = Record<string, string | number | boolean | null>

export async function fetchAgentColumns(): Promise<ColumnInfo[]> {
  const response = await fetch('/api/agents/columns')
  if (!response.ok) throw new Error(await errorDetail(response, 'Failed to load agent columns'))
  return (await response.json()) as ColumnInfo[]
}

export async function fetchQueueAgents(queueId: string): Promise<AgentRecord[]> {
  const response = await fetch(`/api/queues/${encodeURIComponent(queueId)}/agents`)
  if (!response.ok) throw new Error(await errorDetail(response, 'Failed to load agents'))
  return (await response.json()) as AgentRecord[]
}
